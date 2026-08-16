#!/usr/bin/env python3
"""Проверка, что обложка доходит до сборщика — во всех форматах.

Место под обложку есть у каждого сборщика, а раздаёт её `build_book`, и
раздавал по списку форматов, перечисленных руками. `.fb2` и `.tex` в список
не попали: книга выходила без обложки, и увидеть это можно было только
открыв её. Проверки формата тут не помогали — они зовут сборщик напрямую и
эту развилку не проходят.

    python3 tests/cover_check.py
"""
import base64
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import build as B, output as O               # noqa: E402

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")

BLOCKS = [{"id": "s01.b0001", "kind": "title", "text": "Глава первая"},
          {"id": "s01.b0002", "kind": "p", "text": "Текст главы."}]
META = {"title": "Книга", "author": "Автор", "target_lang": "ru", "lang": "en"}


def work_dir():
    d = tempfile.mkdtemp()
    os.makedirs(d + "/tr")
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0, "footnotes": [],
               "tr": {b["id"]: "перевод" for b in BLOCKS}},
              open(d + "/tr/0001.json", "w", encoding="utf-8"), ensure_ascii=False)
    return d


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:44} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    was = dict(O.WRITERS)
    seen = {}

    def spy(ext):
        def writer(dest, meta, items, notes, images, note_prefix, st=None, **kw):
            seen[ext] = kw.get("cover")
            open(dest, "wb").write(b"")      # сборка проверяет размер файла
        return writer

    try:
        for ext in sorted(was):
            O.WRITERS[ext] = spy(ext)
            d = work_dir()
            B.build_book(d, dict(META), list(BLOCKS), PIXEL,
                         os.path.join(d, "книга" + ext),
                         lambda *a, **k: None, False, {})
            shutil.rmtree(d, ignore_errors=True)
            ok(f"обложка доехала до сборщика {ext}", seen.get(ext) == PIXEL,
               "не передана" if seen.get(ext) is None else "передана не та")
    finally:
        O.WRITERS.clear()
        O.WRITERS.update(was)

    print(f"\nслучаев: {len(was)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
