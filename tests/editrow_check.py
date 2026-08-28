#!/usr/bin/env python3
"""Очередь редакторов для куска, переведённого после отказа.

Старое правило отдавало такой кусок переведшей модели целиком — и pro
правила собственный перевод: самоправка, слепая к своим калькам. Теперь
цепочка редакторов идёт как задана, пропуская записанных отказавшихся;
переведшая модель — страховочное дно. Для этого отказавшиеся должны быть
записаны в файл куска по именам — булева пометка не говорит, кого обходить.

    python3 tests/editrow_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402


class Fake:
    def __init__(self, model, refuse=False):
        self.model, self.kind, self.refuse = model, "проба", refuse

    def __repr__(self):
        return f"<{self.model}>"


def names(row):
    return [a.model for a in row]


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    opus, pro, sol = Fake("opus"), Fake("pro"), Fake("sol")

    # Обычный кусок: цепочка как задана.
    row = P._edit_row(opus, [pro, sol], "", None)
    ok("без отказа — цепочка как задана", names(row) == ["opus", "pro", "sol"],
       names(row))

    # Старый файл: отказ был, имён нет — по-старому правит переведшая.
    row = P._edit_row(opus, [pro, sol], "pro", None)
    ok("имён нет — правит переведшая (по-старому)",
       names(row) == ["pro", "sol"], names(row))

    # Имена записаны, редактор не отказывался: он и правит, переведшая — дно.
    row = P._edit_row(opus, [pro, sol], "pro", ["flash"])
    ok("редактор чист — правит он, переведшая дном",
       names(row) == ["opus", "pro", "sol"], names(row))

    # Первый редактор отказался переводить — пропущен, правит второй.
    row = P._edit_row(opus, [pro, sol], "pro", ["opus"])
    ok("отказавшийся редактор пропущен", names(row) == ["pro", "sol"],
       names(row))

    # Переведшей модели нет в цепочке редакторов — дно дописывается? Нет:
    # дописать некого, кусок идёт чистым редакторам.
    row = P._edit_row(opus, [sol], "pro", ["flash"])
    ok("переведшей нет в цепочке — идут чистые", names(row) == ["opus", "sol"],
       names(row))

    # Отказали все редакторы: остаётся переведшая — самоправка как дно,
    # а не как первый выбор.
    row = P._edit_row(opus, [pro, sol], "pro", ["opus", "sol"])
    ok("отказали все — дно, переведшая", names(row) == ["pro"], names(row))

    # Отказали все, переведшей в цепочке нет: пробует заданный редактор.
    row = P._edit_row(opus, [sol], "pro", ["opus", "sol"])
    ok("отказали все, дна нет — заданный редактор", names(row) == ["opus"],
       names(row))

    # Имена отказавшихся доезжают до файла куска: _chain_run собирает их
    # с каждой отказавшей модели, а не только ставит флаг.
    real = P._run

    def fake_run(a, system, prompt, retries, parse, log):
        if a.refuse:
            raise P.Refused("b0001", 1, 2)
        return "готово", {"model": a.model, "cost_usd": 0}, 0.1

    P._run = fake_run
    try:
        r1, r2 = Fake("first", refuse=True), Fake("second", refuse=True)
        got, meta, _ = P._chain_run([r1, r2, Fake("third")], "с", "з", 1,
                                    None, lambda *a, **k: None)
    finally:
        P._run = real
    ok("имена отказавшихся записаны по порядку",
       meta.get("after_refusal") is True
       and meta.get("refused_by") == ["first", "second"], meta)
    ok("перевод от третьей, не от отказавших", meta.get("model") == "third",
       meta)

    # Без отказов пометки нет вовсе — иначе редактура шарахалась бы от
    # каждого куска, просто пережившего сбой связи.
    P._run = fake_run
    try:
        _, meta, _ = P._chain_run([Fake("clean")], "с", "з", 1, None,
                                  lambda *a, **k: None)
    finally:
        P._run = real
    ok("чистый кусок без пометки", "after_refusal" not in meta
       and "refused_by" not in meta, meta)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
