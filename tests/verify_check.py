#!/usr/bin/env python3
"""Проверка сверки замечаний редактора с оригиналом.

Редактор оригинала не видит: спорное по существу место он выписывает
замечанием. Сверщик видит обе стороны и выносит вердикт: ошибся автор —
сноска, ошибся перевод — исправление, пусто — снято, неясно — человеку.
Разбор обязан требовать вердикт на каждый блок и вещественное наполнение
при вердиктах author и translation.

    python3 tests/verify_check.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                        # noqa: E402

OUT = """[[[VERDICT s09.b0003 author]]]
Автор сам называет все пять точек устойчивыми.

[[[NOTE s09.b0003 fact]]]
TERM: точек Лагранжа
TEXT: Ошибка автора: из пяти точек Лагранжа устойчивы только L4 и L5.

[[[VERDICT s39.b0011 translation]]]
В оригинале bulk — масса, а не объём.

[[[P s39.b0011]]]
…сотая часть массы Млечного Пути…

[[[VERDICT s22.b0041 dismiss]]]
Так и в оригинале, движение допустимое.

[[[VERDICT s23.b0097 unsure]]]
Нужен источник, которого здесь нет.
"""
WANT = {"s09.b0003", "s39.b0011", "s22.b0041", "s23.b0097"}


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    v, n, f = P._parse_verify(OUT, WANT)
    ok("вердикт каждому блоку", {k: x[0] for k, x in v.items()} == {
        "s09.b0003": "author", "s39.b0011": "translation",
        "s22.b0041": "dismiss", "s23.b0097": "unsure"}, v)
    ok("сноска при вердикте author", n and n[0]["block"] == "s09.b0003"
       and n[0]["kind"] == "fact", n)
    ok("исправление при вердикте translation",
       f == {"s39.b0011": "…сотая часть массы Млечного Пути…"}, f)

    def rejected(out, want=WANT):
        try:
            P._parse_verify(out, want)
        except ValueError:
            return True
        return False

    ok("пропущенный вердикт отвергается",
       rejected(OUT.replace("[[[VERDICT s23.b0097 unsure]]]",
                            "[[[VERDICT s23.b0097 наверное]]]")))
    ok("author без сноски отвергается",
       rejected(OUT.replace("[[[NOTE s09.b0003 fact]]]",
                            "[[[NOTE s99.b9999 fact]]]")))
    ok("translation без исправления отвергается",
       rejected(OUT.replace("[[[P s39.b0011]]]", "[[[P s99.b9999]]]")))
    v, n, f = P._parse_verify(
        OUT + "\n[[[NOTE s22.b0041 fact]]]\nTERM: пульт\nTEXT: лишняя.\n"
              "\n[[[P s22.b0041]]]\nлишняя правка\n", WANT)
    ok("довесок вопреки вердикту отброшен", len(n) == 1 and len(f) == 1,
       (n, f))

    ok("замечание-отписка не сверяется", not P.has_notes("Нет."))
    ok("настоящее замечание сверяется",
       P.has_notes("s09.b0003: точек Лагранжа всего пять…"))

    # Правка сверщика ложится поверх перевода и редактуры при сборке.
    with tempfile.TemporaryDirectory() as w:
        for sub, body in (
            ("tr", {"index": 1, "tr": {"a": "перевод", "b": "цел"}}),
            ("ed", {"index": 1, "edits": {"a": {"old": "перевод",
                                                "new": "правка"},
                                          "b": {"old": "чужой черновик",
                                                "new": "сирота"}}}),
            ("vf", {"index": 1, "edits": {"a": {"old": "правка",
                                                "new": "сверено"}}}),
        ):
            os.makedirs(f"{w}/ru/{sub}")
            json.dump(body, open(f"{w}/ru/{sub}/0001.json", "w",
                                 encoding="utf-8"))
        tr, edited = P.all_translations(w, "ru")
        ok("сверка поверх редактуры в сборке",
           tr == {"a": "сверено", "b": "цел"} and edited == 2, (tr, edited))
        ok("правка-сирота не применяется", tr["b"] == "цел", tr["b"])
        ok("сверка поверх редактуры в куске",
           P.current(w, 1, "ru") == {"a": "сверено", "b": "цел"},
           P.current(w, 1, "ru"))
        # Сноска сверки помечается редакторской — сборка подпишет её
        # «Прим. ред.»; смесь с переводческой остаётся переводческой.
        json.dump({"index": 1, "footnotes": [
            {"block": "a", "kind": "fact", "term": "т1", "text": "правда."},
            {"block": "b", "kind": "term", "term": "т2", "text": "толк."}]},
            open(f"{w}/ru/vf/0001.json", "w", encoding="utf-8"))
        json.dump({"index": 1, "tr": {}, "footnotes": [
            {"block": "b", "kind": "term", "term": "т3", "text": "своя."}]},
            open(f"{w}/ru/tr/0002.json", "w", encoding="utf-8"))
        notes = P.all_notes(w, {"a": 0, "b": 1}, "ru")
        ok("сноска сверки — редакторская", notes["a"]["editor"] is True,
           notes["a"])
        ok("смесь остаётся переводческой", notes["b"]["editor"] is False,
           notes["b"])
        # Запечённая сверочная сноска: второй проход редактуры переносит её
        # в tr с пометкой в самой записи — голос остаётся редакторским.
        json.dump({"index": 3, "tr": {}, "footnotes": [
            {"block": "c", "kind": "fact", "term": "т4", "text": "истина.",
             "editor": True}]},
            open(f"{w}/ru/tr/0003.json", "w", encoding="utf-8"))
        notes = P.all_notes(w, {"a": 0, "b": 1, "c": 2}, "ru")
        ok("запечённая сноска сохраняет голос редактора",
           notes["c"]["editor"] is True, notes["c"])

    # Сверка параллелится: куски независимы, а исправления и счётчики — под
    # замком. Два куска с замечаниями, два потока — оба файла сверки на месте.
    import re as _re
    import shutil as _sh

    class Judge:
        model, kind = "судья", "стенд"

        def run(self, system, user):
            ids = sorted(set(_re.findall(r"### (s\d+\.b\d+)", user)))
            out = "\n\n".join(f"[[[VERDICT {i} dismiss]]]\nПусто, так и в "
                              "оригинале." for i in ids)
            return out, {"model": self.model, "cost_usd": 0}

    d = tempfile.mkdtemp()
    chunks2 = []
    for i in (1, 2):
        bid = f"s{i:02d}.b0001"
        chunks2.append({"index": i, "label": f"гл{i}",
                        "blocks": [{"id": bid, "kind": "p",
                                    "text": f"Original {i}."}]})
        os.makedirs(f"{d}/tr", exist_ok=True)
        json.dump({"index": i, "model": "стенд", "cost_usd": 0,
                   "tr": {bid: f"Перевод {i}."}},
                  open(f"{d}/tr/{i:04d}.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        os.makedirs(f"{d}/ed", exist_ok=True)
        json.dump({"index": i, "model": "правщик", "blocks": [bid],
                   "notes": f"Сомнение в блоке {bid}: сверить с оригиналом.",
                   "edits": {}},
                  open(f"{d}/ed/{i:04d}.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
    done, skipped, fn, fx = P.verify(d, chunks2, Judge(), "", "задание", 1,
                                     lambda *a, **k: None, jobs=2)
    got_files = sorted(os.listdir(f"{d}/vf")) if os.path.isdir(f"{d}/vf") else []
    ok("параллельная сверка: оба куска сверены",
       done == 2 and got_files == ["0001.json", "0002.json"],
       (done, got_files))
    done2 = P.verify(d, chunks2, Judge(), "", "задание", 1,
                     lambda *a, **k: None, jobs=2)[0]
    ok("повторная сверка пропущена по отпечаткам", done2 == 0, done2)

    # Очередь сверки — по номерам кусков, а не по возрасту файлов: ручная
    # правка одного куска не тасует порядок.
    order = []

    class Tally(Judge):
        def run(self, system, user):
            order.append(sorted(set(_re.findall(r"### (s\d+)", user))))
            return Judge.run(self, system, user)

    _sh.rmtree(f"{d}/vf", ignore_errors=True)
    os.utime(f"{d}/ed/0002.json", (1_000_000_000, 1_000_000_000))
    P.verify(d, chunks2, Tally(), "", "задание", 1, lambda *a, **k: None)
    ok("очередь по номерам при перепутанных возрастах",
       order == [["s01"], ["s02"]], order)
    _sh.rmtree(d, ignore_errors=True)

    # Полнотекстовый прочёс: вердикты обязательны только по замечаниям,
    # молчание по остальным парам — норма, находка идёт тем же форматом.
    sweep = WANT | {"s40.b0001", "s40.b0002", "s40.b0003"}
    v, n, f = P._parse_verify(
        OUT + "\n[[[VERDICT s40.b0002 translation]]]\nМера подменена.\n"
              "\n[[[P s40.b0002]]]\nшириной в милю\n", sweep, WANT)
    ok("молчание по чистым парам — норма",
       "s40.b0001" not in v and "s40.b0003" not in v, v)
    ok("находка прочёса принята", f.get("s40.b0002") == "шириной в милю", f)
    v, n, f = P._parse_verify(OUT + "\n[[[P s40.b0003]]]\nбез вердикта\n",
                              sweep, WANT)
    ok("правка прочёса без вердикта отброшена", "s40.b0003" not in f, f)
    try:
        P._parse_verify(OUT.replace("[[[VERDICT s23.b0097 unsure]]]",
                                    "[[[VERDICT s40.b0001 dismiss]]]"),
                        sweep, WANT)
        ok("замечание без вердикта отвергается и в прочёсе", False)
    except ValueError:
        ok("замечание без вердикта отвергается и в прочёсе", True)

    # Кусок без замечаний: обычная сверка его пропускает, полнотекстовая
    # прочёсывает целиком и кладёт отпечатки на все блоки.
    class Sees:
        def run(self, system, user):
            if "three apples" not in user or "ясным" not in user:
                raise AssertionError("в промпте нет всех пар")
            return ("[[[VERDICT c1.b0001 translation]]]\nЧисло подменено.\n"
                    "[[[P c1.b0001]]]\nу меня три яблока\n",
                    {"model": "стенд", "cost_usd": 0})

    d = tempfile.mkdtemp()
    chunks3 = [{"index": 1, "label": "гл1", "blocks": [
        {"id": "c1.b0001", "kind": "p", "text": "I have three apples."},
        {"id": "c1.b0002", "kind": "p", "text": "The day was clear."}]}]
    os.makedirs(f"{d}/tr"); os.makedirs(f"{d}/ed")
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0,
               "tr": {"c1.b0001": "у меня пять яблок",
                      "c1.b0002": "день был ясным"}},
              open(f"{d}/tr/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"index": 1, "model": "правщик", "notes": "", "edits": {}},
              open(f"{d}/ed/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    hush = lambda *a, **k: None
    ok("обычная сверка кусок без замечаний пропускает",
       P.verify(d, chunks3, Sees(), "", "задание", 1, hush)[0] == 0)
    done, _, fn, fx = P.verify(d, chunks3, Sees(), "", "задание", 1, hush,
                               full=True)
    vf = json.load(open(f"{d}/vf/0001.json", encoding="utf-8"))
    ok("прочёс без замечаний состоялся", done == 1 and fx == 1, (done, fx))
    ok("находка легла правкой",
       vf["edits"]["c1.b0001"]["new"] == "у меня три яблока", vf["edits"])
    ok("отпечатки на весь кусок",
       set(vf["src"]) == {"c1.b0001", "c1.b0002"}, vf["src"])
    ok("повторный прочёс пропущен по отпечаткам",
       P.verify(d, chunks3, Sees(), "", "задание", 1, hush, full=True)[1] == 1)
    _sh.rmtree(d, ignore_errors=True)

    print(f"\nПроверок: {seen}, расхождений: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
