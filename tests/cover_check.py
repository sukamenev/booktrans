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

from booktrans import build as B
from booktrans import extract as E, output as O               # noqa: E402

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

    # Зрячее чтение pdf обложки не давало вовсе: `cover = None` в конце
    # разбора. Книга открывалась титулом с именем файла, а рисунок с первой
    # страницы шёл в текст обычной картинкой.
    d = tempfile.mkdtemp()
    pages = os.path.join(d, "pdf_pages")
    os.makedirs(pages)
    open(os.path.join(pages, "page_0001.png"), "wb").write(PIXEL)
    cover_page = ("Виктория Зименкова\n\n![image](images/рис.png)\n\n"
                  "# Несуществующий\n## или\n# шесть секунд\n")
    from pathlib import Path

    def look(text, name="рис.png"):
        blocks = [{"id": "s01.b0001", "kind": "p", "text": "Автор", "_page": 1},
                  {"id": "s01.img0001", "kind": "image", "text": name, "_page": 1}]
        images = {name: PIXEL}
        got = E._visual_cover(Path(pages), [(1, text)], blocks, images)
        return got, blocks, images

    got, blocks, images = look(cover_page)
    ok("обложка взята отрисовкой страницы", got == PIXEL, got)
    ok("рисунок обложки не задвоен в тексте",
       not images and not [b for b in blocks if b["kind"] == "image"],
       (list(images), len(blocks)))
    # Страница с прозой — не обложка, сколько бы картинок на ней ни стояло.
    proza = "![image](images/рис.png)\n\n" + "слово " * 60
    ok("страница прозы за обложку не сходит", look(proza)[0] is None)
    # И страница без картинки тоже: голый титул обложкой не бывает.
    ok("титул без картинки не обложка",
       look("Виктория Зименкова\n\n# Несуществующий")[0] is None)
    shutil.rmtree(d, ignore_errors=True)

    # Рамку картинки называет модель, и на вёрстке в две колонки она
    # захватывает соседнюю: на живой книге в рисунок попала колонка прозы, и
    # та же проза стояла рядом текстом — читатель видел её дважды. Режем по
    # текстовому слою страницы.
    #
    # Полоса 1000×800, рисунок слева, колонка текста справа от 700.
    text_lines = [(700, 100 + i * 20, 980, 112 + i * 20) for i in range(20)]
    got = E._trim_to_picture((0, 80, 800, 780), text_lines, 1.0)
    ok("колонка текста срезана", got == (0, 80, 700, 780), got)
    # Подпись под рисунком и надписи внутри схемы целиком лежат в рамке —
    # их не трогаем, иначе развалится всякая схема с подписями.
    inside = [(120, 300, 300, 320), (150, 500, 260, 520)]
    ok("надписи внутри рамки целы",
       E._trim_to_picture((100, 200, 600, 700), inside, 1.0) == (100, 200, 600, 700),
       E._trim_to_picture((100, 200, 600, 700), inside, 1.0))
    # Текстового слоя нет — резать нечем, и рамка остаётся как есть.
    ok("без текстового слоя рамка цела",
       E._trim_to_picture((0, 0, 100, 100), [], 1.0) == (0, 0, 100, 100))
    # Если по правилу от картинки почти ничего не остаётся, значит рамка не
    # про колонку рядом, а про схему из одних надписей: верим модели.
    dense = [(0, 0, 1000, 20), (0, 30, 1000, 50), (0, 60, 1000, 80)]
    ok("схема из одних надписей не срезается",
       E._trim_to_picture((10, 10, 990, 90), dense, 1.0) == (10, 10, 990, 90),
       E._trim_to_picture((10, 10, 990, 90), dense, 1.0))

    # Цикл на титульной странице: без него у трёх книг одного цикла заглавия
    # сходятся, и читатель не знает, которую держит.
    seen = {}

    def spy(dest, meta, items, notes, images, note_prefix, st=None, **kw):
        seen["items"] = items
        open(dest, "wb").write(b"")

    was_md = O.WRITERS.get(".md")
    O.WRITERS[".md"] = spy
    try:
        d = work_dir()
        B.build_book(d, dict(META, series_target="The Nonexistent One",
                             series_no="2"),
                     list(BLOCKS), None, os.path.join(d, "к.md"),
                     lambda *a, **k: None, False, {})
        shutil.rmtree(d, ignore_errors=True)
    finally:
        if was_md:
            O.WRITERS[".md"] = was_md
    ok("цикл стоит на титуле",
       any(b == "_meta_series" and "The Nonexistent One, 2" == t
           for _, t, b, *_ in seen.get("items", [])),
       [t for _, t, b, *_ in seen.get("items", []) if b.startswith("_meta")])

    # Имя выходного файла: «Фамилия Имя. Заглавие». Отчество ломало правило —
    # фамилией считалось всё, кроме первого слова, и выходило
    # «Викторовна Зименкова Виктория».
    for who, want in (("Viktoria Viktorovna Zimenkova", "Zimenkova Viktoria Viktorovna"),
                      ("Виктория Викторовна Зименкова", "Зименкова Виктория Викторовна"),
                      ("Габриэль Гарсиа Маркес", "Гарсиа Маркес Габриэль"),
                      ("Sue Burke", "Burke Sue"),
                      ("Slobodan Milosevic", "Milosevic Slobodan"),
                      # Инициал в середине: фамилия последняя, а псевдоним в
                      # скобках не участвует в перестановке и остаётся хвостом.
                      ("Джон К. Маккрей (Wildbow)", "Маккрей Джон К. (Wildbow)"),
                      ("J. R. R. Tolkien", "Tolkien J. R. R."),
                      ("Уайлдбоу (Джон К. Маккрей)", "Уайлдбоу (Джон К. Маккрей)")):
        got = B.out_name({"title": "Книга", "author": who}, "x")
        ok(f"имя файла: {who[:28]}", got == f"{want}. Книга", got)

    # Ключ --name-series: цикл и номер в имени, книги цикла выстраиваются
    # по порядку чтения. Номер — двумя цифрами: список файлов сортируется
    # по буквам, и «10» без нуля вставала между «1» и «2». Хвост «.0» у
    # номера из calibre отрезается, дробный номер остаётся как есть.
    m = {"title": "Плот", "author_target": "Стивен Бакстер",
         "series": "Xeelee Sequence", "series_target": "Ксили",
         "series_no": "1"}
    ok("имя с циклом, номер двумя цифрами",
       B.out_name(m, "x", True) == "Бакстер Стивен. Ксили 01. Плот",
       B.out_name(m, "x", True))
    ok("без ключа цикл не лезет в имя",
       B.out_name(m, "x") == "Бакстер Стивен. Плот", B.out_name(m, "x"))
    m2 = dict(m, series_no="3.0"); m2.pop("series_target")
    ok("номер calibre без хвоста .0",
       B.out_name(m2, "x", True) == "Бакстер Стивен. Xeelee Sequence 03. Плот",
       B.out_name(m2, "x", True))
    m4 = dict(m, series_no="3.5")
    ok("дробный номер не набивается",
       B.out_name(m4, "x", True) == "Бакстер Стивен. Ксили 3.5. Плот",
       B.out_name(m4, "x", True))
    m3 = {"title": "Плот", "author_target": "Стивен Бакстер"}
    ok("цикла нет — имя обычное",
       B.out_name(m3, "x", True) == "Бакстер Стивен. Плот",
       B.out_name(m3, "x", True))
    m5 = {"title_target": "Зили: Выносливость", "author_target": "Стивен Бакстер"}
    ok("двоеточие заглавия — тире, а не голый дефис",
       B.out_name(m5, "x") == "Бакстер Стивен. Зили — Выносливость",
       B.out_name(m5, "x"))

    print(f"\nслучаев: {len(was) + 23}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
