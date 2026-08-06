#!/usr/bin/env python3
"""Проверка таблиц: чтение, перевод, запись.

Таблица идёт через конвейер одним блоком: строка таблицы — на строку, ячейки
через ` | `. Разделитель проходит через модель наравне с разметкой вроде
`<b>`, и сломать его можно двумя способами — потерять при чтении и не собрать
обратно при записи. Проверяем оба конца.

    python3 tests/table_check.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E, output as O                # noqa: E402

PAGE = """<html><head><title>С таблицей</title></head><body>
<h1>Глава</h1>
<p>Перед таблицей.</p>
<table>
<tr><th>Год</th><th>Событие</th></tr>
<tr><td>1915</td><td>Родился в <i>Сент-Поле</i></td></tr>
<tr><td>1942</td><td>Окончил школу | с отличием</td></tr>
</table>
<p>После таблицы.</p>
</body></html>
"""

# Слияния ячеек. При rowspan в следующей строке ячейки просто нет — ни в
# разметке, ни у нас в строке, — поэтому список слияний ложится на ячейки один
# в один и покрывает любое сочетание с colspan.
SPAN = """<html><head><title>Слияния</title></head><body>
<h1>Глава</h1>
<table>
<tr><th colspan="3">Шапка на три</th></tr>
<tr><th>№</th><th>Что</th><th>Год</th></tr>
<tr><td rowspan="2">1</td><td>Первая половина</td><td>1942</td></tr>
<tr><td>Вторая половина</td><td>1942</td></tr>
<tr><td>2</td><td colspan="2">Растянут на две</td></tr>
</table>
<p>После.</p>
</body></html>
"""

# Вложенная таблица: ею в старой вёрстке разбивали страницу на колонки.
NEST = """<html><head><title>Вложенная</title></head><body>
<h1>Глава</h1>
<table>
<thead><tr><th>Раз</th><th>Два</th></tr></thead>
<tbody>
<tr><td>внешняя A</td><td><table><tr><td>внутри 1</td><td>внутри 2</td></tr></table></td></tr>
<tr><td>внешняя B</td><td>обычная</td></tr>
</tbody></table>
<p>После.</p>
</body></html>
"""

# Та же таблица, свёрстанная небрежно: строки и ячейки не закрыты.
DIRTY = """<html><head><title>Небрежно</title></head><body>
<h1>Глава</h1>
<p>Текст.</p>
<table><tr><td>раз<td>два<tr><td>три<td>четыре</table>
<p>Ещё текст.</p>
</body></html>
"""


def read(src):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "book.html")
    open(p, "w", encoding="utf-8").write(src)
    out = E.read_book(p)
    shutil.rmtree(d)
    return out


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:40} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    meta, blocks, cover, imgs = read(PAGE)
    tbl = [b for b in blocks if b["kind"] == "table"]
    ok("таблица прочитана одним блоком", len(tbl) == 1,
       [(b["kind"], b["text"][:30]) for b in blocks])
    rows = tbl[0]["text"].splitlines() if tbl else []
    ok("строк три", len(rows) == 3, rows)
    ok("шапка помечена жирным",
       rows and rows[0] == "<b>Год</b> | <b>Событие</b>", rows[:1])
    ok("разметка внутри ячейки цела",
       any("<i>Сент-Поле</i>" in r for r in rows), rows)
    # Разделитель — знак разметки, поэтому в тексте он экранируется.
    ok("своя черта в ячейке экранирована",
       any(r"\|" in r for r in rows), rows)
    ok("ячейки таблицы не вышли ещё и абзацами",
       not any(b["kind"] == "p" and "1915" in b["text"] for b in blocks),
       [b["text"][:20] for b in blocks if b["kind"] == "p"])

    items = [(b["kind"], b["text"], b["id"], b.get("links")) for b in blocks]
    d = tempfile.mkdtemp()
    O.write_html(os.path.join(d, "a.html"), meta, items, {}, imgs, "Прим.:", {})
    h = re.search(r"<table>.*?</table>", open(os.path.join(d, "a.html"),
                                              encoding="utf-8").read(), re.S)
    ok("в html собрана таблица", bool(h))
    ok("ячеек в html шесть", h and h.group().count("<td>") == 6,
       h.group() if h else "")
    ok("экранирование снято при сборке",
       h and "Окончил школу | с отличием" in h.group(), h.group() if h else "")

    O.write_txt(os.path.join(d, "a.txt"), meta, items, {}, imgs, "Прим.:", {})
    txt = open(os.path.join(d, "a.txt"), encoding="utf-8").read()
    ok("в txt строки таблицы на месте", "1915" in txt and "1942" in txt)
    shutil.rmtree(d)

    meta, blocks, cover, imgs = read(DIRTY)
    tbl = [b for b in blocks if b["kind"] == "table"]
    ok("небрежная вёрстка прочитана", len(tbl) == 1,
       [(b["kind"], b["text"][:30]) for b in blocks])
    ok("две строки по две ячейки",
       tbl and [r.split(" | ") for r in tbl[0]["text"].splitlines()]
       == [["раз", "два"], ["три", "четыре"]],
       tbl[0]["text"] if tbl else "")

    meta, blocks, cover, imgs = read(NEST)
    tbl = [b for b in blocks if b["kind"] == "table"]
    ok("вложенная таблица не задвоила строки", len(tbl) == 1
       and len(tbl[0]["text"].splitlines()) == 3,
       [t["text"] for t in tbl])
    ok("ячейки вложенной не склеились",
       tbl and "внутри 1 внутри 2" in tbl[0]["text"], tbl[0]["text"] if tbl else "")
    ok("thead и tbody не мешают",
       tbl and tbl[0]["text"].splitlines()[0] == "<b>Раз</b> | <b>Два</b>",
       tbl[0]["text"].splitlines()[:1] if tbl else "")

    meta, blocks, cover, imgs = read(SPAN)
    t = [b for b in blocks if b["kind"] == "table"][0]
    ok("слияния собраны по ячейкам",
       t.get("spans") == [[[3, 1]], [[1, 1]] * 3, [[1, 2], [1, 1], [1, 1]],
                          [[1, 1], [1, 1]], [[1, 1], [2, 1]]], t.get("spans"))
    items = [(b["kind"], b["text"], b["id"], b.get("links"), b.get("spans"))
             for b in blocks]
    d = tempfile.mkdtemp()
    O.write_html(os.path.join(d, "s.html"), meta, items, {}, imgs, "Прим.:", {})
    h = re.search(r"<table>.*?</table>",
                  open(os.path.join(d, "s.html"), encoding="utf-8").read(), re.S).group()
    ok("colspan в html", h.count('colspan="3"') == 1 and h.count('colspan="2"') == 1, h)
    ok("rowspan в html", h.count('rowspan="2"') == 1, h)
    shutil.rmtree(d)

    # Таблица без слияний ключа не заводит: хранить нечего.
    meta, blocks, cover, imgs = read(PAGE)
    ok("без слияний ключа нет",
       "spans" not in [b for b in blocks if b["kind"] == "table"][0],
       [b for b in blocks if b["kind"] == "table"][0].keys())

    # Модель вернула другое число ячеек — слияния этой строки не применяются,
    # соседние целы.
    bad_rows = "a | b | c\nодна ячейка вместо трёх\nx | y | z"
    spans = [[[1, 1]] * 3, [[1, 1]] * 3, [[2, 1], [1, 1], [1, 1]]]
    got = O._table_html(bad_rows, O.HTML_INLINE, spans)
    ok("сбитая строка не ломает остальные",
       got.count("<tr>") == 3 and 'colspan="2"' in got, got)

    print(f"\nслучаев: 20   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
