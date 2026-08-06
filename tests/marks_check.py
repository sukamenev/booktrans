#!/usr/bin/env python3
"""Проверка разметки книг без разметки: цепочка моделей и продолжение.

Два случая, оба стоили целого прогона на толстой книге. Отказ модели разметку
переживала — она приходит пустым ответом, — а сбой поставщика (502) летел
наружу мимо запасной модели. И до конца прохода ничего не сохранялось: падение
на девяностом окне из девяноста четырёх стоило всех девяноста.

    python3 tests/marks_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import format as F                           # noqa: E402
from booktrans.agent import AgentError, Fatal               # noqa: E402

PARAS = [f"Абзац номер {i}." for i in range(1, 3 * F.WINDOW + 1)]


def hush(msg="", end="\n"):
    pass


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:48} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # Первая модель падает на каждом окне, вторая размечает.
    seen = []

    def flaky(body, k):
        seen.append(k)
        if k == 0:
            raise AgentError("agy вернул 1: Error 502 (Server Error)")
        return f"{body.splitlines()[0].split()[0]} title\n"

    marks, _ = F.plan(PARAS, flaky, hush, tries=2)
    ok("сбой поставщика уходит запасной модели",
       marks == {1: "title", F.WINDOW + 1: "title", 2 * F.WINDOW + 1: "title"},
       marks)
    ok("к запасной обращаются на каждом окне", seen.count(1) == 3, seen)

    # Отказ доступа — тот же случай: следующая модель нередко у другого
    # поставщика, и у неё доступ есть.
    def denied(body, k):
        if k == 0:
            raise Fatal("model not found")
        return f"{body.splitlines()[0].split()[0]} skip\n"

    marks, _ = F.plan(PARAS[:F.WINDOW], denied, hush, tries=2)
    ok("отказ в доступе тоже уходит дальше", marks == {1: "skip"}, marks)

    # Упала вся цепочка — ошибку видно, молча размечать нечем.
    def dead(body, k):
        raise AgentError("agy вернул 1: Error 502")

    try:
        F.plan(PARAS, dead, hush, tries=2)
        ok("вся цепочка упала — ошибка наружу", False, "молчание")
    except AgentError:
        ok("вся цепочка упала — ошибка наружу", True)

    # Прогон прервался на втором окне: сделанное сохранено, продолжение
    # начинается с третьего и не переспрашивает про первые два.
    saved = {}

    def keep(marks, toc, done):
        saved.update(marks=dict(marks), toc=list(toc), done=done)

    asked = []

    def dies(body, k):
        first = int(body.splitlines()[0].split()[0])
        asked.append(first)
        if first > 2 * F.WINDOW:
            raise AgentError("agy вернул 1: Error 502")
        return f"{first} title\n<<<TOC>>>\nГлава {first}\n"

    try:
        F.plan(PARAS, dies, hush, tries=1, save=keep)
    except AgentError:
        pass
    ok("сделанное сохранено до падения",
       saved.get("done") == 2 * F.WINDOW and len(saved["marks"]) == 2, saved)

    asked.clear()

    def alive(body, k):
        first = int(body.splitlines()[0].split()[0])
        asked.append(first)
        return f"{first} title\n"

    marks, toc = F.plan(PARAS, alive, hush, tries=1,
                        resume=(saved["marks"], saved["toc"], saved["done"]),
                        save=keep)
    ok("продолжение спрашивает только про несделанное",
       asked == [2 * F.WINDOW + 1], asked)
    ok("сделанное прежде осталось в разметке",
       marks == {1: "title", F.WINDOW + 1: "title", 2 * F.WINDOW + 1: "title"},
       marks)
    ok("оглавление прежнего прогона не потерялось",
       toc == [f"Глава {n}" for n in (1, F.WINDOW + 1)], toc)

    print(f"\nслучаев: 8   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
