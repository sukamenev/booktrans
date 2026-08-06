#!/usr/bin/env python3
"""Проверка вывода в LaTeX.

Главное здесь — экранирование. У TeX десяток особых знаков, и один из них,
`%`, молча съедает остаток строки: файл соберётся, а текста в нём не будет.
Это ровно та порода ошибки, которую не видно, пока не сверишь с оригиналом.

    python3 tests/tex_check.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import output as O                          # noqa: E402

ITEMS = [
    ("title", "Глава & первая", "s01.b0001", None, None),
    ("p", "Скидка 50% и знак #3, ~тильда~, $доллар$, _подчёрк_, ^крышка^.",
     "s01.b0002", None, None),
    ("p", "Разметка <b>жирным</b> и <i>курсивом</i>, ссылка "
          "<a1>на сайт</a1>.", "s01.b0003", ["https://example.com/a?b=1&c=2"],
     None),
    ("verse", "первая строка\nвторая строка", "s01.b0004", None, None),
    ("code", "if (a < b) { printf(\"100%%\\n\"); }", "s01.b0005", None, None),
    ("table", "<b>Год</b> | Событие\n1942 | Слито на две",
     "s01.b0006", None, [[[1, 1], [1, 1]], [[1, 1], [2, 1]]]),
    ("p", "Абзац со сноской.", "s01.b0007", None, None),
]


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:44} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    p = os.path.join(d, "book.tex")
    O.write_tex(p, {"title": "Кни%га", "author": "Ав&тор", "target_lang": "ru"},
                ITEMS, {"s01.b0007": {"text": "Пояснение с 5% и $."}},
                {}, "Прим.: ", {})
    t = open(p, encoding="utf-8").read()

    # Ни один особый знак не должен остаться голым в тексте книги.
    body = t[t.index(r"\begin{document}"):]
    # Листинг идёт дословно, экранировать его нельзя и проверять незачем.
    body = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", body, flags=re.S)
    for ch, esc in (("%", r"\%"), ("&", r"\&"), ("#", r"\#"), ("$", r"\$"),
                    ("_", r"\_")):
        naked = [l for l in body.splitlines()
                 if ch in l.replace(esc, "") and not l.startswith("%")
                 and "verbatim" not in l and r"\begin" not in l
                 and r"\end" not in l and r"\href" not in l
                 and not l.endswith(r"\\")]      # строка таблицы: & — разделитель
        ok(f"знак {ch} экранирован", not naked, naked[:1])

    ok("разметка стала командами",
       r"\textbf{жирным}" in t and r"\textit{курсивом}" in t)
    ok("ссылка стала href", r"\href{" in t and "на сайт}" in t)
    ok("стихи в окружении verse", r"\begin{verse}" in t and r"\\" in t)
    ok("листинг дословно", r"\begin{verbatim}" in t and 'printf("100%%' in t)
    ok("сноска встала по месту", r"\footnote{Прим.: Пояснение" in t)
    ok("таблица со слиянием", r"\multicolumn{2}" in t)
    # Число столбцов объявляется заранее, и короткая строка ломает таблицу:
    # шапка из двух ячеек добивается пустой до трёх.
    rows = [l for l in t.splitlines() if l.endswith(r"\\")]
    ok("короткая строка добита пустой ячейкой",
       len(rows) == 2 and rows[0].count("&") == rows[1].count("&") + 1
       and rows[0].rstrip(r"\\ ").endswith("&"), rows)
    # Babel закомментирован, поэтому подписи ставим свои: иначе оглавление
    # в русской книге называется Contents.
    ok("оглавление названо на языке книги",
       r"\renewcommand{\contentsname}" in t, "")
    # В колонтитул идёт название раздела: класс book держит там имя главы, а
    # глав у нас нет, и на каждой странице висело бы «Оглавление».
    ok("колонтитул — название раздела",
       r"\markright{Глава \& первая}" in t and r"\pagestyle{bt}" in t, "")
    ok("шрифты с перебором",
       t.count(r"\IfFontExistsTF") >= 3 and "DejaVu Serif" in t)
    shutil.rmtree(d)

    print(f"\nслучаев: 15   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
