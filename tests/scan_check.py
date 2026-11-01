#!/usr/bin/env python3
"""Скан без текстового слоя, прочитанный распознаванием страниц.

Распознавание проходило, а прогон падал следом: разметка и проверка порчи
брали текст из текстового слоя, которого у скана нет. Теперь книга,
прочитанная распознаванием, обходится без них, оглавление метит сама модель,
а фраза, разорванная стыком страниц, склеивается — с переносом в любом
написании.

    python3 tests/scan_check.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types

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

from booktrans import extract as E, pipeline as P, run as R  # noqa: E402

# Книга знает «well-known» через дефис и «continued» слитно.
BOOK = "A well-known fact. It continued. Ever-green tree."
FORMS = E._word_forms(BOOK)

TOC = """[[[TOC]]]
# Contents

1. The Beginning ........ 3
2. The Machine  7
| Epilogue | 12 |
"""

PAGE2 = """# The Beginning

The first paragraph ends here.

The tool approaches the surface. As

[^1]: A note at the foot of the page."""

PAGE3 = """![image](images/a.png)

*Figure 1. The tool.*

the tool is retracted, three bonds break.

Then a new paragraph begins."""


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:58} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got!r}"))
        bad += not cond

    # Перенос в конце строки текстового слоя.
    for name, txt, want in (
            ("перенос дефисом-минусом", "con-\ntinued", "continued"),
            ("мягкий перенос", "con­\ntinued", "continued"),
            ("типографский дефис", "con‐\ntinued", "continued"),
            ("неразрывный дефис", "con‑\ntinued", "continued"),
            ("составное слово из книги — дефис остаётся", "well-\nknown", "well-known"),
            ("рядом с цифрой дефис настоящий", "1990-\n2000", "1990-2000"),
            ("звено цепочки слева", "easy-to-\nmeasure", "easy-to-measure"),
            ("звено цепочки справа", "SARS-\nCoV-2", "SARS-CoV-2")):
        got = E.dehyphen(txt, FORMS)
        ok("слой: " + name, got == want, got)

    # Незнакомое слово на разрыве — как книга рвёт строки вообще.
    epub = "A well-\nknown and ever-\ngreen, so-\ncalled fact. A well-known, evergreen\nso-called. "
    typeset = "It con-\ntinued to tra-\nvel. It continued to travel. "
    for name, txt, want in (
            ("книга рвёт строки на дефисах — оставить", epub, "frob-nicated"),
            ("книга рвёт строки по слогам — убрать", typeset, "frobnicated")):
        got = E.dehyphen(txt + "frob-\nnicated", None).split()[-1]
        ok("слой: " + name, got == want, got)

    # Стык страниц.
    for name, a, b, want in (
            ("дефис-минус", "it con-", "tinued so", "it continued so"),
            ("мягкий перенос", "it con­", "tinued so", "it continued so"),
            ("типографский дефис", "it con‐", "tinued so", "it continued so"),
            ("неразрывный дефис", "it con‑", "tinued so", "it continued so"),
            ("короткое тире вплотную", "it con–", "tinued so", "it continued so"),
            ("знак ¬", "it con¬", "tinued so", "it continued so"),
            ("составное слово — дефис остаётся", "a well-", "known one", "a well-known one"),
            ("незнакомое слово — дефис убирается", "a frob-", "nicated one", "a frobnicated one"),
            ("звено цепочки", "giving-up-meat-", "help-the planet", "giving-up-meat-help-the planet"),
            ("фраза без переноса", "bonds. As", "the tool is", "bonds. As the tool is"),
            ("тире с пробелом остаётся тире", "he said –", "and left", "he said – and left"),
            ("длинное тире вплотную", "he said—", "and left", "he said—and left"),
            ("точка в конце — не склеивать", "It ended.", "and so", None),
            ("точка перед сноской — не склеивать", "It ended.[^3]", "and so", None),
            ("заглавная буква — не склеивать", "It ended", "The next", None)):
        got = E.join_pages(a, b, FORMS)
        ok("стык: " + name, got == want, got)

    # Склейка через сноску внизу и картинку с подписью вверху, и через
    # страницу, целиком ушедшую в предыдущую.
    pages = [(1, ["Intro.", "The tool approaches. As", "[^1]: Note."]),
             (2, ["![image](images/a.png)", "*Figure 1.*", "the tool is"]),
             (3, ["retracted, bonds break.", "# Next"])]
    E._stitch_pages(pages, FORMS)
    ok("фраза собрана через сноску, картинку и страницу",
       pages[0][1][1] == "The tool approaches. As the tool is retracted, bonds break.",
       pages[0][1])
    ok("сноска, картинка и подпись остались на месте",
       pages[0][1][2] == "[^1]: Note." and pages[1][1] == ["![image](images/a.png)", "*Figure 1.*"],
       pages)
    pages = [(1, ["# A heading"]), (2, ["lowercase start."])]
    E._stitch_pages(pages, FORMS)
    ok("к заголовку не приклеивается", pages[1][1] == ["lowercase start."], pages)

    ok("названия глав из оглавления",
       E._toc_titles(TOC.split("\n", 1)[1]) == ["The Beginning", "The Machine", "Epilogue"],
       E._toc_titles(TOC.split("\n", 1)[1]))

    # Страница отката на текстовый слой — в виде ответа модели.
    real = E._pdf_text
    E._pdf_text = lambda path: ("First line\nis con-\ntinued.\n\nSecond para.\f"
                                "Page two\nends here.\n")
    try:
        got = [E.layer_page("fake-layer.pdf", n) for n in (1, 2, 3)]
    finally:
        E._pdf_text = real
    ok("страница отката: абзацы целы, перенос снят",
       got == ["First line is continued.\n\nSecond para.", "Page two ends here.", ""], got)

    # Цепочка из одного pdftotext — не распознавание: книга идёт по слою.
    def no_ocr(*_a, **_k):
        raise AssertionError("распознавание запущено")
    real_ocr, E.ocr = E.ocr, no_ocr
    said = []
    me = types.SimpleNamespace(
        args=types.SimpleNamespace(book="x.pdf", only=None, pages=None, jobs=1),
        ext=".pdf", steps=("ocr",), log=said.append, T=None, task=lambda *_: "",
        models=types.SimpleNamespace(chain=lambda _k: [types.SimpleNamespace(kind="pdftotext")]))
    try:
        go = R.Run.ocr(me)
        ok("один pdftotext в цепочке — распознавания нет", go is True and not said, said)
    except Exception as e:                                  # noqa: BLE001
        ok("один pdftotext в цепочке — распознавания нет", False, e)
    finally:
        E.ocr = real_ocr

    # Настоящий скан: страницы-картинки, текстового слоя нет.
    d = tempfile.mkdtemp()
    try:
        pdf = os.path.join(d, "scan.pdf")
        im = [Image.new("RGB", (200, 280), "white") for _ in range(3)]
        im[0].save(pdf, "PDF", save_all=True, append_images=im[1:])
        pages_dir = os.path.join(d, "scan.work", "pdf_pages")
        os.makedirs(os.path.join(pages_dir, "images"))
        Image.new("RGB", (8, 8), "red").save(os.path.join(pages_dir, "images", "a.png"))
        for n, t in enumerate((TOC, PAGE2, PAGE3), 1):
            open(os.path.join(pages_dir, f"page_{n:04d}.md"), "w", encoding="utf-8").write(t)

        try:
            E.plain_paragraphs(pdf)
            layer = True
        except E.BadBook:
            layer = False
        ok("у скана нет текстового слоя", not layer)
        with open(pdf, "ab") as f:
            f.write(b"\n# 1 0 obj\n")
        ok("pdf с решёткой в начале строки — не markdown", not E.is_markdown(pdf))
        ok("скан с распознанными страницами читается распознаванием",
           E.visual_pdf(pdf) and not E.visual_pdf(pdf.replace("scan", "none")))

        def never(*_a, **_k):
            raise AssertionError("разметчик вызван")
        me = types.SimpleNamespace(
            args=types.SimpleNamespace(book=pdf), ext=".pdf", ocr_agent=None,
            steps=(), log=lambda *a, **k: None, work=d, task=lambda *_: "",
            ask_model=None, models=types.SimpleNamespace(first=never, rest=never))
        try:
            marks = R.Run._marks(me)
            ok("разметчик не зовётся", marks is None, marks)
        except Exception as e:                              # noqa: BLE001
            ok("разметчик не зовётся", False, e)

        meta, blocks, _, _ = E.read_book(pdf)
        text = [b["text"] for b in blocks]
        ok("страница оглавления в книгу не попала",
           not any("Contents" in t or "Machine" in t for t in text), text)
        ok("фраза со стыка страниц — один абзац",
           "The tool approaches the surface. As the tool is retracted, three bonds break." in text,
           text)
        ok("сноска страницы осталась", any("A note at the foot" in t for t in text), text)
        title = next((b for b in blocks if b["kind"] == "title"), {})
        ok("глава из оглавления — второго уровня", title.get("level") == 2, title)

        seen = []
        agent = types.SimpleNamespace(run=lambda s, u: (seen.append(u), ("НАБРАН", {}))[1])
        body = "\n\n".join(b["text"] for b in blocks if b["kind"] == "p")
        try:
            made = P.ocr_check(d, pdf, agent, lambda *a, **k: None, text=body)
            ok("порча судится по блокам, а не по слою",
               made == "" and seen and "three bonds break" in seen[0], seen)
        except Exception as e:                              # noqa: BLE001
            ok("порча судится по блокам, а не по слою", False, e)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
