#!/usr/bin/env python3
"""Проверка подстановки переводов в листинги — без обращения к модели.

Комментарии в листингах ищет модель: знаков комментария у языков сотни.
Подставляет перевод программа, и вот её-то ошибка тиха и дорога — в книгу
попадёт сломанный код. Поэтому здесь записано, что подставиться обязано, а
что обязано быть отвергнуто, в том числе при негодном ответе модели.

    python3 tests/code_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import code as C                          # noqa: E402

PYTHON = '''def total(rows):
    # add up the prices of every row
    s = 0
    for r in rows:          # walk the table, one row at a time
        s += r.price
    print("add up the prices of every row")
    return s'''

SHELL = '''#!/bin/sh
# collect yesterday's logs and hand them to the parser
for f in /var/log/app/*.log; do
    grep -v DEBUG "$f" | ./parse.sh   # debug lines only get in the way
done'''

MATLAB = '''% adds up every price in the table
for k = 1:numel(p)
  s = s + p(k);   % one more price to the total
end'''

LISP = ''';; walk the whole table and add the prices up
(defun total (rows)
  (reduce #'+ (mapcar #'price rows)))'''

# (имя, листинг, что ответила модель, сколько обязано подставиться)
CASES = [
    ("обычный случай", PYTHON, [
        (2, "add up the prices of every row", "складываем цены всех строк"),
        (4, "walk the table, one row at a time", "идём по таблице, строка за строкой"),
    ], 2),
    ("строковый литерал", PYTHON, [
        (6, "add up the prices of every row", "складываем цены"),
    ], 0),
    ("кусок кода", PYTHON, [
        (3, "s = 0", "с = 0"),
        (5, "s += r.price", "с += r.цена"),
    ], 0),
    ("строки нет", PYTHON, [(99, "nonexistent comment here", "чепуха")], 0),
    ("не сошлось до знака", PYTHON, [
        (2, "add up the prices of every ROW", "складываем цены"),
    ], 0),
    ("маска файлов", SHELL, [
        (2, "collect yesterday's logs and hand them to the parser",
            "собираем вчерашние логи и отдаём разборщику"),
        (4, "debug lines only get in the way", "отладочные строки только мешают"),
    ], 2),
    ("знак нам незнаком", MATLAB, [
        (1, "adds up every price in the table", "складываем все цены таблицы"),
        (3, "one more price to the total", "ещё одна цена в сумму"),
    ], 2),
    ("лисп", LISP, [
        (1, "walk the whole table and add the prices up",
            "идём по всей таблице и складываем цены"),
    ], 1),
    ("ярлык без слов", PYTHON, [(2, "TODO", "СДЕЛАТЬ")], 0),
]


def main():
    bad = 0
    for name, listing, said, want in CASES:
        new, n = C.splice(listing, said)
        ok = n == want and len(new.split("\n")) == len(listing.split("\n"))
        # Всё, чего модель не называла, обязано остаться слово в слово.
        touched = [a for a, b in zip(listing.split("\n"), new.split("\n")) if a != b]
        if len(touched) > len(said):
            ok = False
        print(f"  {name:22} {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"   подставлено {n}, ожидалось {want}")
        if not ok:
            for a, b in zip(listing.split("\n"), new.split("\n")):
                if a != b:
                    print(f"      было:  {a!r}\n      стало: {b!r}")
        bad += not ok
    print(f"\nслучаев: {len(CASES)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
