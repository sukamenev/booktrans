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

    print(f"\nПроверок: {seen}, расхождений: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
