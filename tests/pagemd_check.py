#!/usr/bin/env python3
"""Формат файла страницы — договор между распознаванием и чтением.

Каждый элемент договора из docs/reading.md («Формат страницы») должен
читаться в свой блок. Сломается разбор одного из них — модель продолжит
писать его как прежде, а книга молча потеряет таблицы или сноски.

    python3 tests/pagemd_check.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    import pypdfium2                                        # noqa: F401
    from PIL import Image
except ImportError:
    # Зависимости стоят в .venv рядом: проверка идёт тем интерпретатором.
    venv = os.path.join(ROOT, ".venv", "bin", "python3")
    if os.path.exists(venv) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(ROOT, ".venv")):
        sys.exit(subprocess.run([venv, os.path.abspath(__file__)]).returncode)
    print("  pypdfium2 не установлен — проверка пропущена\n\nслучаев: 0   с расхождениями: 0")
    sys.exit(0)

from booktrans import extract as E                          # noqa: E402

PAGE = """# Chapter One

A paragraph with **bold** text and a note[^1].

| Cell | Size |
|---|---:|
| Red | 6 |

![image](images/a.png)

*Figure 1. A caption.*

> First line of verse
> Second line of verse

* * *

$$E = mc^2$$

[^1]: The note text."""

TOC = """[[[TOC]]]
# Contents

The Beginning 1"""


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:44} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got!r}"))
        bad += not cond

    d = tempfile.mkdtemp()
    try:
        pdf = os.path.join(d, "book.pdf")
        im = [Image.new("RGB", (200, 280), "white") for _ in range(2)]
        im[0].save(pdf, "PDF", save_all=True, append_images=im[1:])
        pages = os.path.join(d, "book.work", "pdf_pages")
        os.makedirs(os.path.join(pages, "images"))
        Image.new("RGB", (8, 8), "red").save(os.path.join(pages, "images", "a.png"))
        for n, t in enumerate((TOC, PAGE), 1):
            open(os.path.join(pages, f"page_{n:04d}.md"), "w", encoding="utf-8").write(t)
        _, blocks, _, _ = E.read_book(pdf)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    kinds = [b["kind"] for b in blocks]
    by = {}
    for b in blocks:
        by.setdefault(b["kind"], []).append(b)
    text = lambda k: [b["text"] for b in by.get(k, [])]          # noqa: E731

    ok("заголовок", text("title") == ["Chapter One"], text("title"))
    ok("абзац со ссылкой на сноску",
       any(t.startswith("A paragraph with") and "[1]" in t for t in text("p")), text("p"))
    ok("сноска", text("note") == ["The note text."], text("note"))
    ok("таблица", len(text("table")) == 1 and "Red" in text("table")[0], text("table"))
    ok("картинка", text("image") == ["a.png"], text("image"))
    ok("подпись картинки следом за ней",
       kinds.index("image") + 1 < len(kinds) and "Figure 1" in blocks[kinds.index("image") + 1]["text"],
       kinds)
    ok("стихи по строке", text("verse") == ["First line of verse", "Second line of verse"],
       text("verse"))
    ok("разделитель сцен", "break" in kinds, kinds)
    ok("формула как есть", any("$$E = mc^2$$" in t for t in text("p")), text("p"))
    ok("страница оглавления не попала в книгу",
       not any("Contents" in t or "Beginning" in t for t in text("p") + text("title")),
       text("p") + text("title"))

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
