#!/usr/bin/env python3
"""Проверка отбора поправок распознавания — без обращения к модели.

Корректор правит оригинал, и ошибка тут дороже прочих: испорченное место
видно, а подменённое нет. Поэтому здесь записано, что принимается, а что
обязано быть отвергнуто.

    python3 tests/fix_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                     # noqa: E402

# (было, стало, принять ли, чем случай интересен)
CASES = [
    ("Proj ect", "Project", True, "слово разорвано пробелом"),
    ("J ANUS", "JANUS", True, "то же, прописными"),
    ("IIc realized", "He realized", True, "перепутанные буквы"),
    ("Courlety", "Courtesy", True, "то же, внутри слова"),
    ("SCIENT15T", "SCIENTIST", True, "цифры вместо букв"),
    ("Seduction by If", "Seduction by K", True, "K развалилась на две буквы"),
    ("naturallyand", "naturally and", True, "слипшиеся слова"),
    ("hitr«d iietion", "Introduction", True, "мусорный знак и порча разом"),

    ("1935", "1955", False, "цифру не угадываем: букв нет вовсе"),
    ("Fed. Proc. 74:93", "Fed. Proc. 14:93", False, "номер тома не угадываем"),
    ("cat", "dog", False, "подмена слова"),
    ("...", "…", False, "букв нет"),
    ("He went to the store and bought a loaf of bread",
     "Он пошёл в магазин", False, "переписывание, да ещё переводом"),
    ("a" * 90, "b" * 90, False, "длиннее предела — это не поправка"),
    ("same", "same", False, "менять нечего"),
]


def main():
    bad = 0
    for old, new, want, why in CASES:
        got = P.fix_ok(old, new)
        ok = got == want
        bad += not ok
        print(f"  {why:34} {'принято' if got else 'отвергнуто':11}"
              f"{'' if ok else '  РАСХОЖДЕНИЕ'}")
    print(f"\nслучаев: {len(CASES)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
