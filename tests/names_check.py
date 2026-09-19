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

    ok("основа из форм разведки", P.name_stem("Лощина", "склоняется: Лощина, Лощины", "ru") == "лощин",
       P.name_stem("Лощина", "склоняется: Лощина, Лощины", "ru"))
    ok("основа без форм — по окончаниям языка", P.name_stem("Сплетница", "", "ru") == "сплетниц"
       and P.name_stem("Лунная Песнь", "", "ru") == "лунн", (P.name_stem("Сплетница", "", "ru"), P.name_stem("Лунная Песнь", "", "ru")))
    ok("короткое имя не режется", P.name_stem("Рой", "", "ru") == "рой", P.name_stem("Рой", "", "ru"))
    ok("короткое имя режется по букве до трёх", P.name_stem("Змей", "", "ru") == "зме"
       and P.name_stem("Лиза", "", "ru") == "лиз", (P.name_stem("Змей", "", "ru"), P.name_stem("Лиза", "", "ru")))
    ok("двусловное имя — основы всех слов", {"мисс", "ополчен"} <= set(P.name_stems("Мисс Ополчение", "", "ru")),
       P.name_stems("Мисс Ополчение", "", "ru"))
    ok("псевдонимы в скобках и кавычки не мешают",
       P.name_stems("«Прорыв»", "", "ru") == ["прорыв"] and "богин" in P.name_stems("Богиня (Бьянка, также Синяя Императрица)", "", "ru"),
       P.name_stems("Богиня (Бьянка, также Синяя Императрица)", "", "ru"))
    ok("короткое имя всё же режется до трёх букв", P.name_stem("Лось", "", "ru") == "лос", P.name_stem("Лось", "", "ru"))
    ok("язык без правила — слово целиком", P.name_stem("Tanaka", "", "ja") == "tanaka"
       and P.name_stem("Peters", "", "de") == "peter", (P.name_stem("Tanaka", "", "ja"), P.name_stem("Peters", "", "de")))
    ok("беглая гласная даёт вторую основу",
       P.name_stems("Чертёнок", "", "ru") == ["чертёнок", "чертёнк"]
       and "ясновидц" in P.name_stems("Ясновидец", "", "ru"), P.name_stems("Чертёнок", "", "ru"))
    ok("повтор слова в ячейке рода — не форма",
       P.name_stems("Сьерра", "«Сьерра» и «Шарлотта» склоняются", "ru") == ["сьерр"],
       P.name_stems("Сьерра", "«Сьерра» и «Шарлотта» склоняются", "ru"))

    rows = [("Haven", "| Haven | Прибежище | средний род | команда |", "NAMES"),
            ("Coil", "| Coil | Койл | мужской род | Томас |", "CHARACTERS"),
            ("Gully", "| Gully | Лощина | склоняется: Лощина, Лощины | героиня |", "CHARACTERS"),
            ("cape", "| cape | кейп | | термин |", "TERMS")]
    rows.append(("Don", "| Don | Дон | | пёс |", "CHARACTERS"))
    rows.append(("One", "| One | Номер Один | | клон |", "CHARACTERS"))
    srcs = {"a": "Haven and Coil met Gully.", "b": "Coil left with a cape.", "c": "Rain fell. Don’t look.",
            "d": "One of them left; one stayed. One more."}
    cur = {"a": "Убежище и Койл встретили Лощину.", "b": "Койла не было, плащ остался.", "c": "Шёл дождь. Не смотри.",
           "d": "Один ушёл, один остался."}
    gaps = P.name_gaps(rows, srcs, cur, "ru")
    ok("найдено только имя без перевода", gaps == {"a": [("Haven", "Прибежище")]}, gaps)
    ok("форма по основе засчитана (Лощину), термины не в счёт",
       "Лощина" not in str(gaps) and "cape" not in str(gaps), gaps)
    ok("ключ-обычное слово (One) не проверяется", "One" not in str(gaps), gaps)

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

    # Общее замечание редактора на два блока получает адрес каждой претензии,
    # а номер претензии на сноске и на абзаце разборщик срезает сам.
    ids2 = ["s629.b0002", "s632.b0002"]
    _, shown2, claims2 = P.claims_text(
        "s629.b0002, s632.b0002: связь выглядит необычно.\n"
        "s632.b0002: термин вызывает сомнение.", {}, ids2)
    ok("общее замечание адресовано каждой претензии",
       shown2.splitlines() == ["s629.b0002: связь выглядит необычно.",
                               "s632.b0002#1: связь выглядит необычно.",
                               "s632.b0002#2: термин вызывает сомнение."]
       and claims2 == ["s629.b0002", "s632.b0002#1", "s632.b0002#2"], shown2)
    ans = ("[[[VERDICT s629.b0002 dismiss]]]\nтак в оригинале\n"
           "[[[VERDICT s632.b0002#1 translation]]]\nнеточно\n"
           "[[[P s632.b0002#1]]]\nИсправленный абзац.\n[[[/P s632.b0002#1]]]\n"
           "[[[VERDICT s632.b0002#2 author]]]\n«…термин…» — так у автора.\n"
           "[[[NOTE s632.b0002#2 fact]]]\nTERM: термин\nTEXT: «…термин…» — автор ошибся: верно иначе.\n"
           "[[[/NOTE s632.b0002#2]]]")
    try:
        v2, n2, f2 = P._parse_verify(ans, set(ids2), claims2)
        got = ([x["block"] for x in n2], list(f2))
    except ValueError as e:
        got = str(e)
    ok("сноска и абзац под номером претензии приняты за блок",
       got == (["s632.b0002"], ["s632.b0002"]), got)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
