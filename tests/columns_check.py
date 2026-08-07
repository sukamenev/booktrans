#!/usr/bin/env python3
"""Проверка распознавания книг, свёрстанных в колонки.

Pdf читается через `pdftotext -layout`: он сохраняет расположение на листе, и
на этом держится снятие колонтитулов. Но на книге в две-три колонки он же
склеивает в одну строку куски соседних колонок, и абзаца в блоке не остаётся:

    Thriving in the Workplace area. They can also save money without the need
    to relocate     companies may even provide an allowance to purchase

Переводить такое бессмысленно, а видно это не сразу: текст выглядит связным
ровно настолько, чтобы модель взялась и дописала недостающее от себя. На живом
учебнике так вышло у половины блоков.

    python3 tests/columns_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                          # noqa: E402


def page(lines):
    return "\n".join(lines)


PROSE = page([
    "     The emergence of remote work means companies no longer need to",
    "rely on finding talent in their local geographic area. They can also",
    "save money without the need to relocate recruits or provide office",
    "space. As an increasing number of companies offer the opportunity to",
    "     Union participation rates in the United States peaked in the",
    "mid-1950s, when approximately 35 percent of all wage and salary",
    "workers belonged to a union. Over the decades that followed, that",
    "share fell steadily, and by the turn of the century it had halved.",
    "     Managers begin planning by inventorying the data they already",
    "have on hand, and only then look at what the coming year demands.",
])

# Две колонки: справа от провала идёт проза, а не номер страницы.
COLUMNS = page([
    "  The emergence of remote work means companies no lon-        • Make sure you have the equipment you need. This is",
    "  ger need to rely on finding talent in their local geographic  something you can ask before you start the job. Some",
    "  area. They can also save money without the need to relocate  companies may even provide an allowance to purchase",
    "  recruits or provide office space. As an increasing number    an ergonomic chair or other equipment.",
    "  of companies offer the opportunity to work remotely, you    • If you are working from home, make sure you have a",
    "  will likely see more remote jobs available, giving you the   designated workspace. That will help you transition",
    "  chance to work from anywhere in the world. In fact, some     into work mode, and you can walk away at the end",
    "  countries are even offering digital nomad visas to attract   of the day. A corner of the kitchen table will do.",
    "  remote workers. These special visas allow you to work        • Set the hours you are available and say them out loud",
    "  remotely from within a country, typically for up to a year.   to the people you work with, so nobody has to guess.",
])

# Оглавление: тот же провал, но справа номер страницы. Колонкой не считается —
# иначе книга из-за одного оглавления читалась бы вся не тем способом.
CONTENTS = page([
    "  Information Controls                                    478",
    "  Balanced Scorecard                                       479",
    "  Benchmarking of Best Practices                           479",
    "Contemporary Issues in Control                             480",
    "  Global Differences in Control                            480",
    "  Workplace Privacy and Employee Monitoring                481",
    "  Employee Theft and Fraud Prevention                      483",
    "  Workplace Violence and Its Aftermath                     485",
    "  Controlling Customer Interactions and Service            487",
    "  Corporate Governance and Its Discontents                 489",
])

# Таблица: справа от провала числа и одиночные слова.
TABLE = page([
    "  Year                    Revenue        Staff        Country",
    "  1992                    343 000        1 200        Denmark",
    "  1997                    511 400        1 850        Denmark",
    "  2003                    980 100        3 400        Germany",
    "  2009                  1 204 000        5 100        Germany",
    "  2015                  2 337 900        8 720        Ireland",
    "  2018                  3 010 500       11 040        Ireland",
    "  2021                  4 392 100       13 900        Poland",
    "  2023                  5 118 000       15 300        Poland",
    "  2024                  5 402 700       15 880        Poland",
])

SHORT = page(["  Chapter 7", "", "  Managing Social Responsibility"])


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:50} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("две колонки опознаются", E._columned(COLUMNS))
    ok("сплошная проза — не колонки", not E._columned(PROSE))
    ok("оглавление — не колонки", not E._columned(CONTENTS))
    ok("таблица — не колонки", not E._columned(TABLE))
    ok("страница в три строки не судится", not E._columned(SHORT))

    # Решение принимается по книге целиком: разметка абзацев определяется по
    # всему тексту сразу, и смешивать страницы двух видов нельзя.
    many = "\f".join([COLUMNS] * 7 + [PROSE] * 3)
    few = "\f".join([COLUMNS] * 1 + [PROSE] * 19)
    ok("книга в колонках читается иначе", E._multicolumn(many))
    ok("одна колоночная страница книгу не меняет", not E._multicolumn(few))
    ok("книга из оглавлений и таблиц не в колонках",
       not E._multicolumn("\f".join([CONTENTS, TABLE] * 5)))
    ok("пустой текст не роняет", not E._multicolumn(""))
    # Титулы и страницы под картинку в счёт не идут: их короткие строки
    # сдвинули бы долю в любую сторону.
    ok("короткие страницы в знаменатель не входят",
       E._multicolumn("\f".join([COLUMNS] * 2 + [SHORT] * 30 + [PROSE] * 8)))

    print(f"\nслучаев: 10   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
