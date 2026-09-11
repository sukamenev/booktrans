#!/usr/bin/env python3
"""Подтверждение «не переводить» моделью разметки.

Правила находят кандидатов, модель решает: «текст» снимает пометку с
ряда, «справочный» оставляет, сбой или невнятный ответ — тоже оставляет.

    python3 tests/asis_confirm_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                          # noqa: E402


def blocks():
    out = [{"id": "s01.b0001", "kind": "p", "text": "Проза перед списком."}]
    for i in range(2, 12):
        out.append({"id": f"s01.b{i:04d}", "kind": "p", "asis": True,
                    "text": f"► Ekul Replied on July {i}th, 2011: пост номер {i}."})
    out.append({"id": "s01.b0012", "kind": "code", "asis": True, "text": "x = 1"})
    out.append({"id": "s01.b0013", "kind": "p", "asis": True, "drop": True,
                "text": "Index entry"})
    out.append({"id": "s01.b0014", "kind": "p", "text": "Проза после."})
    return out


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    runs = E._asis_runs(blocks())
    ok("ряд — подряд идущие абзацы без кода и выброшенного",
       runs == [(1, 10)], runs)

    asked = []
    bs = blocks()
    n = E.confirm_asis(bs, lambda p: asked.append(p) or "ТЕКСТ", None)
    ok("«текст» снимает пометку со всего ряда",
       n == 10 and not any(b.get("asis") for b in bs[1:11]), n)
    ok("код и выброшенное не тронуты",
       bs[11].get("asis") and bs[12].get("asis"), None)
    ok("в вопросе — начало и конец ряда и его размер",
       len(asked) == 1 and "пост номер 2" in asked[0] and "пост номер 11" in asked[0]
       and "10 абзацев" in asked[0], asked[0][:200] if asked else asked)

    bs = blocks()
    E.confirm_asis(bs, lambda p: "СПРАВОЧНЫЙ", None)
    ok("«справочный» оставляет как есть", all(b.get("asis") for b in bs[1:11]), None)

    def boom(p):
        raise RuntimeError("нет связи")
    bs = blocks()
    E.confirm_asis(bs, boom, None)
    ok("сбой модели — решение правил в силе", all(b.get("asis") for b in bs[1:11]), None)
    bs = blocks()
    E.confirm_asis(bs, lambda p: "не знаю", None)
    ok("невнятный ответ — тоже", all(b.get("asis") for b in bs[1:11]), None)

    said = []
    _, bl, _, _ = None, blocks(), None, None
    E.confirm_asis(bl, lambda p: "ТЕКСТ", said.append)
    ok("в лог — границы ряда и вердикт", len(said) == 1 and "s01.b0002" in said[0]
       and "s01.b0011" in said[0], said)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
