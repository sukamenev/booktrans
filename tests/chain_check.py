#!/usr/bin/env python3
"""Проверка цепочки моделей: подхватывает ли запасная.

Запасная модель задаётся ради одного случая — когда первая не справилась.
Случаев «не справилась» два: отказ (модель ответила, но работу не сделала) и
сбой поставщика (502, «high traffic», оборванная связь). Первый переживали
все проходы, второй — только некоторые: он летел мимо цепочки и валил прогон,
хотя запасная модель стояла рядом и была свободна.

Здесь проверяется общая функция и то, что каждый проход умеет её кормить.

    python3 tests/chain_check.py
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402
from booktrans.agent import AgentError, Fatal               # noqa: E402

# Каждый проход, который обращается к модели, обязан принимать цепочку.
PASSES = ("translate", "edit", "notes", "scout", "code_comments", "headings",
          "detect_structure", "format_marks", "fix_ocr", "condense")


class Says:
    """Отвечает, что велено, или падает тем, чем велено."""

    def __init__(self, model, boom=None, answer="готово"):
        self.model, self.boom, self.answer = model, boom, answer
        self.calls = 0

    def run(self, system, user):
        self.calls += 1
        if self.boom:
            raise self.boom
        return self.answer, {"model": self.model, "cost_usd": 0}


def main():
    bad = 0
    said = []

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    def log(m="", end="\n"):
        said.append(m)

    plain = lambda o: (o, "")                               # noqa: E731

    # Сбой поставщика — повод взять следующую модель, а не кончить прогон.
    first = Says("первая", boom=AgentError("agy вернул 1: high traffic"))
    second = Says("вторая")
    (res, _), meta, _ = P._chain_run([first, second], "", "п", 2, plain, log)
    ok("сбой поставщика уходит следующей модели",
       res == "готово" and meta["model"] == "вторая", meta)
    ok("о переходе сказано вслух",
       any("вторая" in m for m in said), said)

    # Отказ в доступе — тем более: у следующей модели доступ может быть.
    first = Says("первая", boom=Fatal("model not found"))
    second = Says("вторая")
    (res, _), _, _ = P._chain_run([first, second], "", "п", 1, plain, log)
    ok("отказ в доступе уходит следующей модели", res == "готово", res)

    # Первая справилась — до второй дело не доходит, деньги не тратятся.
    first, second = Says("первая"), Says("вторая")
    P._chain_run([first, second], "", "п", 1, plain, log)
    ok("справилась первая — вторую не зовут",
       first.calls == 1 and second.calls == 0, (first.calls, second.calls))

    # Упали все — ошибка наружу. Молча вернуть пустое нельзя: пустой ответ
    # запишется как сделанная работа.
    try:
        P._chain_run([Says("а", boom=AgentError("502")),
                      Says("б", boom=AgentError("502"))], "", "п", 1, plain, log)
        ok("упала вся цепочка — ошибка наружу", False, "молчание")
    except RuntimeError:      # `_run` отдаёт «исчерпаны попытки»
        ok("упала вся цепочка — ошибка наружу", True)

    # Порядок значим: первая делает работу, остальные подхватывают.
    who = [Says("а", boom=AgentError("502")), Says("б", boom=AgentError("502")),
           Says("в")]
    (res, _), meta, _ = P._chain_run(who, "", "п", 1, plain, log)
    ok("идут по цепочке до первой, которая возьмётся",
       meta["model"] == "в" and [w.calls for w in who] == [1, 1, 1],
       [w.calls for w in who])

    for name in PASSES:
        fn = getattr(P, name, None)
        ok(f"проход {name} принимает цепочку",
           fn is not None and "fallback" in inspect.signature(fn).parameters,
           "нет такого прохода" if fn is None else "нет параметра fallback")

    print(f"\nслучаев: {6 + len(PASSES)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
