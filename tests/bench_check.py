#!/usr/bin/env python3
"""Бенчмарк перевода: набор в пакете цел, ключ сходится, счёт и разбор
вердикта судьи предсказуемы.

Ни строки текста набора здесь не печатается: он лежит в пакете сжатым, чтобы
не попасть в обучающие выборки, и тест не должен его разворачивать наружу.

    python3 tests/bench_check.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import bench as B, cli, extract as E, lang, pipeline as P, agent as A_  # noqa: E402
from booktrans.models import CHAIN_KEYS                                     # noqa: E402

for k in list(os.environ):
    if k.startswith("BT_"):
        del os.environ[k]

MINI_KEY = """booktrans-bench-key 9.9
source en
[areas]
a Area A
b Area B
[penalties]
ADD-minor judge 3 20 :: a word added
ADD-major judge 6 20 :: a fact added
UNTR judge 3 12 :: left untranslated
STRUCT code 5 25 :: a block lost
SCRIPT code 3 12 :: foreign script
RETRY code 3 50 :: an extra request
[checks]
X1 a 2 b0001 :: first
X2 a 1 b0002-b0003 :: second
Y1 b 1 b0001,b0003 :: third
"""


def main():
    bad = seen = 0
    lang.set_ui("ru")

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # ---- встроенный набор
    s = B.load_set("en")
    key = s["key"]
    ok("набор en читается, ключ 1.4.0 на 250 очков", key["max"] == 250 and key["source"] == "en"
       and key["version"] == "1.4.0", (key["max"], key["version"]))
    ok("в ключе штраф за лишние попытки, считает код",
       key["penalties"]["RETRY"]["per"] == 8 and key["penalties"]["RETRY"]["cap"] == 126
       and key["penalties"]["RETRY"]["who"] == "code", key["penalties"].get("RETRY"))
    ok("областей тринадцать, точек не меньше ста",
       len(key["areas"]) == 13 and len(key["checks"]) >= 100,
       (len(key["areas"]), len(key["checks"])))
    d = tempfile.mkdtemp()
    fb2 = os.path.join(d, "t.fb2")
    open(fb2, "w", encoding="utf-8").write(s["text"])
    _, blocks, _, _ = E.read_book(fb2)
    chunks = P.make_chunks(blocks)
    ok("текст набора — один кусок конвейера", len(chunks) == 1,
       [(c["words"], len(c["blocks"])) for c in chunks])
    ok("в тексте есть стихи и заголовок",
       any(b["kind"] == "verse" for b in blocks) and any(b["kind"] == "title" for b in blocks))
    ids = {b["id"] for b in blocks}
    lost = [c["id"] for c in key["checks"]
            for b in c["blocks"] if not any(i.endswith("." + b) for i in ids)]
    ok("все блоки ключа есть в тексте", not lost, lost[:5])
    ok("набора с чужим именем нет — понятный отказ",
       _raises(SystemExit, B.load_set, "nope"))

    # ---- разбор ключа
    k = B.parse_key(MINI_KEY)
    ok("мини-ключ: суммы и диапазон блоков",
       k["max"] == 4 and k["checks"][1]["blocks"] == ["b0002", "b0003"], k["checks"][1])
    ok("максимум области выводится из весов", dict((a, m) for a, m, _ in k["areas"]) == {"a": 3, "b": 1})
    ok("старый формат с числом у области отвергается",
       _raises(ValueError, B.parse_key, MINI_KEY.replace("a Area A", "a 3 Area A")))
    ok("штраф без описания отвергается",
       _raises(ValueError, B.parse_key, MINI_KEY.replace("UNTR judge 3 12 :: left untranslated", "UNTR 3 12")))
    ok("ключ с двойным id отвергается",
       _raises(ValueError, B.parse_key, MINI_KEY.replace("Y1 b", "X1 b")))

    # ---- вердикт и счёт
    out = "[[[CHECK X1 ok]]]\n[[[CHECK X2 fail]]] потеряно предложение\n[[[CHECK Y1 OK]]]\n" \
          "[[[ADD s01.b0002 major]]] «он вздохнул»\n[[[UNTR s01.b0003]]] Momus\n" \
          "[[[NOTE s01.b0001]]] вкусовое"
    v, pens, rem = B.parse_verdict(out, k)
    ok("вердикты: ok/fail и регистр", v["X1"][0] and not v["X2"][0] and v["Y1"][0], v)
    ok("причина провала сохранена", v["X2"][1] == "потеряно предложение", v["X2"])
    ok("штрафы и замечания разобраны",
       pens == [("ADD-major", "s01.b0002", "«он вздохнул»"), ("UNTR", "s01.b0003", "Momus")]
       and rem == [("s01.b0001", "вкусовое")], (pens, rem))
    ok("точка без вердикта — ошибка с её именем",
       _raises(ValueError, B.parse_verdict, "[[[CHECK X1 ok]]]", k, contains="X2"))
    sc = B.score(k, v, pens)
    ok("счёт: области, штрафы, сырой итог в очках ключа", sc["areas"] == {"a": 2, "b": 1}
       and sc["penalty"] == {"ADD": 6, "UNTR": 3} and sc["raw"] == -6, sc)
    ok("нормированный итог не ниже нуля", sc["score"] == 0.0, sc["score"])
    ok("штраф — в очках ключа: при удвоенных весах точек он весит вдвое меньше",
       B.score(k, v, pens[1:])["score"] == 0.0 and
       B.score(B.parse_key(MINI_KEY.replace("X1 a 2", "X1 a 4").replace("X2 a 1", "X2 a 2").replace("Y1 b 1", "Y1 b 2")), v, pens[1:])["score"] == 37.5)
    ok("штрафы судьи — в промпте из ключа, штрафы кода — нет",
       "[[[ADD s01.b0012 minor]]]" in B.judge_system(k, "ru", "ru") and "a fact added" in B.judge_system(k, "ru", "ru")
       and "STRUCT" not in B.judge_system(k, "ru", "ru") and "{penalty" not in B.judge_system(k, "ru", "ru"))
    many = [("ADD-minor", "s01.b0001", "x")] * 9
    ok("потолок штрафа общий для градаций",
       B.score(k, v, many + [("ADD-major", "s01.b0001", "y")])["penalty"]["ADD"] == 20)
    full = {i: (True, "") for i in ("X1", "X2", "Y1")}
    ok("всё пройдено без штрафов — 100", B.score(k, full, [])["score"] == 100)
    ok("среднее: срыв нулём не пропадает", B.mean([0, 78, 80]) == 52.7 and B.mean([5]) == 5)

    # ---- проверки кодом
    blocks = [{"id": "s01.b0001", "kind": "p", "text": "One <i>two</i> three."},
              {"id": "s01.b0002", "kind": "p", "text": "Four."},
              {"id": "s01.b0003", "kind": "verse", "text": "Five."},
              {"id": "s01.b0004", "kind": "title", "text": "Head"}]
    tr = {"s01.b0001": "Раз два три.", "s01.b0003": "Пять 漢字.", "s01.b0009": "лишний"}
    pen = B.code_checks(blocks, tr, "en", "ru")
    ok("код: пропавший блок, лишний блок, теги, иероглифы",
       sorted(pen) == sorted([("STRUCT", "s01.b0001", "tags ['i'] -> []"),
                              ("STRUCT", "s01.b0002", "missing"),
                              ("STRUCT", "s01.b0009", "extra"),
                              ("SCRIPT", "s01.b0003", "漢字")]), pen)
    ok("латиница и греческие буквы в русском — не чужая письменность",
       not B.foreign("Gamma Cassiopeiae, γ Кассиопеи", ["latin", "cyrillic"]))
    ok("кириллица в китайском переводе — чужая",
       B.foreign("漢字 слово", ["latin", "cjk"]) == list("слово"))

    # ---- командная строка
    a = cli.parser("ru").parse_args(["--bench", "--to", "ru"])
    ok("--bench без книги и без значения", a.bench == "-" and a.book is None, a.bench)
    a = cli.parser("ru").parse_args(["--bench", "мой-набор", "--judge", "codex:gpt-5.6-sol"])
    ok("--bench ПАПКА и --judge", a.bench == "мой-набор" and a.judge == "codex:gpt-5.6-sol")
    ok("судья проверяется вместе с цепочками", "judge" in CHAIN_KEYS)

    # ---- семья судьи
    class Args:
        judge, agent, self_edit = None, "claude", None
    class Fake:
        def __init__(self, n, m, e):
            self.kind, self.model, self.effort = n, m, e

    class Ms:
        def _agent(self, n, m, e=None):
            return Fake(n, m, e)
    who, dropped = B.judges(Args(), Ms(), Fake("claude", "claude-sonnet-5", "high"))
    ok("судья семьи переводчика по умолчанию допущен",
       [w.model for w in who] == ["gpt-6-sol", "claude-opus-5-5"] and dropped == [],
       ([w.model for w in who], [x.model for x in dropped]))
    Args.self_edit = "never"
    who_n, dropped_n = B.judges(Args(), Ms(), Fake("claude", "claude-sonnet-5", "high"))
    ok("--self-edit never вычёркивает судью семьи переводчика",
       [w.model for w in who_n] == ["gpt-6-sol"] and [x.model for x in dropped_n] == ["claude-opus-5-5"])
    Args.self_edit = None
    ok("усилие судьи из умолчания — medium", who[0].effort == "medium", who[0].effort)
    Args.judge = "agy:claude-opus-4-6-thinking"
    who, _ = B.judges(Args(), Ms(), Fake("openai", "glm-5.3", "medium"))
    ok("судье agy без усилия его и не подставляют", who[0].effort is None, who[0].effort)
    Args.judge, Args.self_edit = "claude:claude-opus-5-5,codex:gpt-5.6-sol", "allow"
    who, _ = B.judges(Args(), Ms(), Fake("claude", "claude-haiku-4-5", "medium"))
    ok("--self-edit allow: судья своей семьи судит", [w.model for w in who] == ["claude-opus-5-5", "gpt-5.6-sol"])
    Args.self_edit = "last"
    who, _ = B.judges(Args(), Ms(), Fake("claude", "claude-haiku-4-5", "medium"))
    ok("--self-edit last: судья своей семьи последний", [w.model for w in who] == ["gpt-5.6-sol", "claude-opus-5-5"])
    Args.self_edit = None
    os.environ["OPENAI_BASE_URL"] = "https://router.example.org/v1"
    w = B._who(Fake("openai", "glm-5.3", "medium"))
    ok("у сетевой модели в отчёте адрес точки", B._spec(w) == "openai@router.example.org:glm-5.3:medium", B._spec(w))
    del os.environ["OPENAI_BASE_URL"]
    ok("у CLI-агента адреса нет", B._spec(B._who(Fake("codex", "gpt-6-astra", "medium"))) == "codex:gpt-6-astra:medium")
    # ---- чья вина в несделанном куске: модели — ноль, связи — не в счёт
    AG = A_
    P.RETRY_PAUSE = 0
    class Bad:
        kind, model, effort = "openai", "m", None
        def __init__(self, errs): self.errs = list(errs)
        def run(self, system, user):
            e = self.errs.pop(0)
            if isinstance(e, Exception): raise e
            return e, {}
    def parse(out): raise ValueError("не по форме")
    def fault(errs):
        try:
            P._run(Bad(errs), "", "", len(errs), parse, lambda *a: None)
        except Exception:
            pass
        return P.FAULT.kind
    ok("все попытки не по форме — срыв модели", fault(["x", "y", "z"]) == "model", P.FAULT.kind)
    ok("зациклилась до предела вывода — срыв модели",
       fault([AG.OutputLimit("65536"), AG.OutputLimit("65536")]) == "model", P.FAULT.kind)
    ok("хоть одну попытку сорвала связь — вина роутера",
       fault([AG.AgentError("connection interrupted"), "x", AG.OutputLimit("1")]) == "net", P.FAULT.kind)
    Args.judge = "none"
    ok("--judge none — судей нет, перевод без суда",
       B.judges(Args(), Ms(), Fake("openai", "glm-5.3", "medium")) == ([], []))
    Args.judge = "claude:claude-opus-5"
    ok("судья одной семьи по умолчанию судит",
       [w.model for w in B.judges(Args(), Ms(), Fake("claude", "claude-sonnet-5", "high"))[0]] == ["claude-opus-5"])
    Args.self_edit = "never"
    ok("--self-edit never: все судьи одной семьи — остановка",
       _raises(SystemExit, B.judges, Args(), Ms(), Fake("claude", "claude-sonnet-5", "high")))
    Args.self_edit = None

    # ---- отчёт
    r = {"score": sc, "key": k, "set": "en", "to": "ru", "date": "2026-09-23 12:00",
         "booktrans": "1.0.0", "translator": {"provider": "agy", "model": "m", "effort": "high"},
         "judge": {"provider": "claude", "model": "j", "effort": ""},
         "verdicts": v, "penalties": pens, "remarks": rem, "code_checks": [], "footnotes": 0,
         "cost": {"translate": None, "judge": 1.0}, "time": {"translate": 60, "judge": 60},
         "work": "w"}
    md = B.report_md(r, "en")
    ok("отчёт начинается с нормированного балла", md.startswith("# 0.0 / 100\n"), md[:20])
    ok("в отчёте провал с причиной и штраф",
       "**X2**" in md and "потеряно предложение" in md and "ADD-major s01.b0002" in md)
    ok("строка таблицы: дата, модели, итог, области, штраф",
       B.table_row(r) == "| 2026-09-23 | agy:m:high | 0.0 | 1 | 1 | 2 | 1 | -9 | 1.0.0 | 9.9 | claude:j |",
       B.table_row(r))
    allok = {c["id"]: (True, "") for c in key["checks"]}
    full_r = dict(r, key=key, score=B.score(key, allok, []), verdicts=allok, penalties=[])
    ok("отчёт по-русски называет области по-русски",
       "Точность смысла" in B.report_md(full_r, "ru") and "| 100.0 | 1 | 1 | 68 | 4 | 30 | 20 | 10 | 28 | 6 | 24 | 14 | 14 | 20 | 4 | 8 | 0 |" in B.table_row(full_r),
       B.table_row(full_r))
    multi = dict(full_r, runs=[full_r, dict(r, attempts=2), full_r], scores=[100.0, 0.0, 100.0],
                 mean=100.0, work="w")
    row = B.table_row(multi)
    ok("три прогона: среднее, разброс и попытки в строке", "| 100.0 | 3 (0–100) | 1/2/1 |" in row, row)
    md3 = B.report_md(multi, "en")
    ok("отчёт трёх прогонов: таблица прогонов и среднее",
       md3.startswith("# 100.0 / 100") and "## Runs: 3" in md3 and "Mean 100.0 (from 0.0 to 100.0)" in md3,
       md3[:40])
    ok("судья видит заголовки, разрывы — нет",
       "TTL" in B.judge_prompt(k, [{"id": "s01.b0001", "kind": "title", "text": "TTL"}, {"id": "s01.b0002", "kind": "break", "text": "BRK"}], {}, [])
       and "BRK" not in B.judge_prompt(k, [{"id": "s01.b0002", "kind": "break", "text": "BRK"}], {}, []))

    # ---- пропуски: штраф есть, только если ключ его объявляет
    RETRY = "RETRY code 3 50 :: an extra request\n"
    ko = B.parse_key(MINI_KEY.replace(RETRY, RETRY + "OMIT-minor judge 1 50 :: a word lost\nOMIT-major judge 3 50 :: a sentence lost\n"))
    _, po, _ = B.parse_verdict("[[[CHECK X1 ok]]]\n[[[CHECK X2 ok]]]\n[[[CHECK Y1 ok]]]\n"
                               "[[[OMIT s01.b0002 major]]] «She read.»\n[[[OMIT s01.b0003]]] «icy»\n", ko)
    ok("OMIT читается со степенью, без степени — minor",
       po == [("OMIT-major", "s01.b0002", "«She read.»"), ("OMIT-minor", "s01.b0003", "«icy»")], po)
    allv = {c["id"]: (True, "") for c in ko["checks"]}
    so = B.score(ko, allv, po)
    ok("OMIT: minor 1 + major 3", so["penalty"] == {"OMIT": 4} and so["raw"] == 0, so)
    ok("OMIT: потолок 50 на обе степени вместе",
       B.score(ko, allv, [("OMIT-major", "s01.b0001", "x")] * 30)["penalty"]["OMIT"] == 50)
    ok("ключ без OMIT: пропуски не штрафуются и в счёт не попадают",
       B.score(k, allv, po)["penalty"] == {} and B.score(k, allv, po)["counts"] == {})
    ok("правило о пропусках — в промпте судьи только при OMIT в ключе",
       "[[[OMIT s01.b0012 major]]]" in B.judge_system(ko, "ru", "ru") and "[[[OMIT" not in B.judge_system(k, "ru", "ru"))

    # ---- лишние сноски: белый список тем в ключе, штраф FOOT
    kf = B.parse_key(MINI_KEY.replace(RETRY, RETRY + "FOOT judge 1 5 :: a note outside [notes]\n").replace("[checks]", "[notes]\nMomus\nHelen of Troy\n[checks]"))
    ok("раздел [notes] читается", kf["notes"] == ["Momus", "Helen of Troy"], kf["notes"])
    _, pf, _ = B.parse_verdict("[[[CHECK X1 ok]]]\n[[[CHECK X2 ok]]]\n[[[CHECK Y1 ok]]]\n[[[FOOT s01.b0002]]] Марс\n", kf)
    ok("FOOT читается без степени", pf == [("FOOT", "s01.b0002", "Марс")], pf)
    ok("FOOT: по очку, потолок 5", B.score(kf, allv, pf)["penalty"] == {"FOOT": 1}
       and B.score(kf, allv, pf * 9)["penalty"]["FOOT"] == 5)
    ok("ключ без FOOT: лишняя сноска не штрафуется", B.score(k, allv, pf)["penalty"] == {})
    ok("правило о сносках и перечень тем — только при FOOT в ключе",
       "[[[FOOT" in B.judge_system(kf, "ru", "ru") and "[[[FOOT" not in B.judge_system(k, "ru", "ru")
       and "Helen of Troy" in B.judge_prompt(kf, [], {}, []))

    # ---- статистика ловушек по готовым прогонам
    d = tempfile.mkdtemp()
    kk = {"version": "9.9.1", "checks": [{"id": "X1", "area": "a", "text": "first"},
                                         {"id": "X2", "area": "a", "text": "second"}]}

    def put(path, **kw):
        os.makedirs(os.path.join(d, os.path.dirname(path)), exist_ok=True)
        json.dump(dict({"key": kk, "to": "ru"}, **kw), open(os.path.join(d, path), "w"))
    tr_a = {"provider": "p", "model": "a", "effort": "medium"}
    tr_b = {"provider": "p", "model": "b", "effort": "medium"}
    ok_ = lambda x1, x2: {"X1": [x1, ""], "X2": [x2, ""]}             # noqa: E731
    put("benchmark-en-ru-a.work/run1/ru/bench.json", translator=tr_a, verdicts=ok_(True, True))
    put("benchmark-en-ru-a.work/run2/ru/bench.json", translator=tr_a, verdicts=ok_(True, False))
    put("benchmark-en-ru-a.work/run3/ru/bench.json", translator=tr_a, verdicts=ok_(True, False))
    put("benchmark-en-ru-a.work/run4/ru/bench.json", translator=tr_a, verdicts={}, failed="model")
    put("benchmark-en-ru-a.work/bench.json", translator=tr_a, verdicts=ok_(True, True), runs=[1])
    put("benchmark-en-ru-b.work/ru/bench.json", translator=tr_b, verdicts=ok_(False, True))
    put("benchmark-en-ru-c.work/ru/bench.json", translator=tr_b, verdicts=ok_(False, False),
        key=dict(kk, version="9.8"))
    st = B.stats(["9.9."], d)
    rows = {r["id"]: r for r in st.get("ru", {}).get("rows", [])}
    ok("статистика: сводка серии и сломанный прогон не в счёте, чужая версия тоже",
       st.get("ru", {}).get("runs") == 4 and st["ru"]["series"] == 2, st.get("ru"))
    ok("статистика: модели с равным весом, прогоны — поровну",
       abs(rows["X2"]["models"] - (1 / 3 + 1) / 2) < 1e-9 and rows["X2"]["runs"] == 0.5
       and rows["X1"]["models"] == 0.5 and rows["X1"]["runs"] == 0.75, rows)
    ok("статистика: версия точно, «9.9.» — вся ветка",
       B._ver_ok("9.9.1", ["9.9."]) and B._ver_ok("9.9", ["9.9."])
       and not B._ver_ok("9.90", ["9.9."]) and not B._ver_ok("9.9.1", ["9.9"])
       and B._ver_ok("9.9", ["9.9"]) and not B._ver_ok("9.8", ["9.9."]))
    ok("статистика: сначала самые лёгкие", [r["id"] for r in st["ru"]["rows"]] == ["X2", "X1"])

    lang.set_ui("ru")

    print(f"\n{'ВСЁ СОВПАДАЕТ' if not bad else f'РАСХОЖДЕНИЙ: {bad}'} ({seen} проверок)")
    return 1 if bad else 0


def _raises(exc, fn, *a, contains=None):
    try:
        fn(*a)
    except exc as e:
        return contains is None or contains in str(e)
    return False


if __name__ == "__main__":
    sys.exit(main())
