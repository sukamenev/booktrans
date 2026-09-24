#!/usr/bin/env python3
"""Многоточия при сборке: один вид на книгу по полю `ellipsis` правил языка,
после «?» и «!» — по полю `ellipsis_after_mark`; код и формулы не трогаются.

    python3 tests/ellipsis_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import lang  # noqa: E402


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}" + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("ru: знак «…», после ?/! — две точки", lang.field("ru", "ellipsis") == "…" and lang.field("ru", "ellipsis_after_mark") == "..")
    ok("en: поля нет — сборка ничего не трогает", lang.field("en", "ellipsis") == "" and lang.ellipsis("Ну... да", "") == "Ну... да")
    e = lambda s: lang.ellipsis(s, "…", "..")
    ok("три точки → один знак", e("Ну... да. Так...") == "Ну… да. Так…", e("Ну... да. Так..."))
    ok("«?...», «?…» → «?..»; «!...» → «!..»", e("Правда?... Да?… Прочь!...") == "Правда?.. Да?.. Прочь!..", e("Правда?... Да?… Прочь!..."))
    ok("готовые «?..» и «…» не портятся", e("Правда?.. Ну…") == "Правда?.. Ну…")
    ok("четыре точки и точка после знака — не многоточие", e("Хм.... Всё?.") == "Хм.... Всё?.", e("Хм.... Всё?."))
    ok("код и формулы не трогаются", e("см. <code>a... b</code> и $x...y$, а тут...") == "см. <code>a... b</code> и $x...y$, а тут…",
       e("см. <code>a... b</code> и $x...y$, а тут..."))
    ok("теги не трогаются", e('<a href="x...y">да...</a>') == '<a href="x...y">да…</a>')
    ok("обратный выбор: знак → три точки", lang.ellipsis("Ну… да?…", "...", "..") == "Ну... да?..")
    ok("без правила после знака «?…» остаётся", lang.ellipsis("Да?… Ну...", "…") == "Да?… Ну…")

    print(f"\n{'ВСЁ СОВПАДАЕТ' if not bad else f'РАСХОЖДЕНИЙ: {bad}'} ({seen} проверок)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
