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

from booktrans import lang as O_lang                        # noqa: E402
from booktrans import output as O                          # noqa: E402

OPF = "{http://www.idpf.org/2007/opf}"
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")
OTHER = PIXEL + b"\x00"          # другая картинка, не обложка

# Сноска с разметкой: курсив названий ставит и переводчик тегами, и модель
# звёздочками. `escape` отдавал читателю буквальное «<i>Vesti</i>».
NOTES = {"s01.b0002": {"text": "Прим.: <i>Vesti</i> is a *Russian* channel.",
                       "terms": ["дальше"], "source_only": False}}

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
                 ITEMS, NOTES, {"photo.png": OTHER, "cover.png": PIXEL},
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
    # А всё прочее, наоборот, жмётся: разметка книги ужимается вчетверо, и
    # без этого epub нёс лишние сотни килобайт на ровном месте.
    ok("остальное сжато",
       all(i.compress_type == zipfile.ZIP_DEFLATED
           for i in z.infolist() if i.filename != "mimetype"),
       [i.filename for i in z.infolist() if i.compress_type == 0])
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

    # Сколько картинок легло в книгу, говорит сам сборщик: из набора идёт не
    # всё, и у epub этой строки не было вовсе, хотя у fb2 была.
    said = []
    O.write_epub(os.path.join(d, "b2.epub"),
                 {"title": "Книга", "target_lang": "ru"}, ITEMS, {},
                 {"photo.png": OTHER, "cover.png": PIXEL}, "Прим.:", {},
                 cover=PIXEL, log=said.append, lang=O_lang)
    ok("epub говорит, сколько картинок вложил",
       any("1" in x and "2" in x for x in said), said)

    nx = z.read("OEBPS/notes.xhtml").decode()
    ok("разметка сноски развёрнута, а не экранирована",
       "<i>Vesti</i>" in nx and "<i>Russian</i>" in nx and "&lt;i&gt;" not in nx,
       nx[nx.find("Vesti") - 30:nx.find("Vesti") + 40])

    # Точная привязка: сборка вставила метку после термина, и знак сноски
    # стоит у объясняемого слова, а не в конце абзаца.
    import booktrans.output as _o
    anch = _o.anchor_note("Текст первой главы, <a1>дальше</a1>.", "s01.b0002",
                          ["дальше"])
    d2 = tempfile.mkdtemp()
    p2 = os.path.join(d2, "a.epub")
    O.write_epub(p2, {"title": "Книга", "target_lang": "ru"},
                 [("title", "Глава", "s01.b0001", None, None, 1),
                  ("p", anch, "s01.b0002", None, None, None)],
                 dict(NOTES), {}, "Прим.:", {})
    zz = zipfile.ZipFile(p2)
    ch = "".join(zz.read(n).decode() for n in zz.namelist() if "ch" in n)
    at = re.search(r"дальше<sup>", ch)
    ok("знак сноски стоит у термина", bool(at) and "дальше.<sup" not in ch,
       re.findall(r".{14}<sup>.{28}", ch))
    ok("метка не протекла в книгу", "\ue000" not in ch and "\ue001" not in ch)
    shutil.rmtree(d2, ignore_errors=True)

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
                 ITEMS, NOTES, {"photo.png": OTHER}, "Прим.:", {}, cover=PIXEL)
    t = open(h, encoding="utf-8").read()
    # Файл самодостаточный: картинки уходят в data:, и обложка тоже — иначе
    # при пересылке одним файлом от неё ничего не осталось бы.
    ok("обложка в html есть", '<img class="cover"' in t)
    ok("картинка вшита, а не ссылкой",
       t.count("data:image/png;base64,") == 2, re.findall(r'src="([^"]{0,24})', t))
    ok("пропавшую не выдумали", "нет-такой.png" not in t)
    ok("разметка сноски в html развёрнута",
       "<i>Vesti</i>" in t and "&lt;i&gt;" not in t,
       t[t.find("Vesti") - 30:t.find("Vesti") + 30])
    ok("внутренняя ссылка в html без имени файла",
       'href="#s02.b0001"' in t and 'id="s02.b0001"' in t,
       re.findall(r'href="#[^"]*"', t))
    shutil.rmtree(d)

    # --- чтение: страница без текста и выбор обложки.
    # Обложка часто стоит на странице, где ничего, кроме <img>, и без явной
    # пометки в описи. Прежде такая страница выбрасывалась целиком («страница
    # без текста»), картинка пропадала, а обложкой становилась первая
    # попавшаяся картинка манифеста — на живой книге это был логотип
    # издательства.
    from booktrans import extract as E
    d = tempfile.mkdtemp()
    src = os.path.join(d, "к.epub")
    with zipfile.ZipFile(src, "w") as zz:
        zz.writestr("mimetype", "application/epub+zip")
        zz.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>')
        zz.writestr("content.opf",
                    '<?xml version="1.0"?><package version="2.0" '
                    'xmlns="http://www.idpf.org/2007/opf" unique-identifier="u">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<dc:title>К</dc:title><dc:identifier id="u">x</dc:identifier></metadata>'
                    '<manifest>'
                    '<item id="l" href="logo.png" media-type="image/png"/>'
                    '<item id="c" href="art.png" media-type="image/png"/>'
                    '<item id="p1" href="p1.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="p2" href="p2.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="p3" href="p3.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="p4" href="p4.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="p1"/><itemref idref="p2"/>'
                    '<itemref idref="p4"/>'
                    '<itemref idref="p3"/></spine></package>')
        X = '<html xmlns="http://www.w3.org/1999/xhtml"><body>%s</body></html>'
        zz.writestr("p1.xhtml", X % '<img src="art.png" alt=""/>')
        zz.writestr("p2.xhtml", X % '<p>Глава и её текст, вполне длинный.</p>')
        zz.writestr("p3.xhtml", X % '<img src="logo.png" alt=""/>')
        # оглавление без слова «Contents»: короткие блоки-ссылки в другие файлы
        zz.writestr("p4.xhtml", X % "".join(
            f'<p><a href="p2.xhtml#c{i}">Chapter {i}</a></p>' for i in range(1, 9)))
        zz.writestr("art.png", OTHER)
        zz.writestr("logo.png", PIXEL)
    meta, blocks, cover, images = E.read_book(src, styles=None)
    ok("обложка — картинка первой бестекстовой страницы", cover == OTHER,
       len(cover or b""))
    ok("обложка не задвоена блоком",
       "art.png" not in [b["text"] for b in blocks if b["kind"] == "image"])
    ok("вклейка без текста уцелела блоком",
       "logo.png" in [b["text"] for b in blocks if b["kind"] == "image"],
       [b["text"] for b in blocks if b["kind"] == "image"])
    # Страница «Chapter 1…Chapter 8» из одних ссылок — оглавление, а не
    # главы: прежде она уходила в перевод заголовками без содержания.
    ok("оглавление без слова Contents выброшено",
       not [b for b in blocks if "Chapter" in b.get("text", "")]
       and meta["_cleaned"]["junk_pages"] == 1,
       (meta["_cleaned"], [b["text"][:16] for b in blocks]))
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
