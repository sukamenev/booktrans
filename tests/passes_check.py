#!/usr/bin/env python3
"""Полнота проходов в итоге прогона.

Кусок без файла правки (очередь редакторов опустела) или с отметкой обрыва
— недоделка, и прогон должен сказать о ней в конце; проход без единого
файла не начинался и недоделкой не считается.

    python3 tests/passes_check.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("диапазоны для --chunks", P.chunk_ranges([10, 9, 1, 2, 3, 5]) == "1-3,5,9-10",
       P.chunk_ranges([10, 9, 1, 2, 3, 5]))
    chunks = [{"index": i} for i in (1, 2, 3)]
    with tempfile.TemporaryDirectory() as work:
        def put(sub, i, **extra):
            d = f"{work}/ru/{sub}"
            os.makedirs(d, exist_ok=True)
            json.dump({"index": i, **extra}, open(f"{d}/{i:04d}.json", "w"))
        for i in (1, 2, 3):
            put("tr", i)
        ok("без правки вовсе — не недоделка", P.pass_gaps(work, chunks, "ru") == [])
        put("ed", 1)
        put("ed", 2, stopped_at="s1.b0005")
        put("vf", 1)
        gaps = P.pass_gaps(work, chunks, "ru")
        ok("правка: без файла и с обрывом — без результата",
           ("ed", 1, 3, [2, 3]) in gaps, gaps)
        ok("сверка считается отдельно", ("vf", 1, 3, [2, 3]) in gaps, gaps)
        ok("законченный перевод не в списке",
           not any(g[0] == "tr" for g in gaps), gaps)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
