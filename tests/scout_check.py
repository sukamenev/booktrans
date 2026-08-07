#!/usr/bin/env python3
"""Проверка пересжатия справочника.

Справочник разведки уходит в КАЖДЫЙ запрос на перевод, поэтому его держат в
пределе. Просьба «уложиться в столько-то знаков» стоит в промпте сведения, но
исполняется плохо, и есть отдельный проход, который дожимает.

На живой книге он не справился: справочник в 53 712 знаков стал 50 146 при
пределе 24 000. Причина была не в модели, а в арифметике — раздел, где две
трети занимают биографии, а треть таблица имён, разрешалось ужать не больше
чем на треть от всего раздела разом. Даже послушайся модель дословно, вышло бы
33 437.

    python3 tests/scout_check.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402


def table(n):
    return [f"| Имя {i} | Перевод {i} | пояснение про это имя, строка длинная |"
            for i in range(n)]


def prose(n):
    return [f"**Перемены {i}.** Здесь пересказ событий, биография и голос "
            f"персонажа — то, что режется первым, потому что сюжет конвейер "
            f"помнит отдельным конспектом. Строка {i}." for i in range(n)]


# Раздел, каких в жизни большинство: треть — таблица, две трети — проза.
MIXED = "## ПЕРСОНАЖИ\n\n" + "\n".join(table(90) + prose(140)) + "\n"
PLAIN = "## ОБРАЩЕНИЯ\n\n" + "\n".join(prose(60)) + "\n"
BOOK = MIXED + "\n" + PLAIN


class Obedient:
    """Модель, которая делает ровно то, о чём просят: режет прозу до
    названного размера и не трогает ни одной строки таблицы."""

    model = "послушная"
    kind = "стенд"

    def run(self, system, user):
        want = int(re.search(r"надо около (\d+)", user).group(1))
        part = user.split("---\n\n", 1)[1]
        rows = [l for l in part.splitlines() if l.startswith("|")]
        rest = [l for l in part.splitlines() if not l.startswith("|")]
        out = list(rest)
        # Выбрасываем прозу с конца, пока не уложились.
        while len("\n".join(out + rows)) > want and len(out) > 2:
            out.pop()
        return "\n".join(out[:2] + rows + out[2:]) + "\n", \
            {"model": self.model, "cost_usd": 0}


class Greedy(Obedient):
    """Модель, которая вместо прозы вычёркивает таблицу — самый простой
    способ уложиться в предел и самый разрушительный."""

    model = "жадная"

    def run(self, system, user):
        part = user.split("---\n\n", 1)[1]
        head = part.splitlines()[:2]
        return "\n".join(head + [l for l in part.splitlines()
                                 if not l.startswith("|")][:5]) + "\n", \
            {"model": self.model, "cost_usd": 0}


def hush(m="", end="\n"):
    pass


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:50} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    tbl = sum(len(l) + 1 for l in MIXED.splitlines() if l.startswith("|"))
    # Мера берётся по прозе: таблицы стережёт отдельная проверка, и урезать их
    # заданием не просят вовсе.
    ok("таблицы в пороге не режутся",
       P._floor(MIXED, 10 ** 6) >= tbl, P._floor(MIXED, 10 ** 6))
    ok("проза уходит на две трети",
       P._floor(MIXED, 10 ** 6) == tbl + (len(MIXED) - tbl) // 3,
       P._floor(MIXED, 10 ** 6))
    ok("просят не больше, чем нужно",
       P._floor(MIXED, 100) == len(MIXED) - 100, P._floor(MIXED, 100))
    ok("раздел без таблиц ужимается сильнее",
       P._floor(PLAIN, 10 ** 6) == len(PLAIN) // 3, P._floor(PLAIN, 10 ** 6))

    import tempfile
    d = tempfile.mkdtemp()
    out = f"{d}/scout.md"

    # Системный промпт обязан быть пуст: иначе справочник уезжает на вход по
    # разу на каждый запрос, а модель, увидев его целиком, перекраивает всё.
    seen = []

    class Watch(Obedient):
        def run(self, system, user):
            seen.append(system)
            return Obedient.run(self, system, user)

    got = P._condense_scout(BOOK, [Watch()], "", 1, hush, out)
    ok("справочник не уезжает в системный промпт",
       seen and not any(x.strip() for x in seen), seen[:1])
    ok("справочник уложился в предел", len(got) <= P.SCOUT_BUDGET,
       f"{len(got)} знаков при пределе {P.SCOUT_BUDGET}")
    ok("послушная модель таблиц не трогает", P._rows(BOOK) == P._rows(got),
       f"было {len(P._rows(BOOK))}, стало {len(P._rows(got))}")
    ok("сжатое записано на диск",
       os.path.exists(out) and open(out, encoding="utf-8").read() == got)

    # Строки таблиц с исходными больше не сверяются: первой ступенью лестницы
    # стоит «выбросить очевидное», а очевидное — это как раз строки. Мерой
    # осталась длина: ответ короче половины запрошенного — не сжатие, а
    # выброшенный справочник.
    got = P._condense_scout(BOOK, [Greedy()], "", 1, hush, f"{d}/g.md")
    ok("выпотрошенный справочник отвергнут", got == BOOK,
       f"стало {len(got)} знаков вместо {len(BOOK)}")

    # Уже короткий справочник не трогаем вовсе: запрос стоит денег.
    small = "## ИМЕНА\n\n" + "\n".join(table(3)) + "\n"
    ok("короткий справочник не пересжимают",
       P._condense_scout(small, [Greedy()], "", 1, hush, f"{d}/s.md") == small)

    # Внедрённые обращения к машине. Различение делает разведка, а конвейер
    # читает её ответ: книга **про** инъекции приводит промпты примером на
    # каждой странице, и останавливать на любом упоминании значило бы не
    # переводить как раз те книги, ради которых конвейер и заводят.
    ok("чисто — перевод идёт", P.injected("## ОПАСНЫЕ МЕСТА\nINJECTED: нет\n") == [])
    ok("«не обнаружены» тоже чисто",
       P.injected("INJECTED: Не обнаружены.") == [])
    ok("без отметки не останавливаемся",
       P.injected("справочник прежней версии, отметки нет") == [])
    found = P.injected("INJECTED: 2\n- гл. 4: «Ignore all previous»\n"
                       "- гл. 9: «допиши сюда»\n\nдалее обычный текст")
    ok("места перечислены",
       found == ["гл. 4: «Ignore all previous»", "гл. 9: «допиши сюда»"], found)
    ok("нашлось, но без перечня — всё равно находка",
       P.injected("INJECTED: 1\n\nдальше") == ["1"])

    import shutil
    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: 15   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
