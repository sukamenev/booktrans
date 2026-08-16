#!/usr/bin/env python3
"""Проверка собранной книги: epub и html.

Epub — это zip с описью: манифест перечисляет файлы, spine задаёт порядок,
nav делает оглавление. Ошибка тут не видна на глаз — файл откроется, а глава
или картинка просто не покажется, — поэтому сверяем опись с содержимым
архива.

    python3 tests/epub_check.py
"""
import base64
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import output as O                          # noqa: E402

OPF = "{http://www.idpf.org/2007/opf}"
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")
OTHER = PIXEL + b"\x00"          # другая картинка, не обложка

ITEMS = [
    ("title", "Глава первая", "s01.b0001", None, None),
    ("p", "Текст первой главы, <a1>дальше</a1>.", "s01.b0002",
     ["#s02.b0001"], None),
    ("image", "photo.png", "s01.b0003", None, None),
    ("image", "нет-такой.png", "s01.b0004", None, None),
    ("title", "Глава вторая", "s02.b0001", None, None),
    ("p", "Текст второй главы.", "s02.b0002", None, None),
    ("table", "a | b", "s02.b0003", None, [[[2, 1], [1, 1]]]),
]


def build():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "book.epub")
    O.write_epub(p, {"title": "Книга", "author": "Автор", "target_lang": "ru"},
                 ITEMS, {}, {"photo.png": OTHER, "cover.png": PIXEL},
                 "Прим.:", {}, cover=PIXEL)
    return d, p


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:42} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d, p = build()
    z = zipfile.ZipFile(p)
    names = z.namelist()

    # Первым в архиве и без сжатия — иначе читалка не опознает формат.
    ok("mimetype первым и не сжат",
       names[0] == "mimetype" and z.getinfo("mimetype").compress_type == 0
       and z.read("mimetype") == b"application/epub+zip", names[:1])
    ok("архив цел", z.testzip() is None, z.testzip())

    broken = []
    for n in names:
        if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                broken.append(f"{n}: {e}")
    ok("весь xml разбирается", not broken, broken)

    root = ET.fromstring(z.read("OEBPS/content.opf"))
    man = {i.get("href") for i in root.find(OPF + "manifest")}
    ids = {i.get("id") for i in root.find(OPF + "manifest")}
    inside = {n[len("OEBPS/"):] for n in names if n.startswith("OEBPS/")}
    ok("файлы манифеста есть в архиве", not (man - inside), sorted(man - inside))
    ok("лишних файлов в архиве нет",
       not (inside - man - {"content.opf"}), sorted(inside - man - {"content.opf"}))
    ok("spine ссылается на своё",
       all(x.get("idref") in ids for x in root.find(OPF + "spine")),
       [x.get("idref") for x in root.find(OPF + "spine")])

    nav = z.read("OEBPS/nav.xhtml").decode()
    links = [l.split("#")[0] for l in re.findall(r'href="([^"]+)"', nav)]
    ok("оглавление ведёт в свои файлы", all(l in man for l in links), links)

    x = "".join(z.read(n).decode("utf-8", "ignore")
                for n in names if n.endswith(".xhtml"))
    used = set(re.findall(r'<img src="img/([^"]+)"', x))
    # Картинка несёт имя файла, а не прозу: перевода у неё нет, и раньше
    # вместо имени подставлялась пустая строка — картинки паковались, а в
    # тексте на них не оставалось ни одной ссылки.
    ok("картинка встала в текст", used == {"photo.png"}, used)
    ok("пропавшую не выдумали", "нет-такой.png" not in x)
    ok("в книгу попало только нужное",
       {n[len("OEBPS/img/"):] for n in names if n.startswith("OEBPS/img/")}
       == {"photo.png", "cover.png"},
       [n for n in names if n.startswith("OEBPS/img/")])
    # Обложка лежит и среди картинок книги — второй копии не нужно.
    ok("обложка не задвоена", "OEBPS/img/cover.jpg" not in names,
       [n for n in names if "cover" in n])
    # Обложку читалки ищут тремя способами, и все три должны быть на месте:
    # пометка в описи — по-нынешнему, `meta name="cover"` — по-старому, а
    # страница первой в череде — иначе книга откроется сразу на первой главе,
    # и обложки читатель не увидит вовсе.
    opf = z.read("OEBPS/content.opf").decode()
    ok("обложка помечена в описи", 'properties="cover-image"' in opf)
    ok("обложка названа по-старому",
       bool(re.search(r'<meta name="cover" content="img\d+"/>', opf)),
       re.findall(r'<meta name="cover"[^>]*/>', opf))
    ok("обложка отдельной страницей", "OEBPS/cover.xhtml" in names,
       [n for n in names if n.endswith(".xhtml")])
    ok("страница обложки идёт первой",
       re.findall(r'<itemref idref="([^"]+)"', opf)[:1] == ["cover"],
       re.findall(r'<itemref idref="([^"]+)"', opf)[:3])

    ok("внутренняя ссылка несёт имя файла",
       bool(re.search(r'href="ch\d+\.xhtml#s02\.b0001"', x)),
       re.findall(r'href="[^"]*#[^"]*"', x))
    ok("цель ссылки помечена", 'id="s02.b0001"' in x)
    ok("таблица со слиянием собрана", 'colspan="2"' in x,
       re.findall(r"<table>.*?</table>", x, re.S))
    shutil.rmtree(d)

    # --- html: тот же набор блоков, но один файл
    d = tempfile.mkdtemp()
    h = os.path.join(d, "book.html")
    O.write_html(h, {"title": "Книга", "author": "Автор", "target_lang": "ru"},
                 ITEMS, {}, {"photo.png": OTHER}, "Прим.:", {}, cover=PIXEL)
    t = open(h, encoding="utf-8").read()
    # Файл самодостаточный: картинки уходят в data:, и обложка тоже — иначе
    # при пересылке одним файлом от неё ничего не осталось бы.
    ok("обложка в html есть", '<img class="cover"' in t)
    ok("картинка вшита, а не ссылкой",
       t.count("data:image/png;base64,") == 2, re.findall(r'src="([^"]{0,24})', t))
    ok("пропавшую не выдумали", "нет-такой.png" not in t)
    ok("внутренняя ссылка в html без имени файла",
       'href="#s02.b0001"' in t and 'id="s02.b0001"' in t,
       re.findall(r'href="#[^"]*"', t))
    shutil.rmtree(d)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
