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
    chunks = [{"index": i, "blocks": [{"id": f"s01.b{i:04d}", "kind": "p", "text": "Text."}]}
              for i in (1, 2, 3)]
    # Четвёртый кусок — выброшенный указатель: модели в нём нечего дать.
    dropped = {"index": 4, "blocks": [{"id": "s09.b0001", "kind": "verse", "text": "AKT 7",
                                      "asis": True, "drop": True}]}
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
        ok("выброшенный раздел — не недоделка",
           P.pass_gaps(work, chunks + [dropped], "ru") == gaps,
           P.pass_gaps(work, chunks + [dropped], "ru"))
        ok("законченный перевод не в списке",
           not any(g[0] == "tr" for g in gaps), gaps)

    # Обрыв правки судится по абзацам прозы. Спорная сцена: 2 правки из 41, обе
    # в начале, дальше пусто — обрыв. Предметный указатель: одна опечатка в
    # начале куска из 24 коротких строк — не обрыв, там и править нечего.
    prose = {f"s01.b{n:04d}": "Длинный абзац художественной прозы, в котором редактору всегда есть что поправить: " + "слово " * 12
             for n in range(41)}
    pid = list(prose)
    ok("обрыв: две правки в начале прозаического куска",
       P.edit_stopped({pid[0]: "а", pid[1]: "б"}, pid, prose) == 2,
       P.edit_stopped({pid[0]: "а", pid[1]: "б"}, pid, prose))
    ok("здоровый кусок: правки идут до конца",
       P.edit_stopped({pid[0]: "а", pid[20]: "б", pid[39]: "в"}, pid, prose) == 0)
    index = {f"s99.b{n:04d}": f"<i>GENE{n}</i>, мутация гена {n}" for n in range(24)}
    iid = list(index)
    ok("указатель: одна правка в начале — не обрыв",
       P.edit_stopped({iid[3]: "правка"}, iid, index) == 0, P.edit_stopped({iid[3]: "правка"}, iid, index))
    mixed = dict(prose, **index)
    ok("смешанный кусок: короткие строки в счёт не идут",
       P.edit_stopped({pid[0]: "а"}, pid + iid, mixed) == 1
       and P.edit_stopped({pid[39]: "а", iid[2]: "б"}, pid + iid, mixed) == 0)
    ok("без правок — не обрыв, а «править нечего»", P.edit_stopped({}, pid, prose) == 0)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
