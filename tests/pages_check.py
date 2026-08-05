#!/usr/bin/env python3
"""Проверка снятия колонтитулов и номеров страниц — без обращения к модели.

Правило удаляет строки из книги, и ошибка в нём тиха: пропавший абзац
заметят не сразу. Поэтому здесь записано и то, что обязано сниматься, и то,
что трогать нельзя.

Названия книги правило не знает и не спрашивает: колонтитул опознаётся тем,
что стоит с краю страницы и повторяется. Сокращённое название, полное или
вовсе иное — разницы нет, и случаи ниже это показывают.

    python3 tests/pages_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                       # noqa: E402

REPEAT = "The tank held him weightless and the light went out."
WORDS = ("silence water ceiling breath dolphin number heartbeat ledge "
         "morning copper thunder lantern harbour").split()


def body(i, k):
    """Строка авторского текста, своя на каждой странице.

    Различаются они словами, а не числами нарочно: ключ, по которому ищется
    повтор, цифры отбрасывает — иначе «THE SCIENTIST 127» и «…128» считались
    бы разными строками.
    """
    return (f"He noticed the {WORDS[i % len(WORDS)]} and the {k} "
            f"{WORDS[-i % len(WORDS)]} of page {WORDS[(i * 5) % len(WORDS)]}.")


def pages(head, n=12, num=True, foot=None, extra=None):
    """Книга из n страниц: колонтитул сверху, номер снизу.

    Текст на каждой странице свой, а одна и та же фраза повторяется в
    середине — правило смотрит только на две строки с каждого края и трогать
    её не должно. `extra` — страница, вставляемая перед книгой (оглавление).
    """
    out = [extra] if extra else []
    for i in range(1, n + 1):
        lines = []
        h = head(i) if callable(head) else head
        if h:
            lines.append(" " * 20 + h)
        lines += ["    " + body(i, "first"), body(i, "second"), REPEAT,
                  body(i, "third"), body(i, "fourth")]
        if foot:
            lines.append(foot)
        if num:
            lines.append(" " * 34 + str(i + 6))
        out.append("\n".join(lines))
    return "\f".join(out)


SHORT = "THE SCIENTIST"
FULL = "The Scientist: A Metaphysical Autobiography and Other Writings on the Mind"
CHAPTER = (lambda i: "Two Beliefs" if i <= 6 else "Simulation and Experience")

# Колонтитул главы в книге на сорок страниц: стоит на пяти своих и больше
# нигде. Доли по книге ему не набрать никогда — берётся он тем, что идёт
# подряд.
MANY = 40
LOCAL = (lambda i: "Weaning" if 6 <= i <= 10 else
         ("Two Beliefs" if 20 <= i <= 24 else None))

# Тот же колонтитул, испорченный распознаванием по-разному на каждой странице:
# буквы взяты из живой книги, там «Becoming» вышло четырьмя разными словами.
DIRT = ["Education into Becoming Human", "Education into Beconiing Human",
        "Education into Bectmiing Human", "Education into Becomitig Human",
        "Education into Becoming Human"]
DIRTY = (lambda i: DIRT[i - 6] if 6 <= i <= 10 else None)

# Страница оглавления: столбец названий с прижатым номером. Ключ у такой
# строки тот же, что у колонтитула, и снять её ничего не стоит — а без неё
# главы не расставить.
CONTENTS = ("\n".join(f"{n}: {t}" + " " * 20 + str(20 + n * 4) for n, t in
            enumerate(["Weaning", "Two Beliefs", "Suckling", "Transition",
                       "Hyperspace", "Major Transitions"], 1)))

# Страницы 2 и 3 есть и в книге из трёх страниц.
KEEP = [body(2, "first"), body(3, "fourth"), REPEAT]

# (имя, текст, что обязано исчезнуть, что обязано остаться).
# Повтор в середине страницы (REPEAT) остаётся во всех случаях: правило
# смотрит только на края.
CASES = [
    ("сокращённое название", pages(SHORT), [SHORT, "\n13"], KEEP),
    ("полное название", pages(FULL), [FULL, "\n13"], KEEP),
    ("название главы", pages(CHAPTER),
     ["Two Beliefs", "Simulation and Experience"], KEEP),
    ("колонтитул снизу", pages(None, foot=SHORT), [SHORT, "\n13"], KEEP),
    ("без колонтитула, номера", pages(None), ["\n13"], KEEP),
    ("ни того ни другого", pages(None, num=False), [], KEEP),
    # Мало страниц — правило молчит: на трёх повтор ещё не довод.
    ("книга в три страницы", pages(SHORT, n=3), [], KEEP + [SHORT]),
    # Колонтитул главы на пяти страницах из сорока: доля по книге — 12%,
    # порога в 15% ему не взять, а снимать его надо.
    ("колонтитул главы, редкий", pages(LOCAL, n=MANY),
     ["Weaning", "Two Beliefs"], KEEP),
    # Та же строка, но повторяется в разных концах книги — это не колонтитул.
    ("повтор вразброс", pages(lambda i: "Weaning" if i % 9 == 0 else None,
                              n=MANY), [], KEEP + ["Weaning"]),
]

# Случаи, где текст грязный: распознавание портит колонтитул каждый раз
# по-своему, и ни один вариант сам по себе до порога не дотягивает.
DIRTY_CASES = [
    ("искажённый колонтитул", pages(DIRTY, n=MANY), DIRT, KEEP),
    ("оглавление не трогаем", pages(LOCAL, n=MANY, extra=CONTENTS),
     ["Weaning\n", "Two Beliefs\n"], KEEP + ["1: Weaning", "6: Major Transitions"]),
]


def main():
    bad = 0
    for name, txt, gone, kept in CASES + DIRTY_CASES:
        out = E._strip_running(txt, dirty=(name, txt, gone, kept) in DIRTY_CASES)
        wrong = [s for s in gone if s in out] + [s for s in kept if s not in out]
        print(f"  {name:24} {'совпадает' if not wrong else 'РАСХОЖДЕНИЕ'}")
        for s in wrong:
            was = "осталось" if s in out else "пропало"
            print(f"      {was}: {s[:60]!r}")
        bad += bool(wrong)
    print(f"\nслучаев: {len(CASES) + len(DIRTY_CASES)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
