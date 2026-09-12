#!/usr/bin/env python3
"""Отвергнутые написания имён вычищаются из прозы справочника.

Канон цикла подменяет строку имени, а карточки соседей помнили прежнее
написание — справочник противоречил сам себе, и переводчик шёл за
карточкой. Ищет и переписывает модель, код принимает только точечные
правки; отказ модели оставляет пары ждать следующего запуска.

    python3 tests/rename_check.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402

REF = """## CHARACTERS — Персонажи

| Coil | Койл | мужской род | Томас Калверт, работодатель Неформалов. |
| Dinah | Дина | женский род | девочка-преког, удерживаемая Спиралью и спасённая Тейлор. |
| Tattletale | Сплетница | женский род | получила имущество Спирали и его подставные личности. |

## NAMES — Имена

| spiral staircase | спиральная лестница | | в штабе. |
"""


class Model:
    def __init__(self, answer=None, boom=False):
        self.answer, self.boom, self.asked = answer, boom, []

    def run(self, system, user):
        self.asked.append(user)
        if self.boom:
            raise RuntimeError("нет связи")
        return self.answer, {"model": "стенд"}


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("пара из подмены строки",
       P._trans_pairs("| Coil | Спираль | м | карточка |", "| Coil | Койл | м; склоняется | карточка |")
       == [("Спираль", "Койл")], None)
    ok("псевдонимы — попарно",
       P._trans_pairs("| A; B | Ва; Вб | | |", "| A; B | Ва; Вэ | | |") == [("Вб", "Вэ")], None)
    ok("без перемены перевода пар нет",
       P._trans_pairs("| Coil | Койл | | старая карточка |", "| Coil | Койл | м | новая |") == [], None)
    ok("точечная правка принимается", P._local_edit(
        "| Dinah | Дина | ж | девочка, удерживаемая Спиралью и спасённая Тейлор. |",
        "| Dinah | Дина | ж | девочка, удерживаемая Койлом и спасённая Тейлор. |", 4), None)
    ok("пересказ отвергается", not P._local_edit(
        "| a | b | c | старый короткий текст здесь |", "| a | b | c | совсем другой пересказ карточки |", 4), None)
    ok("сломанная строка таблицы отвергается", not P._local_edit(
        "| a | b | c | текст про Спираль |", "| a | b | текст про Койла |", 4), None)

    d = tempfile.mkdtemp()
    sp = os.path.join(d, "scout.md")
    open(sp, "w", encoding="utf-8").write(REF)
    lines = REF.split("\n")
    dinah = next(i for i, l in enumerate(lines) if l.startswith("| Dinah"))
    tt = next(i for i, l in enumerate(lines) if l.startswith("| Tattletale"))
    m = Model(answer=f"{dinah} | {lines[dinah].replace('Спиралью', 'Койлом')}\n"
                     f"{tt} | | Tattletale | Сплетница | женский род | совершенно новая карточка про неё. |\n")
    said = []
    n = P.rename_prose(sp, [("Спираль", "Койл")], m, said.append)
    got = open(sp, encoding="utf-8").read()
    ok("модель получила пары и строки с номерами",
       len(m.asked) == 1 and "Спираль → Койл" in m.asked[0] and f"{dinah} | | Dinah" in m.asked[0], m.asked[0][:200])
    ok("точечная правка легла в справочник", "удерживаемая Койлом" in got and n == 1, got)
    ok("пересказ карточки отвергнут", "имущество Спирали" in got and any("отклонена" in s for s in said), said)
    ok("прочее не тронуто", "спиральная лестница" in got and "Томас Калверт" in got, None)
    ok("ожидающих пар не осталось", not os.path.exists(sp + ".rename.json"), None)

    open(sp, "w", encoding="utf-8").write(REF)
    boom = Model(boom=True)
    P.rename_prose(sp, [("Спираль", "Койл")], boom, said.append)
    ok("отказ модели: файл цел, пары ждут",
       open(sp, encoding="utf-8").read() == REF and json.load(open(sp + ".rename.json")) == [["Спираль", "Койл"]], None)
    m2 = Model(answer=f"{dinah} | {lines[dinah].replace('Спиралью', 'Койлом')}\n{tt} | {lines[tt].replace('Спирали', 'Койла')}\n")
    n = P.rename_prose(sp, [], m2, said.append)
    ok("следующий запуск подхватывает ожидающие пары",
       n == 2 and "имущество Койла" in open(sp, encoding="utf-8").read()
       and not os.path.exists(sp + ".rename.json"), n)
    ok("без пар модель не зовут", P.rename_prose(sp, [], m2, said.append) == 0 and len(m2.asked) == 1, None)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
