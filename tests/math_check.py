#!/usr/bin/env python3
"""Формулы в собранной книге: простая — текстом, сложная — картинкой.

Распознавание заворачивает в доллары и греческую букву, и индекс. Такое
ложится в текст; картинкой остаётся то, чего строкой не набрать. Служебная
метка картинки в книгу не попадает ни в каком виде.

    python3 tests/math_check.py
"""
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import output as O                           # noqa: E402

META = {"title": "Кровь", "author": "Автор", "target_lang": "ru"}


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    for tex, want in ((r"\beta", "β"), (r"B_{12}", "B<sub>12</sub>"),
                      (r"5 \times 10^{9}/\text{L}", "5 × 10<sup>9</sup>/L"),
                      (r"37^\circ\text{C}", "37°C"), (r"\geq 20\%", "≥ 20%"),
                      (r"BCR::ABL1", "<i>BCR::ABL1</i>"), (r"Fe^{2+}", "Fe<sup>2+</sup>"),
                      (r"\alpha_2\beta_2", "α<sub>2</sub>β<sub>2</sub>")):
        ok(f"текстом: {tex}", O.tex_inline(tex) == want, O.tex_inline(tex))
    for tex in (r"\frac{a}{b}", r"\sqrt{x}", r"\sum_{i=1}^n x_i", r"x^{2", r"\unknowncmd"):
        ok(f"не текстом: {tex}", O.tex_inline(tex) is None, O.tex_inline(tex))

    items = [("title", "Глава", "s01.b0000", None),
             ("p", r"При $\beta$-талассемии уровень $B_{12}$ выше $5 \times 10^{9}$/л.",
              "s01.b0001", None),
             ("p", r"Доля равна $\frac{a}{b}$ от нормы.", "s01.b0002", None)]
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.epub")
    O.write_epub(p, META, items, {}, {}, "Прим.:", {})
    z = zipfile.ZipFile(p)
    body = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))
    ok("epub: простая формула — текстом",
       "β-талассемии" in body and "B<sub>12</sub>" in body and "10<sup>9</sup>" in body, body[-400:])
    ok("epub: служебной метки в книге нет", "imgmath" not in body, body[-400:])
    try:
        import pypdfium2                                    # noqa: F401
        can_draw = bool(shutil.which("lualatex"))
    except ImportError:
        can_draw = False
    if can_draw:
        pics = [n for n in z.namelist() if "math_" in n]
        ok("epub: сложная формула — картинкой, и картинка в книге",
           len(pics) == 1 and f'<img src="img/{os.path.basename(pics[0])}"' in body,
           (pics, body[-300:]))
    else:
        ok("epub: рисовать нечем — сложная формула остаётся в долларах", r"\frac{a}{b}" in body, body[-300:])
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
