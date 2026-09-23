"""Бенчмарк перевода: одна модель, один нашпигованный ловушками текст, судья с
ключом ответов и балл, который считает код.

    booktrans --bench --to ru --translator claude:claude-sonnet-5:high

Меряется только переводчик: без разведки (её решения об именах и роде были
бы подсказками) и без редактора. Судья не оценивает перевод «на глаз», а
отвечает да/нет по контрольным точкам ключа — где какая ловушка посажена и
что считается верным. Балл области = сумма очков пройденных точек; сверх
ключа штрафуются отсебятина, непереведённое и (кодом) порча структуры и
чужая письменность. Итог нормируется к 100.

Текст и ключ лежат в пакете сжатыми: репозиторий публичный, и открытый ключ
ответов рано или поздно попал бы в обучающую выборку той модели, которую
им же меряют. Исходники набора — у владельца, вне репозитория.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import time
import zlib

from . import agent as agent_mod, lang, pipeline
from .build import release_version
from .models import Models, family, parse_chain
from .run import Run, locked

HERE = os.path.dirname(os.path.abspath(__file__))
SETS = os.path.join(HERE, "bench")
TEXT, KEY = "text.fb2", "key.txt"
JUDGE_DEFAULT = "claude:claude-opus-5-5:medium,codex:gpt-5.6-sol:medium"
# Греческие буквы законны в любом переводе: «γ Кассиопеи» — не чужой язык.
ALWAYS_SCRIPT = r"Ͱ-Ͽ"

T = lang.T


# ------------------------------------------------------------------ набор

def pack(src_dir, out):
    """Собрать набор из папки с text.fb2 и key.txt в один сжатый файл."""
    data = {n: open(os.path.join(src_dir, n), encoding="utf-8").read()
            for n in (TEXT, KEY)}
    parse_key(data[KEY])                    # битый ключ не упаковываем
    blob = zlib.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"), 9)
    with open(out, "wb") as f:
        f.write(blob)
    return len(blob)


def load_set(spec):
    """Набор по имени: `en` (или пусто) — из пакета; путь к папке с text.fb2
    и key.txt — распакованный набор, свой или сгенерированный."""
    spec = spec or "en"
    if os.path.isdir(spec):
        data = {n: open(os.path.join(spec, n), encoding="utf-8").read()
                for n in (TEXT, KEY)}
        name = os.path.basename(os.path.normpath(spec))
    else:
        path = spec if os.path.isfile(spec) else os.path.join(SETS, spec + ".bin")
        if not os.path.exists(path):
            have = sorted(os.path.splitext(f)[0] for f in os.listdir(SETS)
                          if f.endswith(".bin"))
            raise SystemExit(T("bench_no_set", spec, ", ".join(have)))
        data = json.loads(zlib.decompress(open(path, "rb").read()).decode("utf-8"))
        name = os.path.splitext(os.path.basename(path))[0]
    key = parse_key(data[KEY])
    return {"name": name, "text": data[TEXT], "key": key}


# ------------------------------------------------------------------- ключ

CHECK_LINE = re.compile(r"^(\w+)\s+(\w+)\s+(\d+)\s+(\S+)\s+::\s+(.+)$")


def _blocks_of(s):
    """`b0008,b0010-b0012` → ['b0008', 'b0010', 'b0011', 'b0012']."""
    out = []
    for tok in s.split(","):
        m = re.fullmatch(r"b(\d+)-b(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += [f"b{i:04d}" for i in range(a, b + 1)]
        elif re.fullmatch(r"b\d+", tok):
            out.append(tok)
        else:
            raise ValueError(f"bad block ref {tok!r}")
    return out


def parse_key(text):
    """Ключ судьи: области, штрафы, контрольные точки. Суммы очков точек
    по областям обязаны сходиться с объявленными максимумами: ключ пишется
    руками, и расхождение здесь — ошибка ключа, а не судьи."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("booktrans-bench-key "):
        raise ValueError("not a booktrans bench key")
    key = {"version": lines[0].split()[1], "source": "", "title": "",
           "areas": [], "penalties": {}, "checks": []}
    sec = None
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            continue
        if sec is None:
            k, _, v = line.partition(" ")
            if k in ("source", "title"):
                key[k] = v.strip()
            continue
        if sec == "areas":
            code, pts, name = line.split(None, 2)
            key["areas"].append((code, int(pts), name))
        elif sec == "penalties":
            code, per, cap = line.split()
            key["penalties"][code] = (int(per), int(cap))
        elif sec == "checks":
            m = CHECK_LINE.match(line)
            if not m:
                raise ValueError(f"bad check line: {line[:40]}")
            cid, area, pts, blocks, want = m.groups()
            key["checks"].append({"id": cid, "area": area, "points": int(pts),
                                  "blocks": _blocks_of(blocks), "text": want})
    areas = {c: p for c, p, _ in key["areas"]}
    ids = [c["id"] for c in key["checks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate check ids")
    got = {}
    for c in key["checks"]:
        if c["area"] not in areas:
            raise ValueError(f"check {c['id']}: unknown area {c['area']}")
        got[c["area"]] = got.get(c["area"], 0) + c["points"]
    for code, pts in areas.items():
        if got.get(code, 0) != pts:
            raise ValueError(f"area {code}: checks sum to {got.get(code, 0)}, "
                             f"declared {pts}")
    key["max"] = sum(areas.values())
    if not key["max"] or not key["source"]:
        raise ValueError("empty key")
    return key


# -------------------------------------------------------- проверки кодом

def foreign(text, scripts):
    """Буквы письменности, которая не исходная и не целевая. Слова без
    перевода — латиница в русском — дело судьи; иероглиф посреди русской
    фразы — дефект без вопросов, и код видит его надёжнее судьи."""
    allowed = "".join(lang.SCRIPTS[s] for s in scripts if s in lang.SCRIPTS)
    ok = re.compile(f"[{allowed}{ALWAYS_SCRIPT}]")
    return [ch for ch in text if ch.isalpha() and not ok.match(ch)]


TAGS = re.compile(r"<(/?)([a-z]+)[^>]*>")


def code_checks(blocks, tr, source, to):
    """Штрафы, которые считает код: STRUCT — блок без перевода, перевод без
    блока, разошедшиеся теги разметки; SCRIPT — блоки с чужой письменностью.
    Возвращает [(код штрафа, блок, пояснение)]."""
    out = []
    want = {b["id"]: b for b in blocks if b["kind"] in ("p", "verse")}
    for bid, b in want.items():
        t = tr.get(bid)
        if not t or not t.strip():
            out.append(("STRUCT", bid, "missing"))
            continue
        src_tags = sorted(n for s, n in TAGS.findall(b["text"]) if not s)
        tr_tags = sorted(n for s, n in TAGS.findall(t) if not s)
        if src_tags != tr_tags:
            out.append(("STRUCT", bid, f"tags {src_tags} -> {tr_tags}"))
    for bid in tr:
        if bid not in want:
            out.append(("STRUCT", bid, "extra"))
    scripts = [lang.script_of(source), lang.script_of(to)]
    for bid, t in tr.items():
        bad = foreign(t, scripts)
        if bad:
            out.append(("SCRIPT", bid, "".join(bad[:8])))
    return out


# --------------------------------------------------------------- судья

def judge_prompt(key, blocks, tr, footnotes):
    """Пользовательская часть запроса судье: ключ, потом текст парами."""
    lines = [T("bench_p_key")]
    for c in key["checks"]:
        lines.append(f"{c['id']} [{c['area']} {c['points']}] "
                     f"{','.join(c['blocks'])} :: {c['text']}")
    lines += ["", T("bench_p_text")]
    notes = {}
    for n in footnotes or []:
        notes.setdefault(n.get("block"), []).append(
            f"{n.get('term', '')} — {n.get('text', '')}".strip(" —"))
    for b in blocks:
        if b["kind"] not in ("p", "verse"):
            continue
        lines += ["", f"<<<{b['id']}>>>", f"SRC: {b['text']}",
                  f"TRG: {tr.get(b['id'], '')}"]
        for n in notes.get(b["id"], []):
            lines.append(f"NOTE: {n}")
    return "\n".join(lines)


VERDICT = re.compile(r"\[\[\[CHECK\s+(\w+)\s+(ok|fail)\]\]\]\s*(.*)", re.I)
PENALTY = re.compile(r"\[\[\[(ADD|UNTR)\s+(s\d+\.b\d+)(?:\s+(minor|major))?\]\]\]\s*(.*)")
REMARK = re.compile(r"\[\[\[NOTE\s+(s\d+\.b\d+)\]\]\]\s*(.*)")


def parse_verdict(out, key):
    """Ответ судьи → {точка: (пройдена, причина)}, штрафы, замечания.
    Точка без вердикта — не «пропуск», а незаконченный ответ: перечисляется
    в ошибке, и повтор запроса просит именно её."""
    verdicts, pens, remarks = {}, [], []
    for line in out.splitlines():
        m = VERDICT.search(line)
        if m:
            verdicts[m.group(1).upper()] = (m.group(2).lower() == "ok",
                                            m.group(3).strip())
            continue
        m = PENALTY.search(line)
        if m:
            kind, bid, grade, quote = m.groups()
            code = f"ADD-{grade or 'minor'}" if kind == "ADD" else "UNTR"
            pens.append((code, bid, quote.strip()))
            continue
        m = REMARK.search(line)
        if m:
            remarks.append((m.group(1), m.group(2).strip()))
    ids = [c["id"] for c in key["checks"]]
    missing = [i for i in ids if i not in verdicts]
    if missing:
        raise ValueError(T("bench_missing", len(missing), ", ".join(missing[:12])))
    return {i: verdicts[i] for i in ids}, pens, remarks


def score(key, verdicts, pens):
    """Очки по областям, штрафы с потолками, сырой и нормированный итог.
    Потолок общий для градаций одного штрафа (ADD-minor и ADD-major)."""
    areas = {code: 0 for code, _, _ in key["areas"]}
    for c in key["checks"]:
        if verdicts.get(c["id"], (False,))[0]:
            areas[c["area"]] += c["points"]
    groups = {}
    for code, bid, note in pens:
        per, cap = key["penalties"].get(code, (0, 0))
        g = groups.setdefault(code.split("-")[0], {"n": 0, "raw": 0, "cap": 0})
        g["n"] += 1
        g["raw"] += per
        g["cap"] = max(g["cap"], cap)
    penalty = {g: min(v["raw"], v["cap"]) for g, v in groups.items()}
    raw = sum(areas.values()) - sum(penalty.values())
    # Один знак после запятой: при максимуме ключа не в сто очков доля не
    # целая, а при ста — читается тем же числом.
    norm = round(100 * max(0, raw) / key["max"], 1)
    return {"areas": areas, "penalty": penalty, "counts": {g: v["n"] for g, v in groups.items()},
            "raw": raw, "score": norm, "max": key["max"]}


# --------------------------------------------------------------- отчёт

def _who(a):
    """Провайдер, модель и усилие агента — три поля отчёта."""
    return {"provider": getattr(a, "kind", "?"), "model": getattr(a, "model", None) or "?",
            "effort": getattr(a, "effort", None) or ""}


def _spec(w):
    return ":".join(x for x in (w["provider"], w["model"], w["effort"]) if x)


def report_md(r, ui):
    """Отчёт: первая строка — нормированный балл, дальше подробности."""
    T = lang.set_ui(ui)
    key = r["key"]

    def area_name(code, name):
        # Название области на языке интерфейса; нет перевода — из ключа.
        label = T(f"bench_area_{code}")
        return name if label == f"bench_area_{code}" else label

    names = {code: area_name(code, name) for code, _, name in key["areas"]}
    runs = r.get("runs") or [r]
    med = r.get("median", r["score"]["score"])
    lines = [f"# {med:.1f} / 100", "",
             T("bench_r_head", r["set"], key["version"], r["booktrans"]),
             T("bench_r_date", r["date"]),
             T("bench_r_translator", _spec(r["translator"])),
             T("bench_r_judge", _spec(r["judge"])),
             T("bench_r_lang", key["source"], r["to"]),
             T("bench_r_raw", r["score"]["raw"], sum(r["score"]["penalty"].values()),
               r["score"]["max"])]
    if len(runs) > 1:
        lines += ["", "## " + T("bench_r_runs", len(runs)), "",
                  "| # | " + T("bench_r_col_points") + " | raw | penalty | fail | attempts | $ | min |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, x in enumerate(runs, 1):
            sc = x["score"]
            lines.append(f"| {i} | {sc['score']:.1f} | {sc['raw']} | {-sum(sc['penalty'].values())} | "
                         f"{sum(1 for v in x['verdicts'].values() if not v[0])} | {x.get('attempts', 1)} | "
                         f"{_money(x['cost']['translate'])} / {_money(x['cost']['judge'])} | "
                         f"{_mins(x['time']['translate'])} / {_mins(x['time']['judge'])} |")
        lines.append("")
        lines.append(T("bench_r_median", f"{med:.1f}", f"{min(sc for sc in r['scores']):.1f}",
                       f"{max(sc for sc in r['scores']):.1f}", os.path.basename(r["work"])))
    lines += ["", "## " + T("bench_r_areas"), "",
             "| " + T("bench_r_col_area") + " | " + T("bench_r_col_points") + " |",
             "|---|---|"]
    for code, mx, _ in key["areas"]:
        lines.append(f"| {names[code]} | {r['score']['areas'][code]} / {mx} |")
    failed = [(c, r["verdicts"][c["id"]][1]) for c in key["checks"]
              if not r["verdicts"][c["id"]][0]]
    lines += ["", "## " + T("bench_r_failed", len(failed), len(key["checks"])), ""]
    for c, why in failed:
        lines.append(f"- **{c['id']}** ({names.get(c['area'], c['area'])}, "
                     f"{c['points']}; {','.join(c['blocks'][:4])}) — {why}")
    lines += ["", "## " + T("bench_r_penalties"), ""]
    if not r["penalties"]:
        lines.append(T("bench_r_none"))
    for code, bid, note in r["penalties"]:
        lines.append(f"- {code} {bid}: {note}")
    if r["remarks"]:
        lines += ["", "## " + T("bench_r_remarks"), ""]
        lines += [f"- {bid}: {note}" for bid, note in r["remarks"]]
    lines += ["", "## " + T("bench_r_run"), "",
              T("bench_r_cost", _money(r["cost"]["translate"]), _mins(r["time"]["translate"]),
                _money(r["cost"]["judge"]), _mins(r["time"]["judge"]))]
    for role in ("translate", "judge"):
        tok = (r.get("tokens") or {}).get(role)
        if tok:
            lines.append(T(f"bench_r_tokens_{role}", tok.get("in", 0), tok.get("cached", 0),
                           tok.get("out", 0), tok.get("reasoning", 0)))
    if r.get("attempts", 1) > 1:
        lines.append(T("bench_r_attempts", r["attempts"]))
    lines += [T("bench_r_footnotes", r["footnotes"]),
              T("bench_r_work", r["work"]), "",
              "## " + T("bench_r_row"), "", table_row(r)]
    return "\n".join(lines) + "\n"


def _money(x):
    return f"${x:.2f}" if isinstance(x, (int, float)) else "—"


def _mins(sec):
    return f"{sec / 60:.1f}"


def table_row(r):
    """Строка для docs/bench/results-<пара>.md: дата, переводчик, судья,
    версии, итог, области, штрафы."""
    s = r["score"]
    scores = r.get("scores") or [s["score"]]
    med = r.get("median", s["score"])
    spread = f"{len(scores)} ({min(scores):.0f}–{max(scores):.0f})" if len(scores) > 1 else "1"
    # Попытки по прогонам: «1/1/2» — третий перевод принят со второго захода.
    tries = "/".join(str(x.get("attempts", 1)) for x in (r.get("runs") or [r]))
    cells = [r["date"][:10], _spec(r["translator"]), f"{med:.1f}", spread, tries]
    cells += [str(s["areas"][code]) for code, _, _ in r["key"]["areas"]]
    cells += [str(-sum(s["penalty"].values())) if s["penalty"] else "0",
              r["booktrans"], r["key"]["version"], _spec(r["judge"])]
    return "| " + " | ".join(cells) + " |"


def table_head(key):
    cells = ["date", "translator", "score", "runs", "attempts"] + [code for code, _, _ in key["areas"]]
    cells += ["penalty", "booktrans", "test", "judge"]
    return "| " + " | ".join(cells) + " |\n|" + "---|" * len(cells)


# ------------------------------------------------------------------ прогон

def judges(args, models, translator):
    """Цепочка судей без семьи переводчика. Судья одной семьи — судья в
    собственном деле: к своим калькам модель слепа, и таблица лидеров с
    самопроверками ничего не стоит."""
    fam = family(getattr(translator, "model", None))
    chain = parse_chain(args.judge or JUDGE_DEFAULT, args.agent)
    out, dropped = [], []
    for name, m, eff in chain:
        (dropped if family(m) == fam else out).append(
            models._agent(name, m, eff or "high"))
    if not out:
        sys.exit(T("bench_judge_family", fam, args.judge or JUDGE_DEFAULT))
    return out, dropped


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _file_log(path):
    """Лог прогона — в файл: три перевода идут разом, и в общем логе их
    строки перемешались бы; в общий лог уходят только итоги."""
    f = open(path, "a", encoding="utf-8")

    def log(msg="", end="\n"):
        f.write(msg + end)
        f.flush()
    return log


def _one(args, models, bench, work, who, log, main):
    """Один прогон: перевод штатным проходом, суд, счёт. -> словарь итога."""
    key, T = bench["key"], lang.T
    os.makedirs(work, exist_ok=True)
    done = pipeline.lpath(work, "bench.json", args.to)
    if os.path.exists(done):
        # Прогон уже судился: повторный запуск в ту же папку досуживает
        # только то, что не доехало, а не платит за готовое второй раз.
        r = json.load(open(done, encoding="utf-8"))
        if r.get("key", {}).get("version") == key["version"]:
            main("  " + T("bench_run_kept", os.path.basename(work), f"{r['score']['score']:.1f}"))
            return dict(r, key=key)
    book = os.path.join(work, "bench.fb2")
    with open(book, "w", encoding="utf-8") as f:
        f.write(bench["text"])
    a = _args_for(args, book)
    t0 = time.time()
    with locked(work, sys.argv[1:]):
        pipeline.note_version(work)
        r = Run(a, work, log, models, ["translate"])
        r.book()
        r.user_prompt()
        r.merge_meta()
        r.measure()
        if len(r.chunks) != 1:
            sys.exit(T("bench_chunks", len(r.chunks)))
        if not r.step_translate():
            sys.exit(T("bench_unfinished"))
    t_tr = time.time() - t0
    files = pipeline.chunk_files(pipeline.lpath(work, "tr", args.to))
    if not files:
        sys.exit(T("bench_unfinished"))
    tr_json = json.load(open(files[-1][1], encoding="utf-8"))
    tr, footnotes = tr_json.get("tr", {}), tr_json.get("footnotes", [])
    blocks = r.chunks[0]["blocks"]
    ids = {b["id"] for b in blocks}
    missing = [c["id"] for c in key["checks"]
               for b in c["blocks"] if not any(i.endswith("." + b) for i in ids)]
    if missing:
        sys.exit(T("bench_key_blocks", ", ".join(sorted(set(missing))[:8])))
    translator = models.first("translator")
    # Имя модели в файле куска бывает пустым (haiku у claude): в лог и отчёт
    # тогда идёт имя из ключа --translator.
    if not tr_json.get("model"):
        tr_json["model"] = _who(translator)["model"]
    main("  " + T("bench_run_translated", os.path.basename(work), _mins(t_tr),
                  agent_mod.label(tr_json)))

    code_pen = code_checks(blocks, tr, key["source"], args.to)
    # Лишние заходы за ответом по форме — тоже изъян модели: кусок стоил
    # конвейеру вдвое-втрое дороже, чем у той, что отвечает с первого раза.
    code_pen += [("RETRY", "s01", f"attempt {n}")
                 for n in range(2, int(tr_json.get("attempts") or 1) + 1)]
    log("")
    log(f"=== {T('bench_judging', agent_mod.label(who[0]))} ===")
    system = "\n\n---\n\n".join(x for x in (
        lang.prompt("bench_judge")[0].format(
            to=lang.lang_name(args.to), ui=lang.lang_name(args.ui)),
        lang.prompt("units")[0],
        (lang.prompt("sys_rules")[0] + "\n\n" + lang.rules(args.to))
        if lang.rules(args.to) else "") if x)
    prompt = judge_prompt(key, blocks, tr, footnotes)
    t1 = time.time()
    (verdicts, pens, remarks), meta, _ = pipeline._chain_run(
        who, system, prompt, args.retries, lambda out: parse_verdict(out, key), log)
    t_j = time.time() - t1
    pens = pens + code_pen
    s = score(key, verdicts, pens)
    judge = _who(who[0])
    if meta.get("model"):
        judge = dict(judge, model=meta["model"], effort=meta.get("effort") or judge["effort"])
    result = {
        "score": s, "key": key, "set": bench["name"], "to": args.to,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "booktrans": release_version(),
        "translator": dict(_who(translator), model=tr_json.get("model") or _who(translator)["model"]),
        "judge": judge, "verdicts": verdicts, "penalties": pens, "remarks": remarks,
        "code_checks": code_pen, "footnotes": len(footnotes),
        "attempts": int(tr_json.get("attempts") or 1),
        "cost": {"translate": tr_json.get("cost_usd"), "judge": meta.get("cost_usd")},
        # Токены — когда поставщик их называет (claude, codex, openrouter);
        # agy молчит, и строки в отчёте тогда нет.
        "tokens": {"translate": tr_json.get("tokens"), "judge": meta.get("tokens")},
        "time": {"translate": t_tr, "judge": t_j}, "work": work,
    }
    out_json = pipeline.lpath(work, "bench.json", args.to)
    json.dump(_slim(result), open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    main("  " + T("bench_run_judged", os.path.basename(work), f"{s['score']:.1f}",
                  len(key["checks"]) - sum(1 for v in verdicts.values() if v[0]),
                  -sum(s["penalty"].values())))
    return result


def _args_for(args, book):
    """Ключи прогона перевода: та же командная строка, но книга — текст
    набора, проход — один, разведки нет."""
    a = argparse.Namespace(**vars(args))
    a.book, a.only, a.chunks, a.scout = book, "translate", None, None
    return a


def _slim(r):
    """Итог в json без полного ключа: он большой и лежит в пакете."""
    k = r["key"]
    return dict(r, key={"version": k["version"], "source": k["source"],
                        "areas": k["areas"], "max": k["max"]})


def run(args, log):
    """Весь бенчмарк: рабочая папка, N прогонов перевода и суда, медиана,
    отчёт. Прогоны идут разом, каждый пишет свой лог в своей папке."""
    T = lang.T
    bench = load_set(args.bench if args.bench != "-" else None)
    key = bench["key"]
    if args.to == key["source"]:
        sys.exit(T("bench_same_lang", key["source"]))
    if args.to not in lang.available_langs():
        sys.exit(f"нет правил для языка {args.to!r}; есть: "
                 f"{', '.join(lang.available_langs())}")
    models = Models(args, log)
    models.check()
    translator = models.first("translator")
    who, dropped = judges(args, models, translator)
    for d in dropped:
        log("  " + T("bench_judge_dropped", agent_mod.label(d)))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    # Испытуемый — в имени папки: рядом лежат прогоны разных моделей, и
    # различать их по дате неудобно. Косые, двоеточия и прочее, чего не
    # терпят Windows или Linux, — в дефисы; точка на конце Windows тоже не даёт.
    tag = re.sub(r"[^\w.-]+", "-", _spec(_who(translator))).strip("-.")
    base = f"benchmark-{key['source']}-{args.to}-{tag}-{stamp}"
    work = args.work or base + ".work"
    os.makedirs(work, exist_ok=True)
    n = max(1, int(args.bench_runs or 1))
    log("")
    log("  " + T("bench_start", bench["name"], key["version"],
                 agent_mod.label(translator), agent_mod.label(who[0]), n))
    subs = [os.path.join(work, f"run{i}") for i in range(1, n + 1)]
    results = [None] * n
    errors = []

    def job(i):
        try:
            results[i] = _one(args, models, bench, subs[i], who,
                              _file_log(os.path.join(subs[i], "bench.log")), log)
        except SystemExit as e:                   # прогон встал: остальные идут
            errors.append((subs[i], str(e)))
        except BaseException as e:                # noqa: BLE001
            errors.append((subs[i], repr(e)))
    with cf.ThreadPoolExecutor(n) as ex:
        for i in range(n):
            os.makedirs(subs[i], exist_ok=True)
            ex.submit(job, i)
    for sub, err in errors:
        log("  " + T("bench_run_failed", os.path.basename(sub), err.splitlines()[0][:200]))
    done = [r for r in results if r]
    if not done:
        sys.exit(T("bench_unfinished"))

    scores = [r["score"]["score"] for r in done]
    med = median(scores)
    # Разбивка по областям и провалы — у прогона, ближайшего к медиане.
    mid = min(done, key=lambda r: (abs(r["score"]["score"] - med), -r["score"]["score"]))
    final = dict(mid, runs=[_slim(r) for r in done], median=med,
                 scores=scores, work=work,
                 date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    md = report_md(final, args.ui)
    lang.set_ui(args.ui)
    out_json = os.path.join(work, "bench.json")
    json.dump(_slim(final), open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    report = os.path.join(os.path.dirname(os.path.abspath(work)) if args.work else ".",
                          os.path.splitext(os.path.basename(work))[0] + ".md")
    with open(report, "w", encoding="utf-8") as f:
        f.write(md)
    log("")
    for line in md.splitlines():
        log("  " + line)
    log("")
    log("  " + T("bench_saved", report, out_json))
    return 1 if errors else 0
