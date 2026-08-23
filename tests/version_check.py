#!/usr/bin/env python3
"""Проверка записи версии конвейера в рабочую папку.

Папки переживают архивы: перевод сделан одной версией, пересборка другой.
По versions.json спустя год видно, какими версиями что делалось и какие
миграции внутренних форматов нужны.

    python3 tests/version_check.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                        # noqa: E402


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    P.note_version(d, stamp="1.0.0 aaaaaaaaaaaa")
    v = json.load(open(f"{d}/versions.json", encoding="utf-8"))
    ok("первая запись: first и last совпадают",
       v["first"]["pipeline"] == v["last"]["pipeline"] == "1.0.0 aaaaaaaaaaaa", v)
    # другая версия трогает папку: first остаётся, last меняется, история растёт
    P.note_version(d, stamp="2.0.0 bbbbbbbbbbbb")
    v = json.load(open(f"{d}/versions.json", encoding="utf-8"))
    ok("first не переписывается", v["first"]["pipeline"] == "1.0.0 aaaaaaaaaaaa",
       v["first"])
    ok("last — последняя версия", v["last"]["pipeline"] == "2.0.0 bbbbbbbbbbbb")
    ok("история хранит обе", sorted(v["seen"]) ==
       ["1.0.0 aaaaaaaaaaaa", "2.0.0 bbbbbbbbbbbb"], sorted(v["seen"]))
    # та же версия снова — история не разбухает, обновляется дата last
    P.note_version(d, stamp="2.0.0 bbbbbbbbbbbb")
    v = json.load(open(f"{d}/versions.json", encoding="utf-8"))
    ok("повтор версии не плодит записей", len(v["seen"]) == 2, v["seen"])

    # отпечаток кода: стабилен в пределах запуска и похож на хэш
    a, b = P._code_print(), P._code_print()
    ok("отпечаток стабилен", a == b, (a, b))
    ok("двенадцать шестнадцатеричных", len(a) == 12
       and all(c in "0123456789abcdef" for c in a), a)

    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
