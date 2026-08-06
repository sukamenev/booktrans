#!/usr/bin/env python3
"""Проверка перекрытия промптов по языку.

Свой промпт кладут в папку по коду языка перевода, а дополнение — в файл
`.add.md` рядом. Ошибка здесь тиха вдвойне: не подхватилось перекрытие —
человек не поймёт, почему его указания не действуют; подхватилось чужое —
книга поедет по правилам не того языка.

    python3 tests/prompt_check.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import cli                                # noqa: E402

BASE = "авторский промпт\n<<<P>>>\nTERM: слово"


def build(files):
    d = tempfile.mkdtemp()
    for name, text in files.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
    return d


# (имя, файлы в prompts/, язык перевода, что должно выйти, своё ли).
CASES = [
    ("нет перекрытия", {"translate.md": BASE}, "de", [BASE], False),

    ("папка есть, файла нет",
     {"translate.md": BASE, "de/edit.md": "чужой проход"}, "de", [BASE], False),

    ("перекрыт", {"translate.md": BASE, "de/translate.md": "deutsch"},
     "de", ["deutsch"], True),

    # Папка читается только для того языка, на который переводят.
    ("чужой язык не берётся",
     {"translate.md": BASE, "de/translate.md": "deutsch"}, "es", [BASE], False),

    ("язык не задан", {"translate.md": BASE, "de/translate.md": "deutsch"},
     None, [BASE], False),

    ("дополнение к авторскому",
     {"translate.md": BASE, "de/translate.add.md": "и ещё вот так"},
     "de", [BASE, "и ещё вот так"], False),

    ("дополнение к своему",
     {"translate.md": BASE, "de/translate.md": "deutsch",
      "de/translate.add.md": "и ещё"}, "de", ["deutsch", "и ещё"], True),

    ("общее дополнение идёт всем языкам",
     {"translate.md": BASE, "translate.add.md": "для всех"},
     "es", [BASE, "для всех"], False),

    ("общее раньше языкового",
     {"translate.md": BASE, "translate.add.md": "для всех",
      "de/translate.add.md": "для немецкого"},
     "de", [BASE, "для всех", "для немецкого"], False),
]

# Знаки протокола: что должно найтись пропавшим в перекрытом промпте.
TOKENS = [
    ("всё на месте", "deutsch\n<<<P>>>\nTERM: Wort", []),
    ("метка потеряна", "deutsch\nTERM: Wort", ["<<<P"]),
    ("ярлык переведён", "deutsch\n<<<P>>>\nBEGRIFF: Wort", ["TERM:"]),
    ("потеряно всё", "deutsch ohne alles", ["<<<P", "TERM:"]),
]


def main():
    bad = 0
    for name, files, to, want, own_want in CASES:
        d = build(files)
        text, own = cli.prompt("translate", to, d)
        ok = text == "\n\n".join(want) and bool(own) == own_want
        print(f"  {name:34} {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}")
        if not ok:
            print(f"      ждали: {'|'.join(want)!r}, своё={own_want}")
            print(f"      вышло: {text!r}, своё={bool(own)}")
        bad += not ok
        shutil.rmtree(d)
    for name, over, want in TOKENS:
        d = build({"translate.md": BASE})
        got = cli.lost_tokens("translate", over, d)
        ok = got == want
        print(f"  знаки: {name:27} {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}")
        if not ok:
            print(f"      ждали {want}, вышло {got}")
        bad += not ok
        shutil.rmtree(d)
    n = len(CASES) + len(TOKENS)
    print(f"\nслучаев: {n}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
