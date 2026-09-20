#!/usr/bin/env python3
"""Картинки внутри абзаца и таблицы при разборе распознанных страниц.

Абзац рвался вокруг картинки на куски с одним идентификатором: таблица с
рисунками в ячейках дала девять блоков `s32.b0005`, перевод хранил один из
них, и кусок переводился заново при каждом запуске.

    python3 tests/pdfimg_check.py
"""
import collections
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

PAGE = """# Глава

Вводный абзац.

| Cell | Diameter | Function |
|---|---:|---|
| Red cells<br>![image](images/a.png) | 6–8 | Oxygen transport |
| Platelets<br>![image](images/b.png) | 0.5–3.0 | Haemostasis |

![image](images/c.png)

*Figure 1. Подпись под рисунком.*

Начало фразы ![image](images/a.png) и её конец.

Последний абзац.
"""


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    pdf = os.path.join(d, "book.pdf")
    Image.new("RGB", (200, 280), "white").save(pdf, "PDF")
    pages = os.path.join(d, "book.work", "pdf_pages")
    os.makedirs(os.path.join(pages, "images"))
    for n in "abc":
        Image.new("RGB", (8, 8), "red").save(os.path.join(pages, "images", f"{n}.png"))
    open(os.path.join(pages, "page_0001.md"), "w", encoding="utf-8").write(PAGE)
    got = E._pdf_visual(pdf, None)
    blocks = next(x for x in got if isinstance(x, list) and x and isinstance(x[0], dict))
    ids = collections.Counter(b["id"] for b in blocks)
    ok("идентификаторы блоков не повторяются", max(ids.values()) == 1,
       [k for k, v in ids.items() if v > 1])
    tab = [b for b in blocks if b["kind"] == "table"]
    ok("таблица с картинками в ячейках — один блок-таблица",
       len(tab) == 1 and "Red cells" in tab[0]["text"] and "Platelets" in tab[0]["text"]
       and "![" not in tab[0]["text"] and "<br>" not in tab[0]["text"], [b["text"] for b in tab])
    seq = [(b["kind"], b["text"][:22]) for b in blocks]
    at = seq.index(("table", tab[0]["text"][:22])) if tab else -1
    ok("картинки ячеек идут следом за таблицей",
       [k for k, _ in seq[at + 1:at + 3]] == ["image", "image"], seq)
    cap = next((i for i, (k, t) in enumerate(seq) if "Figure 1" in t), -1)
    ok("картинка перед подписью осталась перед ней", cap > 0 and seq[cap - 1][0] == "image", seq)
    whole = [b for b in blocks if b["kind"] == "p" and "Начало фразы" in b["text"]]
    ok("фраза вокруг картинки не разорвана",
       len(whole) == 1 and "и её конец" in whole[0]["text"], [b["text"] for b in whole])
    nums = [b["id"] for b in blocks if b["kind"] in ("p", "table", "title")]
    ok("номера абзацев идут подряд, без дыр", nums == sorted(nums) and len(nums) == 6, nums)
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
