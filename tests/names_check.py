#!/usr/bin/env python3
"""Имена по справочнику: находки конвейера, претензии с номерами, вердикты.

Основа имени берётся из форм, названных разведкой, иначе — по последней
гласной; абзац с именем в оригинале и без его перевода даёт замечание
сверщику; к одному блоку замечаний может быть несколько, и вердикт нужен
на каждое, а исправление абзаца — одно.

    python3 tests/names_check.py
"""
import json
import os
import re
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

    ok("основа из форм разведки", P.name_stem("Лощина", "склоняется: Лощина, Лощины") == "лощин",
       P.name_stem("Лощина", "склоняется: Лощина, Лощины"))
    ok("основа без форм — по последней гласной", P.name_stem("Сплетница") == "сплетниц",
       P.name_stem("Сплетница"))
    ok("короткое имя не режется", P.name_stem("Рой") == "рой", P.name_stem("Рой"))
    ok("двусловное имя — по длинному слову", P.name_stem("Мисс Ополчение") == "ополчени",
       P.name_stem("Мисс Ополчение"))

    rows = [("Haven", "| Haven | Прибежище | средний род | команда |", "NAMES"),
            ("Coil", "| Coil | Койл | мужской род | Томас |", "CHARACTERS"),
            ("Gully", "| Gully | Лощина | склоняется: Лощина, Лощины | героиня |", "CHARACTERS"),
            ("cape", "| cape | кейп | | термин |", "TERMS")]
    srcs = {"a": "Haven and Coil met Gully.", "b": "Coil left with a cape.", "c": "Rain fell."}
    cur = {"a": "Убежище и Койл встретили Лощину.", "b": "Койла не было, плащ остался.", "c": "Шёл дождь."}
    gaps = P.name_gaps(rows, srcs, cur)
    ok("найдено только имя без перевода", gaps == {"a": [("Haven", "Прибежище")]}, gaps)
    ok("форма по основе засчитана (Лощину), термины не в счёт",
       "Лощина" not in str(gaps) and "cape" not in str(gaps), gaps)

    raw, shown, claims = P.claims_text("s1.b1: сомнение редактора в факте.",
                                       {"s1.b1": [("Haven", "Прибежище")], "s1.b2": [("Coil", "Койл")]},
                                       ["s1.b1", "s1.b2", "s1.b3"])
    ok("две претензии к блоку нумеруются, одна — нет",
       claims == ["s1.b1#1", "s1.b1#2", "s1.b2"] and "s1.b1#2: имя Haven" in shown
       and shown.splitlines()[2].startswith("s1.b2: имя Coil"), (claims, shown))
    ok("без находок текст замечаний прежний",
       P.claims_text("s1.b1: сомнение.", {}, ["s1.b1"])[0] == "s1.b1: сомнение.", None)

    out = ("[[[VERDICT s1.b1#1 dismiss]]]\n«…факт…» — так у автора.\n"
           "[[[VERDICT s1.b1#2 translation]]]\n«Убежище» — по справочнику Прибежище.\n"
           "[[[P s1.b1]]]\nПрибежище и Койл встретили Лощину.\n[[[/P s1.b1]]]\n"
           "[[[VERDICT s1.b2 dismiss]]]\n«Койла» — форма имени.\n")
    v, notes, fixes = P._parse_verify(out, {"s1.b1", "s1.b2"}, ["s1.b1#1", "s1.b1#2", "s1.b2"])
    ok("вердикт на каждую претензию", set(v) == {"s1.b1#1", "s1.b1#2", "s1.b2"}
       and v["s1.b1#2"][0] == "translation", v)
    ok("исправление одно на блок", fixes == {"s1.b1": "Прибежище и Койл встретили Лощину."}, fixes)
    try:
        P._parse_verify(out.replace("[[[VERDICT s1.b1#1 dismiss]]]\n«…факт…» — так у автора.\n", ""),
                        {"s1.b1", "s1.b2"}, ["s1.b1#1", "s1.b1#2", "s1.b2"])
        ok("без ответа на претензию — переспрос", False)
    except ValueError as e:
        ok("без ответа на претензию — переспрос", "s1.b1#1" in str(e), str(e))

    # Сквозной прогон сверки: замечание редактора и находка конвейера на одном
    # блоке, судья отвечает по номерам.
    class Judge:
        model, kind = "судья", "стенд"

        def run(self, system, user):
            claims = re.findall(r"^(s\d+\.b\d+(?:#\d+)?):", user, re.M)
            out = []
            for c in claims:
                if "[конвейер]" in next(l for l in user.splitlines() if l.startswith(c + ":")):
                    out.append(f"[[[VERDICT {c} translation]]]\n«Убежище» — по справочнику.\n"
                               f"[[[P {c.split('#')[0]}]]]\nПрибежище пришло.\n[[[/P {c.split('#')[0]}]]]")
                else:
                    out.append(f"[[[VERDICT {c} dismiss]]]\n«…» — так у автора.")
            return "\n".join(out), {"model": self.model, "cost_usd": 0}

    d = tempfile.mkdtemp()
    bid = "s01.b0001"
    chunks = [{"index": 1, "label": "гл1", "blocks": [{"id": bid, "kind": "p", "text": "Haven came."}]}]
    os.makedirs(f"{d}/tr"); os.makedirs(f"{d}/ed")
    json.dump({"index": 1, "model": "стенд", "tr": {bid: "Убежище пришло."}}, open(f"{d}/tr/0001.json", "w"), ensure_ascii=False)
    json.dump({"index": 1, "model": "правщик", "blocks": [bid], "edits": {},
               "notes": f"Сомнение в блоке {bid}: сверить факт с оригиналом."}, open(f"{d}/ed/0001.json", "w"), ensure_ascii=False)
    open(f"{d}/scout.md", "w", encoding="utf-8").write("## NAMES — Имена\n\n| Haven | Прибежище | средний род | команда |\n")
    done, skipped, fn, fx = P.verify(d, chunks, Judge(), "", "задание", 1, lambda *a, **k: None, full=False)
    vf = json.load(open(f"{d}/vf/0001.json", encoding="utf-8"))
    ok("сверка прошла по двум претензиям", done == 1 and fx == 1
       and f"{bid}#1:" in vf["notes"] and f"{bid}#2:" in vf["notes"], vf.get("notes"))
    ok("имя исправлено в тексте", vf["edits"][bid]["new"] == "Прибежище пришло.", vf.get("edits"))
    done2 = P.verify(d, chunks, Judge(), "", "задание", 1, lambda *a, **k: None, full=False)[0]
    ok("повтор — по отпечаткам пропущен", done2 == 0, done2)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
