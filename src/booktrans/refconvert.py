"""Перекладка справочника в строки нового вида.

Раньше ключ строки писался «Перевод (Оригинал)», род и свойства жили в
отдельных таблицах GENDER и WORLD, и отбор по куску ловил только строки,
где оригинал угадывался в скобках. Теперь у сущности одна строка:
`| оригинал | перевод | род и склонение | содержимое |` в CHARACTERS, NAMES и
TERMS, `| оригинал | перевод | содержимое |` в ADDRESS и FOOTNOTES. Здесь
всё, что связано со старым видом: разбор такого ключа, перекладка строк
разведки, вливание GENDER и WORLD в строки сущностей и однократная
конвертация рабочей папки. Когда старых папок не останется, модуль
выбрасывается вместе с вызовами в pipeline.scout, pipeline._settle_rows,
pipeline._cycle_canon и cli.main.
"""
import json
import os
import re

from .lang import T

# Выпуск, с которого справочник пишется в новом виде; папку, которую
# последней трогала версия старше, convert_ref перекладывает при открытии.
REF_FORMAT = "1.10.9"

# Разделы, чьи строки вливаются в ячейки сущностей: род — в третью,
# свойства — в четвёртую.
_FOLD = {"GENDER": 2, "WORLD": 3}
# Раздел, который прежде дописывал _unfork; теперь выбор вносится в текст.
_ONETERM = "## ОКОНЧАТЕЛЬНЫЙ ВЫБОР ПО ТЕРМИНАМ"


def _script_re(to):
    """Регулярка букв письменности целевого языка; None — письменность
    неизвестна."""
    from .lang import SCRIPTS, script_of
    rng = SCRIPTS.get(script_of(to) or "", "")
    return re.compile(f"[{rng}]") if rng else None


def _split_key(key, tgt):
    """Старый ключ — в (оригинал, перевод): «Перевод (Оригинал)»,
    «Оригинал (перевод)», «Перевод / Оригинал». Без скобок и косой —
    (ключ, '')."""
    inner = [p.strip() for p in re.findall(r"\(([^()]+)\)", key)]
    outer = " ".join(re.sub(r"\([^()]*\)", " ", key).split())
    outer = re.sub(r"\s+([,;])", r"\1", outer).strip(" :—–-")
    if len(inner) == 1 and inner[0].lower() == outer.lower():
        return outer, ""                    # «Fern (Fern)»
    if inner:
        orig, trans = "; ".join(inner), outer
        if tgt and tgt.search(orig) and not tgt.search(trans):
            orig, trans = trans, orig   # «слово (перевод)» у сносок
        elif tgt and not tgt.search(trans):
            return f"{trans}; {orig}", ""   # «Имя (Прозвище)» — оба оригиналы
        return orig, trans
    if tgt and " / " in key:
        parts = [p.strip() for p in key.split(" / ")]
        mine = [p for p in parts if tgt.search(p)]
        theirs = [p for p in parts if not tgt.search(p)]
        if mine and theirs:
            return "; ".join(theirs), "; ".join(mine)
    return key, ""


_ADDR_SEP = re.compile(r"\s+[—–↔→&]\s+|\s+(?:и|and)\s+|\s*[/;,]\s*")


def _words(s):
    return {w.strip(".,;:«»\"'()") for w in s.lower().split()}


def _name_like(s, tgt, upper, orig):
    """Короткое написание — перевод, а не справка; `upper` — имя
    собственное, с заглавной. Несколько написаний через «;» или «,» — тоже
    написание. Буквы чужой письменности допустимы в словах оригинала и
    коротких кодах («Google Документы», «трасса I-95»); написание без
    единой буквы целевого языка — только сам оригинал («JPIGGOT»)."""
    parts = [p for p in re.split(r"\s*[;,]\s*", s) if p]
    if not parts or any(len([w for w in _words(p) if re.search(r"[^\W\d_]", w)])
                        > 5 or re.search(r"[!?]", p) for p in parts):
        return False
    if upper and not any(ch.isupper() for ch in s):
        return False
    if not tgt:
        return True
    if not tgt.search(s):
        return s.lower() == orig.lower()
    ok = _words(orig)
    return all(w in ok or len(re.findall(r"[^\W\d_]", w)) <= 3
               for w in _words(tgt.sub("", s)) if re.search(r"[^\W\d_]", w))


def _head(cell, sec, tgt, orig):
    """Ячейка «Перевод — справка» или «Перевод, справка» старой строки — в
    (перевод, справка); одно написание — (оно, ''); справка — ('', она).
    Из разделителей берётся первый, за которым остаётся написание: в
    «мистер Гладли; обращение Mr. G — мистер Джи» это «;»."""
    upper = sec != "TERMS" and any(ch.isupper() for ch in orig)
    # Двоеточие — только после имени: «Гилпатрик: офицер»; у термина за ним
    # бывает и справка целиком.
    seps = list(re.finditer(r"\s+[—–]\s+" + (r"|:\s+" if upper else ""), cell))
    # «Рой; Тейлор» и «Штаты, США» — два написания, не справка.
    seps += [m for m in re.finditer(r"[;,]\s+(?=[^\W\d_])", cell)
             if cell[m.end()].islower()]
    for m in sorted(seps, key=lambda m: m.start()):
        head = cell[:m.start()].rstrip(".")
        # Разделитель внутри кавычек или скобок — часть написания.
        if (any(head.count(o) != head.count(c) for o, c in ("«»", "()"))
                or head.count('"') % 2):
            continue
        if _name_like(head, tgt, upper, orig):
            return head, cell[m.end():]
    whole = cell.rstrip(".")
    if _name_like(whole, tgt, upper, orig):
        return whole, ""
    return "", cell


def canon_row(line, sec, tgt, legacy=False):
    """Строка реестра — в новый вид. Возвращает (строка, переложена ли,
    мёртвая ли): мёртвая — без единой буквы вне письменности целевого
    языка, такую по куску не найти. `legacy` — строка старого справочника:
    всё после перевода там справка, третьей ячейки рода ещё не было."""
    from .pipeline import _BULLET_KEY, _HEADER_KEYS, REF_ENTITY, _row
    t = " ".join(line.split())
    if t.startswith("|") and t.count("|") > 2:
        cells = [c.strip() for c in t.strip("|").split("|")]
    else:
        m = _BULLET_KEY.match(line)
        if not m:
            return line, False, False
        rest = line[m.end():].strip().lstrip(":—–- ").strip()
        cells = [m.group(1).strip(), rest]
    key = re.sub(r"[\s*_`~]+", " ", cells[0]).strip()
    if not key or set(key) <= set("- :") or key.lower() in _HEADER_KEYS:
        return line, False, False
    orig, trans = _split_key(key, tgt)
    if orig.lower() in _HEADER_KEYS:
        # Шапка старой таблицы с «оригиналом» в скобках; новой шапка не нужна.
        return ("", True, False) if legacy else (line, False, False)
    if sec == "ADDRESS":
        orig = "; ".join(p for p in _ADDR_SEP.split(orig) if p)
    dead = bool(tgt) and not re.search(r"[^\W\d_]", tgt.sub("", orig))
    new = [orig, trans] + cells[1:] if trans else [orig] + cells[1:]
    if sec in REF_ENTITY and not dead:
        if len(new) == 2:
            new = [orig, *_head(new[1], sec, tgt, orig)]
        if len(new) == 3:
            new = [new[0], new[1], "", new[2]]
        elif len(new) > 3 and legacy:
            new = [new[0], new[1], "", "; ".join(c for c in new[2:] if c)]
    out = _row(new)
    if t == out:
        return line, False, dead
    return out, True, dead


def _originals(lines, rows, tgt):
    """Перевод → оригинал по строкам сущностей самого справочника: имена в
    ячейках через «;» сопоставляются попарно."""
    from .pipeline import _cells, REF_ENTITY
    known = {}
    for i, sec, is_dead in rows:
        if sec not in REF_ENTITY or is_dead or lines[i] is None:
            continue
        cells = _cells(lines[i])
        if len(cells) < 3 or not cells[1]:
            continue
        op = [x.strip() for x in cells[0].split(";")]
        tp = [x.strip() for x in cells[1].split(";")]
        pairs = zip(op, tp) if len(op) == len(tp) else [(cells[0], cells[1])]
        for o, t in pairs:
            if o and t and not (tgt.search(o) and not tgt.search(t)):
                known.setdefault(t.lower(), o)
    return known


def _revive(line, sec, known):
    """Мёртвой строке — оригинал из справочника: точным переводом или
    именем-началом полного («Тейлор» ⊂ «Тейлор Эберт»). Имя, которого в
    справочнике нет, остаётся как было."""
    from .pipeline import _cells, _row, REF_ENTITY
    cells = _cells(line)
    parts, found = [], 0
    for p in cells[0].split(";"):
        p = p.strip()
        k = p.lower()
        o = known.get(k) or next((v for kk, v in known.items()
                                  if kk.startswith(k + " ")), None)
        parts.append(o or p)
        found += bool(o)
    if not found:
        return None
    if sec == "ADDRESS":
        parts = [q for p in parts for q in _ADDR_SEP.split(p) if q]
    new = ["; ".join(parts)] + cells
    if sec in REF_ENTITY and len(new) == 3:
        new = [new[0], new[1], "", new[2]]
    return _row(new)


def _home(key, sec):
    """Раздел для строки GENDER или WORLD, у которой нет своей сущности:
    имя с заглавной — персонаж или название, прочее — термин."""
    if not any(ch.isupper() for ch in key):
        return "TERMS"
    return "CHARACTERS" if sec == "GENDER" else "NAMES"


def _fold(lines, kinds, rows, order, tgt):
    """Строки GENDER и WORLD — в ячейки своих сущностей; вошедшие
    вычёркиваются. Сущность ищется по ключу целиком, потом по частям ключа:
    роду годятся все персонажи с такими именами, свойствам — единственная
    строка. Строка без сущности сама становится сущностью в хвосте раздела
    по _home: дописывается в конец lines, а в порядок вывода `order` — за
    последней строкой раздела. Возвращает число тронутых строк."""
    from .pipeline import _cells, _norm_key, _row, REF_ENTITY
    whole, part, last = {}, {}, {}
    alive = {i: is_dead for i, _, is_dead in rows}
    for i in order:
        sec = kinds[i][0]
        if i in alive and sec in REF_ENTITY:
            last[sec] = i
            if not alive[i]:
                k = _norm_key(_cells(lines[i])[0])
                whole.setdefault(k, i)
                for p in k.split(" / "):
                    part.setdefault(p, set()).add(i)
    n = 0
    for i, sec, is_dead in list(rows):
        if sec not in _FOLD or is_dead or lines[i] is None:
            continue
        cells = _cells(lines[i])
        k = _norm_key(cells[0])
        at = _FOLD[sec]
        if k in whole:
            hits = [whole[k]]
        else:
            hits = set().union(*(part.get(p, set()) for p in k.split(" / ")))
            if sec == "GENDER":
                hits = [j for j in hits if kinds[j][0] == "CHARACTERS"] or hits
            elif len(hits) != 1:
                hits = []
        body = "; ".join(c for c in (cells[2:] if len(cells) > 2 else cells[1:])
                         if c)
        if not body:
            continue
        for j in hits:
            ecells = _cells(lines[j])
            if len(ecells) < 4:
                continue
            ecells[at] = "; ".join(x for x in (ecells[at].rstrip("."), body)
                                   if x)
            lines[j] = _row(ecells)
        if hits:
            lines[i] = None
            n += 1
            continue
        home = _home(cells[0], sec)
        if home not in last:
            continue
        trans, body = _head(cells[1], home, tgt, cells[0]) if len(cells) == 2 \
            else (cells[1], body)
        new = [cells[0], trans, "", ""]
        new[at] = body
        lines.append(_row(new))
        kinds.append((home, "row"))
        rows.append((len(lines) - 1, home, False))
        order.insert(order.index(last[home]) + 1, len(lines) - 1)
        last[home] = len(lines) - 1
        lines[i] = None
        n += 1
    return n


def canon_ref(text, to, legacy=False):
    """Все строки реестра — в новый вид. Возвращает (текст, сколько
    переложено, сколько мёртвых). `legacy` — справочник старой версии:
    строки старых таблиц перекладываются целиком, а раздел, оставшийся без
    строк, убирается — см. canon_row."""
    from .pipeline import REF_ENTITY, REF_KEYED, _ref_scan
    tgt = _script_re(to)
    lines, kinds, rows, n, dead = [], [], [], 0, 0
    skip = False
    for sec, _head, _key, line, kind in _ref_scan(text):
        if kind == "head":
            skip = legacy and line.strip() == _ONETERM
        if skip or (legacy and kind == "junk" and sec in REF_ENTITY):
            continue
        if kind == "row" and sec in REF_KEYED:
            line, changed, is_dead = canon_row(line, sec, tgt, legacy)
            n += changed
            rows.append((len(lines), sec, is_dead))
        lines.append(line)
        kinds.append((sec, kind))
    order = list(range(len(lines)))
    # Оживление и вливание — по кругу: строка, ставшая сущностью при
    # вливании, даёт оригинал мёртвым строкам о ней.
    while True:
        step = 0
        if tgt:
            known = _originals(lines, rows, tgt)
            for at, (i, sec, is_dead) in enumerate(rows):
                if is_dead and (fixed := _revive(lines[i], sec, known)):
                    lines[i] = fixed
                    rows[at] = (i, sec, False)
                    step += 1
        step += _fold(lines, kinds, rows, order, tgt)
        n += step
        if not step:
            break
    dead = sum(is_dead for _, _, is_dead in rows)
    # Раздел GENDER или WORLD, отдавший все строки и без прозы, не нужен.
    for sec in _FOLD:
        mine = [i for i, (s, _) in enumerate(kinds) if s == sec]
        if mine and not any(lines[i] is not None and lines[i].strip()
                            and kinds[i][1] != "head" for i in mine):
            for i in mine:
                lines[i] = None
    res = "\n".join(lines[i] for i in order if lines[i] is not None)
    res = re.sub(r"\n{3,}", "\n\n", res)
    if text.endswith("\n") and not res.endswith("\n"):
        res += "\n"
    return res, n, dead


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def convert_ref(work, to, log):
    """Справочник папки, которую последней трогала версия старше REF_FORMAT,
    переложить в новый вид; старый — в scout.md.bak."""
    from .pipeline import lpath
    sp = lpath(work, "scout.md", to)
    if not os.path.exists(sp):
        return
    try:
        with open(os.path.join(work, "versions.json"), encoding="utf-8") as f:
            last = json.load(f)["last"]["pipeline"].split()[0]
    except (OSError, ValueError, KeyError, IndexError):
        last = "0"
    if _vtuple(last) >= _vtuple(REF_FORMAT):
        return
    with open(sp, encoding="utf-8") as f:
        txt = f.read()
    new, n, dead = canon_ref(txt, to, legacy=True)
    if new != txt:
        with open(sp + ".bak", "w", encoding="utf-8") as f:
            f.write(txt)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(new)
    if n:
        log(T("ref_converted", n))
    if dead:
        log(T("ref_dead", dead))
