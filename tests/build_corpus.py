"""Постоянный набор книг для проверки конвейера.

Две главы в книге и около 600 слов в главе: две — чтобы проверялся перенос
конспекта между кусками, короткие — чтобы полный прогон стоил недорого.

Режем разбором дерева, а не текстом: обрезка регулярками ломает вёрстку,
и книга перестаёт читаться.
"""
import os, re, shutil, subprocess, sys, zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
from booktrans import extract as E

# ОТКУДА брать книги. Набор собирается из чужих книг, поэтому в репозиторий
# они не попадают и пути у каждого свои. Порядок такой: переменная окружения,
# затем файл tests/corpus.paths, затем умолчание рядом с проектом.
#
#   BT_BOOKS      — папка с книгами, которые названы в этом скрипте поимённо
#   BT_LIB        — домашняя библиотека: оттуда берутся fb2, txt и pdf.
#                   Внутри ожидается привычная раскладка по папкам
#   BT_GUTENBERG  — куда скачаны книги «Проекта Гутенберг»: pg2701, pg2707
#
# КУДА складывается готовый набор, не настраивается: всегда tests/corpus,
# рядом с этим скриптом. Опись manifest.json описывает именно его.
#
# Файл corpus.paths — те же имена, по строке «ИМЯ = значение». Он не в
# репозитории: у каждого свои пути.


def _paths():
    conf = {}
    p = os.path.join(HERE, "corpus.paths")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = os.path.expanduser(v.strip())

    def get(name, default):
        return os.path.expanduser(os.environ.get(name) or conf.get(name) or default)

    return (get("BT_BOOKS", os.path.dirname(os.path.dirname(HERE))),
            get("BT_LIB", "~/Biblioteka"),
            get("BT_GUTENBERG", "/tmp/gut"))


OUT = os.path.join(HERE, "corpus")
OPF = "{http://www.idpf.org/2007/opf}"
T, B, GUT = _paths()
for name, path in (("BT_BOOKS", T), ("BT_LIB", B)):
    if not os.path.isdir(path):
        sys.exit(f"нет папки {path}\n"
                 f"Задайте {name} в окружении или в {HERE}/corpus.paths")
WORDS = 600


def synthetic(dst):
    """Книга, собранная из ничего: чат через <br/>, форум с датами, стихи,
    концевые ссылки и библиография. Чужого текста в ней нет, а значит она
    проверяет ровно те правила, что ошибались на живых книгах: перенос
    строки внутри абзаца и «не переводить» по датам и двоеточиям."""
    prose = ("The harbour woke slowly that morning, and the gulls argued over "
             "the same crust for the better part of an hour while the ferry "
             "waited for a passenger who never came. ") * 3
    chat = ("<p><b>Ann:</b> are you there<br/><b>Bob:</b> yes, give me a minute"
            "<br/><b>Ann:</b> the ferry is leaving<br/><b>Bob:</b> coming</p>")
    forum = "".join(
        f"<p>► <b>{who}</b> (Harbour Regular) Replied on July {6 + i % 2}th, 2011: "
        f"The theory makes sense, but the numbers are off, and nobody says who "
        f"pays for the cleanup.</p>"
        for i, who in enumerate(["Ekul", "AverageAlexandros", "Lolitup", "Robby",
                                 "TheGnat", "Chrome", "TRJ", "Nod"]))
    cites = "".join(
        f'<p>"a phrase number {i}": Hans Moravec, Mind Children: The Future of '
        f'Robot Intelligence (Harvard University Press, {1980 + i}), {i * 7}.</p>'
        for i in range(1, 9))
    bib = "".join(
        f"<p>{i}. Lilly, J. C. {1950 + i}. On the whales, part {i}. "
        f"Journal of Marine Talk {i}: {i * 3}–{i * 3 + 9}.</p>" for i in range(1, 9))
    chapters = [
        ("Chapter One", "<p>" + prose + "</p>" * 1 + "<p>" + prose + "</p>" + chat
         + "<p>" + prose + "</p>"),
        ("Chapter Two", "<p>" + prose + "</p>" + forum + "<p>" + prose + "</p>"),
        ("Notes", cites),
        ("Bibliography", bib),
    ]
    files = {}
    for i, (title, body) in enumerate(chapters, 1):
        files[f"OEBPS/ch{i}.xhtml"] = (
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>")
    items = "".join(f'<item id="ch{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>'
                    for i in range(1, len(chapters) + 1))
    refs = "".join(f'<itemref idref="ch{i}"/>' for i in range(1, len(chapters) + 1))
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Harbour Notes</dc:title><dc:creator>Nobody</dc:creator><dc:language>en</dc:language>'
        '<dc:identifier id="id">urn:uuid:synthetic-harbour</dc:identifier></metadata>'
        f'<manifest>{items}</manifest><spine>{refs}</spine></package>')
    files["META-INF/container.xml"] = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>')
    with zipfile.ZipFile(dst, "w") as o:
        o.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        for n, d in files.items():
            o.writestr(n, d)


if "--synthetic" in sys.argv:
    os.makedirs(OUT, exist_ok=True)
    synthetic(os.path.join(OUT, "13_synthetic_markup_en.epub"))
    print("собрана только синтетическая книга")
    sys.exit(0)
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)


def _words(el):
    return len(" ".join(el.itertext()).split())


def trim_xhtml(data, words, max_notes=99, keep_classes=()):
    """Оставить в теле около `words` слов прозы, не тронув вёрстку.

    Сноски и абзацы, которые на них ссылаются, не режем никогда: ради них
    книга в наборе и лежит.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    ns = "{http://www.w3.org/1999/xhtml}"
    body = root.find(ns + "body")
    if body is None:
        body = root.find("body")
    if body is None:
        return data

    OPS = "{http://www.idpf.org/2007/ops}type"
    parent = {id(ch): el for el in root.iter() for ch in el}
    protect = set()
    seen_notes = [0]
    for el in root.iter():
        t = (el.get(OPS) or el.get("role") or "").replace("doc-", "")
        cls = (el.get("class") or "").strip()
        href = el.get("href") or ""
        if t in ("noteref", "footnote", "endnote", "note") or cls == "foot" \
                or cls in keep_classes \
                or re.search(r"#(linknote|fn|note)", href):
            seen_notes[0] += 1
            if seen_notes[0] > max_notes * 2:   # ссылка и сама сноска
                continue
            node = el
            while node is not None:
                protect.add(id(node))
                node = parent.get(id(node))
    left = [words]

    def prune(node):
        for ch in list(node):
            tag = re.sub(r"\{.*?\}", "", ch.tag)
            if tag not in ("p", "div", "section", "blockquote", "ol", "ul", "table"):
                continue
            if id(ch) in protect:
                # сам не трогаем, но внутрь заходим: иначе защита одного
                # абзаца сохраняет весь раздел целиком
                if tag != "p":
                    prune(ch)
                continue
            if left[0] <= 0:
                node.remove(ch)
                continue
            if tag == "p":
                left[0] -= _words(ch)
            else:
                prune(ch)
    prune(body)
    ET.register_namespace("", "http://www.w3.org/1999/xhtml")
    ET.register_namespace("epub", "http://www.idpf.org/2007/ops")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def cut_epub(src, dst, keep_names, words=WORDS, max_notes=99, keep_classes=()):
    z = zipfile.ZipFile(src)
    opf_path = [n for n in z.namelist() if n.endswith('.opf')][0]
    base = os.path.dirname(opf_path)
    root = ET.fromstring(z.read(opf_path))
    man, spine = root.find(OPF + 'manifest'), root.find(OPF + 'spine')
    keep_ids, keep_files, trim_files = set(), set(), set()
    for it in list(man):
        href, mt = it.get('href') or '', it.get('media-type') or ''
        p = E._zpath(base, href)
        if mt.startswith('image/') or 'css' in mt or href.endswith('.ncx') \
                or 'nav' in (it.get('properties') or ''):
            keep_ids.add(it.get('id')); keep_files.add(p); continue
        if any(k in os.path.basename(href) for k in keep_names):
            keep_ids.add(it.get('id')); keep_files.add(p); trim_files.add(p)
        else:
            man.remove(it)
    for r in list(spine):
        if r.get('idref') not in keep_ids:
            spine.remove(r)
    ET.register_namespace('', 'http://www.idpf.org/2007/opf')
    with zipfile.ZipFile(dst, 'w') as o:
        o.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        for n in z.namelist():
            if n == 'mimetype':
                continue
            if n == opf_path:
                o.writestr(n, ET.tostring(root, encoding='utf-8', xml_declaration=True)); continue
            if n.startswith('META-INF/') or n in keep_files:
                d = z.read(n)
                if n in trim_files:
                    d = trim_xhtml(d, words, max_notes, keep_classes)
                o.writestr(n, d)
            elif not n.endswith(('.xhtml', '.html', '.htm')):
                o.writestr(n, z.read(n))
    return dst


FB = "{http://www.gribuser.ru/xml/fictionbook/2.0}"


def cut_fb2(src, dst, words=WORDS, chapters=2, keep_images=2, poem_lines=0):
    txt = E._decode(open(src, 'rb').read())
    txt = re.sub(r'^\s*<\?xml[^>]*\?>', '<?xml version="1.0" encoding="utf-8"?>', txt, count=1)
    root = ET.fromstring(txt)
    bodies = root.findall(FB + 'body')
    main = bodies[0]
    secs = main.findall(FB + 'section')
    for s in secs[chapters:]:
        main.remove(s)
    # Разделы бывают вложенными: режем по всему дереву, а не только сверху.
    left = [words * chapters]

    def prune(node):
        for el in list(node):
            tag = el.tag.replace(FB, '')
            if tag == 'section':
                prune(el)
                if not [x for x in el if x.tag.replace(FB, '') != 'title']:
                    node.remove(el)       # раздел, из которого всё вырезано
            elif tag == 'poem':
                continue                  # стихи не режем: ради них книга и взята
            elif tag in ('p', 'cite', 'empty-line', 'subtitle'):
                if left[0] <= 0:
                    node.remove(el)
                else:
                    left[0] -= _words(el)
    prune(main)
    # Стихи режем отдельно и строфами целиком: разорванное четверостишие
    # лишает смысла саму проверку, ради которой книга взята. Оставляем по
    # две строфы в первых четырёх стихотворениях и по одной в остальных —
    # многострофные нужны для проверки разреза по границе строфы, но держать
    # их все дорого: стихи обходятся вдесятеро дороже прозы.
    if poem_lines:
        seen = 0
        for parent in main.iter():
            for poem in list(parent):
                if poem.tag != FB + 'poem':
                    continue
                seen += 1
                left = poem_lines * 2 if seen <= 4 else poem_lines
                for st in list(poem):
                    if st.tag != FB + 'stanza':
                        continue
                    vs = st.findall(FB + 'v')
                    if left <= 0:
                        poem.remove(st)
                        continue
                    for v in vs[left:]:
                        st.remove(v)
                    left -= min(len(vs), left)

    live = set()
    for a in main.iter():
        h = a.get('{http://www.w3.org/1999/xlink}href') or ''
        if h.startswith('#'):
            live.add(h[1:])
    for b in bodies[1:]:
        if (b.get('name') or '') == 'notes':
            for s in b.findall(FB + 'section'):
                if s.get('id') not in live:
                    b.remove(s)
        else:
            root.remove(b)
    for bn in root.findall(FB + 'binary')[keep_images:]:
        root.remove(bn)
    ET.register_namespace('', 'http://www.gribuser.ru/xml/fictionbook/2.0')
    ET.register_namespace('l', 'http://www.w3.org/1999/xlink')
    ET.ElementTree(root).write(dst, encoding='utf-8', xml_declaration=True)
    return dst


made = []
for i, (name, extra) in enumerate([('Semiosis', []), ('Interference', ['adcard']),
                                   ('Usurpation', ['acknowledgments'])], 1):
    keep = ['cover', 'title', 'copyrightnotice', 'dedication', 'chapter1', 'chapter2',
            'abouttheauthor', 'newsletter', 'torad', 'copyright'] + extra
    made.append(cut_epub(f'{T}/{name}_-_Sue_Burke.epub',
                         f'{OUT}/{i:02d}_{name}_en.epub', keep))
made.append(cut_epub(GUT + '/pg2707.epub', f'{OUT}/05_Herodotus_notes_en.epub',
                     ['-h-0.htm'], 1200, max_notes=6))
made.append(cut_epub(GUT + '/pg2701.epub', f'{OUT}/06_MobyDick_en.epub', ['-h-1.htm'], 1200))
made.append(cut_fb2(f'{B}/lib/Fantastic/Lem/Станислав Лем. Собрание сочинений в 17 томах/13. Молох.fb2',
                    f'{OUT}/07_Lem_notes_ru.fb2'))
made.append(cut_fb2(f'{B}/lib/Fantastic/Белянин/Белянин 1 Моя жена - ведьма.fb2',
                    f'{OUT}/08_Belyanin_verse_ru.fb2', poem_lines=4))
# 11. Neverness: две главы со стихами и эпиграфами, вёрстка из безымянных кусков
zind = f'{T}/Neverness_-_David_Zindell.epub'
import zipfile as _zf
_names = [n for n in _zf.ZipFile(zind).namelist() if n.endswith(('.html', '.xhtml'))]
made.append(cut_epub(zind, f'{OUT}/11_Neverness_verse_en.epub',
                     ['titlepage', os.path.basename(_names[11]), os.path.basename(_names[21])],
                     words=500,
                     keep_classes=('c10', 'c13', 'c14', 'c15', 'c16', 'c19')))

src_txt = subprocess.run(['find', B, '-name', 'afranij.txt'], capture_output=True, text=True).stdout.strip()
open(f'{OUT}/09_plain_cp1251_ru.txt', 'w', encoding='cp1251', errors='replace').write(
    ' '.join(E._decode(open(src_txt, 'rb').read()).split(' ')[:1200]))
made.append(f'{OUT}/09_plain_cp1251_ru.txt')
pdf = subprocess.run(['find', B, '-name', 'dnl23.pdf'], capture_output=True, text=True).stdout.strip().split('\n')[0]
subprocess.run(['qpdf', pdf, '--pages', '.', '1-4', '--', f'{OUT}/10_paper_en.pdf'], check=False)
made.append(f'{OUT}/10_paper_en.pdf')

# Издательский pdf целиком: главы, примечания, указатель, описания картинок и
# шесть с половиной тысяч ссылок. Резать его нельзя — задняя часть опознаётся
# по тому, что стоит в конце книги, а у обрезка конец другой. Раз берём целиком,
# то и копируем как есть: qpdf переписал бы структуру файла, а проверять надо
# ровно то, что попадёт в руки читателю.
book = subprocess.run(['find', B, T, '-name', 'super_agers*.pdf'],
                      capture_output=True, text=True).stdout.strip().split('\n')[0]
if book:
    shutil.copy(book, f'{OUT}/12_backmatter_en.pdf')
    made.append(f'{OUT}/12_backmatter_en.pdf')
print("собрано:", len(made))
