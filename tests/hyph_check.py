#!/usr/bin/env python3
"""Правила переноса едут с книгой: экспорт в TeX переносит слова и там, где
языкового пакета TeX нет.

    python3 tests/hyph_check.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import output as O                           # noqa: E402

TEXT = ("Наследственный гемохроматоз характеризуется повышенным всасыванием железа "
        "в кишечном тракте, что приводит к его накоплению в паренхиматозных клетках. ") * 3


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    for code in O.TEX_HYPH:
        t = O.hyph_tex(code)
        ok(f"правила {code}: команда babel и лицензия названа",
           "\\babelpatterns[%s]{" % O.TEX_BABEL[code] in t and "licence" in t and len(t) > 300,
           t[:120])
    ok("у русского есть и исключения", "\\babelhyphenation[russian]{" in O.hyph_tex("ru"))
    ok("английскому, японскому, китайскому файл не нужен",
       not any(O.hyph_file(c) for c in ("en", "ja", "zh", "xx")),
       [O.hyph_file(c) for c in ("en", "ja", "zh", "xx")])

    d = tempfile.mkdtemp()
    items = [("title", "Глава", "s01.b0000", None), ("p", TEXT, "s01.b0001", None)]
    p = os.path.join(d, "Книга с пробелами.tex")
    O.write_tex(p, {"title": "Кровь", "author": "Автор", "target_lang": "ru"},
                items, {}, {}, "Прим.:", {})
    tex = open(p, encoding="utf-8").read()
    ok("файл правил лёг рядом с .tex", os.path.exists(os.path.join(d, "booktrans-hyph-ru.tex")),
       os.listdir(d))
    ok("преамбула подключает его только под LuaTeX",
       r"\ifdefined\directlua\ifdefined\babelpatterns\IfFileExists{booktrans-hyph-ru.tex}" in tex,
       [ln for ln in tex.splitlines() if "hyph" in ln])
    O.write_tex(os.path.join(d, "en.tex"), {"title": "Blood", "target_lang": "en"},
                [("p", "Text.", "s01.b0001", None)], {}, {}, "Note:", {})
    ok("английской книге файл правил не пишется",
       not os.path.exists(os.path.join(d, "booktrans-hyph-en.tex")), os.listdir(d))

    if shutil.which("lualatex"):
        # Узкая колонка: без правил TeX не перенёс бы ни одного русского слова.
        narrow = tex.replace(r"\begin{document}", r"\begin{document}\hsize=5cm\textwidth=5cm\linewidth=5cm", 1)
        open(p, "w", encoding="utf-8").write(narrow)
        r = subprocess.run(["lualatex", "-interaction=nonstopmode", os.path.basename(p)],
                           cwd=d, capture_output=True, text=True, timeout=300)
        log = open(os.path.splitext(p)[0] + ".log", encoding="utf-8", errors="ignore").read()
        errs = re.findall(r"^! .*", log, re.M)
        ok("lualatex: сборка без ошибок", not errs, errs[:3])
        txt = subprocess.run(["pdftotext", "-layout", os.path.splitext(p)[0] + ".pdf", "-"],
                             capture_output=True, text=True).stdout if shutil.which("pdftotext") else ""
        n = len(re.findall(r"[а-яё][-‐]\n", txt))
        if txt:
            ok("lualatex: русские слова переносятся", n >= 3, n)
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
