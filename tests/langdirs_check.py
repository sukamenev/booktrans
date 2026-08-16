#!/usr/bin/env python3
"""Проверка языковых папок: одна книга — несколько языков.

Разбор оригинала, разметка и картинки языка не имеют и стоят дороже всего
после самого перевода, поэтому лежат общими. А перевод, редактура, сноски,
справочник и конспект у каждого языка свои: `tr_ru` и `tr_de` рядом.

Отдельно — папки прежнего вида, без имени языка: пока своей, с суффиксом,
нет, работа берётся из них. Так продолжают читаться книги, переведённые
прежними выпусками.

    python3 tests/langdirs_check.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                        # noqa: E402

RU = {"s01.b0001": "Он спустился в подвал и подцепил стрелку компаса.",
      "s01.b0002": "Потом смотрел, как она вертится, и молчал."}


def work(sub="tr", names=RU):
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/{sub}")
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0, "footnotes": [],
               "tr": names}, open(f"{d}/{sub}/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    return d


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # Имена: папка и файл получают суффикс, расширение остаётся последним.
    for name, to, want in (("tr", "ru", "tr_ru"), ("ed", "de", "ed_de"),
                           ("state.json", "ru", "state_ru.json"),
                           ("scout.md", "uk", "scout_uk.md"),
                           ("scout.part.json", "ru", "scout.part_ru.json"),
                           ("tr", "", "tr")):
        got = os.path.basename(P.lpath("/w", name, to))
        ok(f"{name} + {to or 'без языка'} → {want}", got == want, got)

    # Имени с суффиксом ещё нет, а без суффикса есть — берём его: так
    # читаются папки прежних выпусков, и переводить в них заново нечего.
    d = work()
    tr, _ = P.all_translations(d, "ru")
    ok("папка без суффикса читается", tr == RU, tr)
    ok("путь ведёт к ней же", os.path.basename(P.lpath(d, "tr", "ru")) == "tr",
       P.lpath(d, "tr", "ru"))
    os.makedirs(f"{d}/tr_ru")
    ok("но своя папка сильнее", os.path.basename(P.lpath(d, "tr", "ru")) == "tr_ru",
       P.lpath(d, "tr", "ru"))
    shutil.rmtree(d, ignore_errors=True)

    # Два языка в одной папке не мешают друг другу.
    d = work("tr_ru")
    os.makedirs(f"{d}/tr_de")
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0, "tr":
               {"s01.b0001": "Er ging in den Keller hinunter."}},
              open(f"{d}/tr_de/0001.json", "w", encoding="utf-8"), ensure_ascii=False)
    ru, _ = P.all_translations(d, "ru")
    de, _ = P.all_translations(d, "de")
    ok("языки не мешают друг другу",
       ru == RU and list(de) == ["s01.b0001"], (len(ru), len(de)))
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
