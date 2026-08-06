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

    print(f"\nслучаев: 12   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
