#!/usr/bin/env python3
"""Освежение отпечатков готовности после ручной правки переводов.

Точечная замена в переводе (имя, термин), внесённая синхронно в пары
правок, не меняет сделанности работы — но отпечатки расходятся, и на живой
книге редактура перечитывала весь том заново. Освежитель признаёт
сделанным блок, чья правка по-прежнему чисто накладывается; осиротевшая
правка остаётся честно несделанной. Сверка меряется по тексту с
наложенными правками.

    python3 tests/refresh_check.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402
from booktrans.pipeline import fingerprint as fp            # noqa: E402


def hush(m="", end="\n"):
    pass


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/tr"); os.makedirs(f"{d}/ed"); os.makedirs(f"{d}/vf")
    # b1 — правленый блок, текст переименован синхронно в tr и в паре;
    # b2 — правка-сирота (old отстал от tr); b3 — правки не было, текст новый.
    tr = {"b1": "новое имя в тексте", "b2": "текст два", "b3": "текст три"}
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0, "tr": tr},
              open(f"{d}/tr/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"index": 1, "model": "стенд",
               "src": {"b1": "старый-хэш", "b2": "старый-хэш",
                       "b3": "старый-хэш"},
               "edits": {"b1": {"old": "новое имя в тексте",
                                "new": "правленое имя"},
                         "b2": {"old": "ДРУГОЙ текст", "new": "х"}}},
              open(f"{d}/ed/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    # сверка: b1 исправлен ею поверх правки, b3 — только осмотрен.
    json.dump({"index": 1, "model": "стенд",
               "src": {"b1": "старый-хэш", "b3": "старый-хэш"},
               "edits": {"b1": {"old": "правленое имя",
                                "new": "сверенное имя"}}},
              open(f"{d}/vf/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)

    P.refresh(d, hush)
    ed = json.load(open(f"{d}/ed/0001.json", encoding="utf-8"))
    vf = json.load(open(f"{d}/vf/0001.json", encoding="utf-8"))
    ok("чистая правка признана сделанной",
       ed["src"]["b1"] == fp(tr["b1"]), ed["src"]["b1"])
    ok("сирота не тронута — переделается честно",
       ed["src"]["b2"] == "старый-хэш", ed["src"]["b2"])
    ok("блок без правки освежён",
       ed["src"]["b3"] == fp(tr["b3"]), ed["src"]["b3"])
    ok("сверка меряется по исправленному ею тексту",
       vf["src"]["b1"] == fp("сверенное имя"), vf["src"]["b1"])
    ok("осмотренный сверкой блок — по наложенному тексту",
       vf["src"]["b3"] == fp(tr["b3"]), vf["src"]["b3"])

    # Повторный запуск ничего не меняет.
    P.refresh(d, hush)
    ok("освежение идемпотентно",
       json.load(open(f"{d}/ed/0001.json", encoding="utf-8")) == ed)

    import shutil
    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
