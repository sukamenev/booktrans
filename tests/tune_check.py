#!/usr/bin/env python3
"""Проверка файла настроек.

Числа конвейера собраны в `tune.py`, а свои значения человек кладёт в
`tune.conf`. Опасность тут одна и тихая: правка, которая не доехала. Модули
разбирают имена из `tune` при импорте, поэтому файл должен читаться раньше
них, а имя не из списка — молча пропускаться, но не менять ничего другого.

    python3 tests/tune_check.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import tune                                  # noqa: E402


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # Значения по умолчанию разбираются модулями по именам: пропажа имени —
    # это ImportError у половины конвейера, и лучше узнать о ней здесь.
    need = ("TARGET_WORDS", "MAX_WORDS", "MAX_BLOCKS", "SKIP_MAX", "REFS_RUN",
            "NOTE_GAP", "WINDOW", "FAIL_PAUSE", "RETRY_PAUSE", "CAPTION",
            "TEX_HEAD", "MIN_SHARE", "FIX_NEAR", "COL_PAGES")
    ok("все имена на месте", all(hasattr(tune, n) for n in need),
       [n for n in need if not hasattr(tune, n)])

    # Разбор строки: целое, дробное, набор.
    for text, want in (("SKIP_MAX = 120", 120), ("FIX_NEAR = 0.5", 0.5),
                       ("SKIP_MAX=120   # с комментарием", 120)):
        was = tune.SKIP_MAX if "SKIP" in text else tune.FIX_NEAR
        name = "SKIP_MAX" if "SKIP" in text else "FIX_NEAR"
        d = tempfile.mkdtemp()
        p = os.path.join(d, "tune.conf")
        open(p, "w", encoding="utf-8").write(text + "\n")
        ch = tune.load(p)
        got = getattr(tune, name)
        ok(f"прочитано: {text[:28]}", got == want and name in ch, got)
        setattr(tune, name, was)

    d = tempfile.mkdtemp()
    p = os.path.join(d, "tune.conf")
    open(p, "w", encoding="utf-8").write("FAIL_PAUSE = (30, 90)\n")
    was = tune.FAIL_PAUSE
    tune.load(p)
    ok("набор чисел разбирается", tune.FAIL_PAUSE == (30, 90), tune.FAIL_PAUSE)
    tune.FAIL_PAUSE = was

    # Чужое имя не заводит настройки: опечатка не должна менять поведение
    # втихую, но и валить прогон из-за строки в конфиге незачем.
    open(p, "w", encoding="utf-8").write(
        "SKIP_MAXX = 999\nвообще не строка настроек\nWINDOW = не число\n")
    was = dict(WINDOW=tune.WINDOW)
    ch = tune.load(p)
    ok("чужое имя и мусор пропускаются",
       ch == {} and tune.WINDOW == was["WINDOW"], (ch, tune.WINDOW))

    ok("нет файла — нет и правок", tune.load(os.path.join(d, "нет.conf")) == {})

    print(f"\nслучаев: 7   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
