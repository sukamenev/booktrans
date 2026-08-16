"""Запись готовой книги: fb2, epub, html, txt.

На вход всем — единый набор: метаданные, блоки с уже подставленным переводом,
сноски и картинки. Формат выбирается по расширению выходного файла.
"""
import base64
import os
import re
import zipfile
from xml.sax.saxutils import escape
from .tune import TEX_HEAD, CAPTION, MATH_MAX

FB2_INLINE = {
    "i": "emphasis", "em": "emphasis",
    "b": "strong", "strong": "strong",
    "s": "strikethrough", "del": "strikethrough", "strike": "strikethrough",
    "sub": "sub", "sup": "sup", "code": "code",
}
HTML_INLINE = {"i": "i", "em": "i", "b": "b", "strong": "b",
               "s": "s", "del": "s", "strike": "s",
               "sub": "sub", "sup": "sup", "code": "code"}


def _mime(name):
    return "image/png" if name.lower().endswith(".png") else "image/jpeg"


# Голый адрес в тексте. `&` к этому месту уже превращён в `&amp;`, оттого в
# наборе точка с запятой: без неё адрес с запросом обрывался бы на середине.
# Кончаться адрес обязан буквой, цифрой или косой чертой — иначе в него уйдёт
# точка или скобка, стоящая после него в предложении.
BARE_URL = re.compile(r"(https?://[a-zA-Z0-9./\-?=_&;#%~+]+[a-zA-Z0-9/])")


def _autolink(s, tag):
    """Сделать ссылками голые адреса. Внутрь готовых тегов не заходим: адрес
    в `href` уже ссылка, и обернуть его второй раз значит сломать разметку."""
    out = []
    for part in re.split(r"(<[^>]+>)", s):
        out.append(part if part.startswith("<")
                   else BARE_URL.sub(rf'<{tag}="\1">\1</a>', part))
    return "".join(out)


def _inline(s, table, links=None):
    """Экранирует текст, разворачивая разрешённую разметку и ссылки."""
    s = escape(s)
    for src, dst in table.items():
        s = s.replace(f"&lt;{src}&gt;", f"<{dst}>").replace(f"&lt;/{src}&gt;", f"</{dst}>")
    if links:
        for i, url in enumerate(links, 1):
            href = escape(url, {'"': "&quot;"})
            tag = "a l:href" if table is FB2_INLINE else "a href"
            s = s.replace(f"&lt;a{i}&gt;", f'<{tag}="{href}">')
            s = s.replace(f"&lt;/a{i}&gt;", "</a>")
    s = re.sub(r"&lt;/?a\d+&gt;", "", s)
    tag = "a l:href" if table is FB2_INLINE else "a href"
    s = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', rf'<{tag}="\2">\1</a>', s)
    
    img_tag = "image l:href" if table is FB2_INLINE else "img src"
    s = re.sub(r"&lt;imgmath name=&quot;([^&]+)&quot;&gt;", rf'<{img_tag}="\1"/>', s)
    if table is FB2_INLINE:
        s = re.sub(r"&lt;imgmath name=&quot;([^&]+)&quot;&gt;", rf'<image l:href="#\1"/>', s)
    else:
        s = re.sub(r"&lt;imgmath name=&quot;([^&]+)&quot;&gt;", rf'<img src="\1" alt="math"/>', s)
    return _autolink(s, tag)


def _plain(s):
    return re.sub(r"<[^>]+>", "", s)


def _md_inline(s):
    """Конвертирует markdown-разметку в теги перед рендерингом.

    Нужно для текста сносок переводчика: модель пишет *курсив* и **жирный**,
    а не XML-теги. extract.py делает это для блоков книги, но сноски генерируются
    позже — их нужно конвертировать здесь, до передачи в _tex/_inline.
    """
    s = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', s)
    s = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'\*(.*?)\*', r'<i>\1</i>', s)
    s = re.sub(r'_(.*?)_', r'<i>\1</i>', s)
    return s


# ---------------------------------------------------------------- txt

def _cells(row):
    """Ячейки строки таблицы. Разделитель — « | »; экранированный не считается."""
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def span_attr(spans, i, j, n):
    """Атрибуты слияния для ячейки, если они есть и подходят к строке.

    Слияния хранятся отдельно от текста и через модель не проходят. Но текст
    через неё проходит, и число ячеек в строке она изменить может; тогда
    слияния этой строки не применяются вовсе — таблица выйдет без них, а не
    поехавшей. Остальные строки это не затрагивает.
    """
    row = (spans or [])[i] if spans and i < len(spans) else None
    if not row or len(row) != n:
        return ""
    c, r = row[j]
    return (f' colspan="{c}"' if c > 1 else "") + (f' rowspan="{r}"' if r > 1 else "")


def _table_html(text, inline, spans=None):
    rows = []
    for i, row in enumerate(text.splitlines()):
        cells = _cells(row)
        rows.append("<tr>" + "".join(
            f"<td{span_attr(spans, i, j, len(cells))}>{_inline(c, inline)}</td>"
            for j, c in enumerate(cells)) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def write_txt(path, meta, items, notes, images, note_prefix, st=None, **kw):
    st = st or {}
    out = []
    title = meta.get("title_target") or meta.get("title") or ""
    if title:
        out += [title.upper(), ""]
    if meta.get("author_target") or meta.get("author"):
        out += [meta.get("author_target") or meta["author"], ""]
    out.append("")
    nums = {b: i for i, b in enumerate(notes, 1)}
    for kind, text, bid, links, *sp in items:
        if kind == "title":
            out += ["", "", _plain(text).upper(), ""]
        elif kind == "subtitle":
            out += [_plain(text), ""]
        elif kind == "gap":
            out.append("")
        elif kind == "break":
            out += ["", "* * *", ""]
        elif kind == "verse":
            out.append("    " + _plain(text))
        elif kind == "code":
            out += ["    " + l for l in text.splitlines()] + [""]
        elif kind == "table":
            out += ["   ".join(_cells(row)) for row in text.splitlines()] + [""]
        elif kind == "image":
            out.append("[" + st.get("illustration", "иллюстрация: {alt}").format(alt=text) + "]")
        else:
            mark = f" [{nums[bid]}]" if bid in nums else ""
            out.append(_plain(text) + mark)
            out.append("")
    if notes:
        out += ["", "", st.get("notes_title", "Примечания").upper(), ""]
        for i, (bid, txt) in enumerate(notes.items(), 1):
            body = txt["text"] if isinstance(txt, dict) else txt
            src_only = isinstance(txt, dict) and txt.get("source_only")
            if not src_only and not body.startswith(note_prefix):
                body = note_prefix + body
            body = _md_inline(body)
            out += [f"{i}. {body}", ""]
    open(path, "w", encoding="utf-8").write("\n".join(out).strip() + "\n")


# ---------------------------------------------------------------- html

CSS = """body{max-width:38em;margin:2em auto;padding:0 1em;
font:1.05em/1.6 Georgia,'Times New Roman',serif;color:#1a1a1a;background:#fdfdfb}
h1{font-size:1.6em;margin:2.5em 0 .3em;font-weight:normal;letter-spacing:.05em}
h2{font-size:1em;color:#666;font-weight:normal;margin:.2em 0 1.5em;font-style:italic}
p{margin:0 0 .9em;text-align:justify;hyphens:auto}
p.v{margin:0 0 .1em 2em;text-align:left;font-style:italic;text-indent:-1em}
p.gap{margin:0;height:.9em}
pre{font:.85em/1.4 'DejaVu Sans Mono',Consolas,monospace;background:#f4f4f0;
border-left:3px solid #ddd;padding:.6em .8em;margin:1.2em 0;overflow-x:auto;
white-space:pre-wrap;word-wrap:break-word}
img{max-width:100%;height:auto;display:block;margin:1.5em auto}
img.cover{max-height:80vh;width:auto;margin:0 auto 2em}
table{border-collapse:collapse;margin:1.2em 0}
td,th{border:1px solid #ccc;padding:.3em .6em;text-align:left;vertical-align:top}
hr{border:0;text-align:center;margin:2em 0}hr:after{content:'* * *';color:#999}
sup a{text-decoration:none;color:#06c;font-size:.75em}
.notes{margin-top:4em;border-top:1px solid #ddd;padding-top:1em;font-size:.9em;color:#444}
.notes li{margin-bottom:.6em}
@media(prefers-color-scheme:dark){body{background:#16161a;color:#ddd}
h2{color:#999}.notes{color:#bbb;border-color:#333}sup a{color:#7ab}}"""


def write_html(path, meta, items, notes, images, note_prefix, st=None, cover=None, **kw):
    st = st or {}
    targets = _targets(items)
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    code = meta.get("target_lang", "ru")
    o = ["<!doctype html>", f'<html lang="{code}"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{escape(title)}</title><style>{CSS}</style>",
         '<script>MathJax = {tex: {inlineMath: [["$", "$"]]}};</script>',
         '<script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>',
         "</head><body>"]
    if cover:
        # Обложка первой, как в epub и fb2. Файл самодостаточный, поэтому
        # картинка идёт в него же — отдельным файлом рядом она бы потерялась
        # при пересылке, а ради неё html и выбирают.
        o.append(f'<img class="cover" src="data:{_cover_mime(cover)[0]};base64,'
                 f'{base64.b64encode(cover).decode()}" alt="">')
    o.append(f"<h1>{escape(title)}</h1>")
    if author:
        o.append(f"<p><em>{escape(author)}</em></p>")
    nums = {b: i for i, b in enumerate(notes, 1)}
    for kind, text, bid, links, *sp in items:
        if kind == "title":
            level = min(sp[1] if len(sp) > 1 and sp[1] is not None else 1, 6)
            o.append(f"<h{level}{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h{level}>")
        elif kind == "subtitle":
            o.append(f"<h2{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h2>")
        elif kind == "gap":
            o.append('<p class="gap"></p>')
        elif kind == "break":
            o.append("<hr>")
        elif kind == "image" and text in images:
            data = base64.b64encode(images[text]).decode()
            o.append(f'<img src="data:{_mime(text)};base64,{data}" alt="">')
        elif kind == "image" and re.match(r"https?://|//", text):
            o.append(f'<img src="{escape(text)}" alt="">')   # картинка по сети
        elif kind == "table":
            o.append(_table_html(text, HTML_INLINE, sp[0] if sp else None))
        elif kind == "verse":
            o.append(f'<p class="v">{_inline(text, HTML_INLINE)}</p>')
        elif kind == "code":
            o.append(f"<pre>{escape(text)}</pre>")
        elif kind == "p":
            mark = (f'<sup><a href="#n{nums[bid]}" id="r{nums[bid]}">[{nums[bid]}]</a></sup>'
                    if bid in nums else "")
            o.append(f"<p{_at(bid, targets)}>"
                     f"{_inline(text, HTML_INLINE, links)}{mark}</p>")
    if notes:
        o.append('<div class="notes"><h2>' + escape(st.get("notes_title", "Примечания")) + '</h2><ol>')
        for i, (bid, txt) in enumerate(notes.items(), 1):
            body = txt["text"] if isinstance(txt, dict) else txt
            src_only = isinstance(txt, dict) and txt.get("source_only")
            if not src_only and not body.startswith(note_prefix):
                body = note_prefix + body
            body = _md_inline(body)
            o.append(f'<li id="n{i}">{escape(body)} <a href="#r{i}">↑</a></li>')
        o.append("</ol></div>")
    o.append("</body></html>")
    open(path, "w", encoding="utf-8").write("\n".join(o))


# ---------------------------------------------------------------- epub

def _cover_mime(raw):
    """Чем на самом деле является обложка. Раньше ей приписывался jpeg, а
    лежать там может и png: строгая читалка на такое ругается."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if raw[:3] == b"GIF":
        return "image/gif", "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "image/jpeg", "jpg"


def _at(bid, targets):
    return f' id="{bid}"' if bid in targets else ""


def _targets(items):
    """Блоки, на которые ссылаются изнутри книги: им нужен `id`."""
    return {u[1:] for x in items for u in (x[3] or ()) if u.startswith("#")}


def write_epub(path, meta, items, notes, images, note_prefix, st=None, cover=None, **kw):
    items = _render_math_to_images(items, images)
    st = st or {}
    targets = _targets(items)
    code = meta.get("target_lang", "ru")
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    uid = meta.get("uid") or "booktrans-" + re.sub(r"\W+", "-", title.lower())[:40]
    nums = {b: i for i, b in enumerate(notes, 1)}

    # режем на файлы по заголовкам: читалки грузят книгу по частям
    parts, cur, titles = [], [], []
    for it in items:
        is_major_title = False
        if it[0] == "title":
            sp = it[4:]
            level = sp[1] if len(sp) > 1 and sp[1] is not None else 1
            if int(level) <= 2:
                is_major_title = True
                
        if is_major_title and cur:
            parts.append(cur)
            cur = []
        cur.append(it)
    if cur:
        parts.append(cur)
    for p in parts:
        t = next((_plain(x[1]) for x in p if x[0] == "title"), "")
        titles.append(t or st.get("part", "Часть {n}").format(n=len(titles) + 1))

    def xhtml(body, head):
        return ('<?xml version="1.0" encoding="utf-8"?>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{code}"><head>'
                f'<title>{escape(head)}</title>'
                '<link rel="stylesheet" href="style.css" type="text/css"/>'
                f"</head><body>{body}</body></html>")

    # В epub книга разложена по файлам, и «#блок» внутри одного из них до
    # цели в другом не доведёт: адрес обязан нести имя файла.
    where = {x[2]: f"ch{i:03d}.xhtml" for i, part in enumerate(parts, 1)
             for x in part}
    files = {}
    for i, part in enumerate(parts, 1):
        o = []
        for kind, text, bid, links, *sp in part:
            links = [where.get(u[1:], "") + u if u.startswith("#") else u
                     for u in (links or [])] or None
            if kind == "title":
                level = min(sp[1] if len(sp) > 1 and sp[1] is not None else 1, 6)
                o.append(f"<h{level}{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h{level}>")
            elif kind == "subtitle":
                o.append(f"<h2{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h2>")
            elif kind == "gap":
                o.append('<p class="gap"></p>')
            elif kind == "break":
                o.append("<hr/>")
            elif kind == "image" and text in images:
                o.append(f'<img src="img/{escape(text)}" alt=""/>')
            elif kind == "table":
                o.append(_table_html(text, HTML_INLINE, sp[0] if sp else None))
            elif kind == "verse":
                o.append(f'<p class="v">{_inline(text, HTML_INLINE)}</p>')
            elif kind == "code":
                o.append(f"<pre>{escape(text)}</pre>")
            elif kind == "p":
                mark = (f'<sup><a href="notes.xhtml#n{nums[bid]}">[{nums[bid]}]</a></sup>'
                        if bid in nums else "")
                o.append(f"<p{_at(bid, targets)}>"
                         f"{_inline(text, HTML_INLINE, links)}{mark}</p>")
        files[f"ch{i:03d}.xhtml"] = xhtml("".join(o), titles[i - 1])

    if notes:
        o = ["<h1>" + escape(st.get("notes_title", "Примечания")) + "</h1><ol>"]
        for i, (bid, txt) in enumerate(notes.items(), 1):
            body = txt["text"] if isinstance(txt, dict) else txt
            src_only = isinstance(txt, dict) and txt.get("source_only")
            if not src_only and not body.startswith(note_prefix):
                body = note_prefix + body
            body = _md_inline(body)
            o.append(f'<li id="n{i}">{escape(body)}</li>')
        o.append("</ol></div>")
        files["notes.xhtml"] = xhtml("".join(o), st.get("notes_title", "Примечания"))

    nav = ['<?xml version="1.0" encoding="utf-8"?>',
           '<html xmlns="http://www.w3.org/1999/xhtml" '
           f'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{code}"><head>'
           f'<title>{escape(st.get("toc_title", "Оглавление"))}</title></head><body>'
           f'<nav epub:type="toc"><h1>{escape(st.get("toc_title", "Оглавление"))}</h1><ol>']
    for i, t in enumerate(titles, 1):
        nav.append(f'<li><a href="ch{i:03d}.xhtml">{escape(t)}</a></li>')
    if notes:
        nav.append(f'<li><a href="notes.xhtml">{escape(st.get("notes_title", "Примечания"))}</a></li>')
    nav.append("</ol></nav></body></html>")

    # Обложка нередко лежит и среди картинок книги: тогда второй копии под
    # именем cover.jpg не нужно — на одной живой книге она весила 2,3 МБ из
    # 5,4. Хватит пометки на той, что уже есть.
    same = next((n for n, raw in images.items() if raw == cover), None) if cover else None
    # В книгу кладём только то, на что в ней ссылаются, плюс обложку. Реклама
    # и титульная картинка из исходника обычно висят на выброшенных блоках, а
    # весят как половина книги: на одной живой epub — 860 КБ из 3,2 МБ.
    used = {t for k, t, *_ in items if k == "image"}
    images = {n: r for n, r in images.items() if n in used or n == same}
    # Отчёт тот же, что у fb2: из набора в книгу идёт не всё, и разойтись эти
    # два числа могут по-разному — картинка на выброшенном блоке, картинка,
    # которой в наборе не оказалось, разделитель глав на два десятка мест.
    spots = [t for k, t, *_ in items if k == "image"]
    n_img = sum(1 for n in images if n in used)
    if n_img and kw.get("log") and kw.get("lang"):
        kw["log"]("  " + kw["lang"].T("images_n", n_img, len(spots)))
    man, spine = [], []
    cmime, cext = _cover_mime(cover) if cover else ("", "")
    cover_href = f"img/{same}" if same else (f"img/cover.{cext}" if cover else "")
    cover_id = "cover-img" if cover and not same else ""
    if cover and not same:
        man.append(f'<item id="cover-img" href="img/cover.{cext}" '
                   f'media-type="{cmime}" properties="cover-image"/>')
    for i in range(1, len(parts) + 1):
        man.append(f'<item id="ch{i}" href="ch{i:03d}.xhtml" '
                   'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i}"/>')
    if notes:
        man.append('<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="notes"/>')
    for j, name in enumerate(images):
        cov = ' properties="cover-image"' if name == same else ""
        if name == same:
            cover_id = f"img{j}"
        man.append(f'<item id="img{j}" href="img/{escape(name)}" '
                   f'media-type="{_mime(name)}"{cov}/>')
    # Обложка отдельной страницей и первой в череде. Пометки `cover-image` в
    # описи мало: по ней читалка берёт картинку для полки, а книгу открывает
    # сразу на первой главе — обложки читатель так и не увидит.
    if cover_href:
        man.insert(0, '<item id="cover" href="cover.xhtml" '
                      'media-type="application/xhtml+xml"/>')
        spine.insert(0, '<itemref idref="cover"/>')
    man.append('<item id="css" href="style.css" media-type="text/css"/>')
    man.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               'properties="nav"/>')

    seq = ""
    if meta.get("series"):
        seq = (f'<meta property="belongs-to-collection" id="s">{escape(meta["series"])}</meta>'
               '<meta refines="#s" property="collection-type">series</meta>')
        if meta.get("series_no"):
            seq += f'<meta refines="#s" property="group-position">{meta["series_no"]}</meta>'
    pub_tag = f'<dc:publisher>{escape(meta["publisher"])}</dc:publisher>' if meta.get("publisher") else ""
    date_tag = f'<dc:date>{escape(str(meta["year"]))}</dc:date>' if meta.get("year") else ""
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="uid"><metadata '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">{escape(uid)}</dc:identifier>'
           f'<dc:title>{escape(title)}</dc:title>'
           f'<dc:creator>{escape(author)}</dc:creator>'
           f'{pub_tag}{date_tag}'
           f'<dc:language>{code}</dc:language>'
           f'<dc:contributor>Booktrans</dc:contributor>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
           # Обложку читалки ищут двумя способами: `properties="cover-image"`
           # в описи — по-нынешнему, и `meta name="cover"` — по-старому.
           # Второй понимают все, первый — не все, поэтому пишем оба.
           + (f'<meta name="cover" content="{cover_id}"/>' if cover_id else "")
           + seq + "</metadata><manifest>" + "".join(man) +
           '</manifest><spine>' + "".join(spine) + "</spine></package>")

    # Epub — это zip, и текст в нём надо жать: без сжатия книга весила на
    # три четверти своей разметки больше, чем нужно. Картинки уже сжаты
    # своим форматом, им это ничего не даёт, но и не портит.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype обязан идти первым и без сжатия — этого требует стандарт
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", "\n".join(nav))
        z.writestr("OEBPS/style.css", CSS)
        for name, body in files.items():
            z.writestr("OEBPS/" + name, body)
        for name, raw in images.items():
            z.writestr("OEBPS/img/" + name, raw)
        if cover and not same:
            z.writestr(f"OEBPS/img/cover.{cext}", cover)
        if cover_href:
            z.writestr("OEBPS/cover.xhtml",
                       xhtml(f'<img class="cover" src="{escape(cover_href)}" alt=""/>',
                             title))



# ---------------------------------------------------------------- tex

# Знаки, которые TeX читает как разметку. Промах здесь опаснее прочих: `%`
# молча съедает остаток строки, и текст пропадает при собранном без ошибок
# файле.
TEX_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
           "&": r"\&", "#": r"\#", "^": r"\textasciicircum{}", "_": r"\_",
           "~": r"\textasciitilde{}", "%": r"\%"}
# Формула или просто два доллара в строке. Распознавание оборачивает формулы
# в `$…$` (см. prompts/ocr.md), но `$` в книге чаще знак денег: «с $5 до $10»
# отдавало в формулу обычную прозу — курсивом, а с кириллицей внутри lualatex
# отказывался собирать книгу вовсе. Формулу отличаем по письму и по знакам:
# в ней латиница и служебные знаки TeX, а не слова живого языка.
MATH_SIGN = re.compile(r"[\\^_{}]|[=+*/<>]\s*\d|\d\s*[=+*/<>]")


def is_math(s):
    s = s.strip()
    if not s or len(s) > MATH_MAX:
        return False
    # Кириллица, греческий, иврит, восточное письмо: в формуле их не бывает —
    # там `\alpha`, а не «α». Латиница ниже 0x250 остаётся: `x`, `mc`, `\sin`.
    if any(c.isalpha() and ord(c) > 0x24F for c in s):
        return False
    return len(s) <= 3 or bool(MATH_SIGN.search(s))


TEX_INLINE = {"i": "textit", "em": "textit", "b": "textbf", "strong": "textbf",
              "s": "sout", "del": "sout", "strike": "sout",
              "sub": "textsubscript", "sup": "textsuperscript", "code": "texttt"}

# Шрифты берутся по покрытию письменностей: Noto закрывает почти все, DejaVu
# — латиницу, кириллицу и греческий. Ставим первый, какой найдётся: fontspec
# умеет искать по имени семейства, установленного в системе.
TEX_FONTS = {
    "rm": ["Noto Serif", "DejaVu Serif", "Liberation Serif"],
    "sf": ["Noto Sans", "DejaVu Sans", "Liberation Sans"],
    "tt": ["Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono"],
}
# Для письменностей, которых в основных шрифтах нет.
TEX_SCRIPT = {"ja": "Noto Serif CJK JP", "zh": "Noto Serif CJK SC",
              "ko": "Noto Serif CJK KR", "hi": "Noto Serif Devanagari"}
TEX_BABEL = {"ru": "russian", "en": "english", "de": "german", "fr": "french",
             "es": "spanish", "hi": "hindi"}


def _tex(s, links=None, notes_dict=None):
    """Текст в TeX: экранирование и разворот инлайновой разметки."""
    # Декодируем HTML-сущности, которые OCR-модель вставляет для отступов
    # (например, &nbsp;&nbsp;&nbsp;&nbsp; в предметном указателе). Делаем
    # это до экранирования: иначе & → \&amp; и сущность не распознаётся.
    import html
    s = html.unescape(s)
    marks = {}
    # Ярлыки разметки прячем, чтобы экранирование их не тронуло.
    def hide(m):
        marks[len(marks)] = m.group()
        return f"\x00{len(marks) - 1}\x00"

    if notes_dict:
        # Resolve footnotes BEFORE escaping!
        # <a1>[1]</a1> where url for a1 is in notes_dict.
        def replace_fn(m):
            i = int(m.group(1))
            url = (links or [None] * i)[i - 1] if i <= len(links or []) else None
            if url and url in notes_dict:
                # When converting to footnote, we do NOT want to escape the content 
                # because `notes_dict[url]` already contains markdown/links that will be processed 
                # separately inside _tex if it was passed to it, but here it's processed recursively!
                # We use marks dictionary to hide the footnote from the escaping below!
                fn_content = _tex(notes_dict[url], links=None)
                marks[len(marks)] = r"\footnote{%s}" % fn_content
                return f"\x00{len(marks) - 1}\x00"
            return m.group(0)
        s = re.sub(r"<a(\d+)>.*?</a\1>", replace_fn, s)

    # Формулы прячем от экранирования: внутри `$…$` знаки TeX не гости, а
    # хозяева. Одиночные доллары — только если между ними правда формула.
    s = re.sub(r"\$\$(.*?)\$\$", hide, s, flags=re.DOTALL)
    s = re.sub(r"\$([^$]*?)\$",
               lambda m: hide(m) if is_math(m.group(1)) else m.group(0),
               s, flags=re.DOTALL)
    
    def hide_md_link(m):
        url = m.group(2).replace("%", r"\%").replace("#", r"\#")
        marks[len(marks)] = r"\href{%s}{" % url
        marks[len(marks)] = "}"
        return f"\x00{len(marks)-2}\x00{m.group(1)}\x00{len(marks)-1}\x00"

    s = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', hide_md_link, s)

    def hide_bare_url(m):
        url = m.group(1).replace("%", r"\%").replace("#", r"\#")
        marks[len(marks)] = r"\url{%s}" % url
        return f"\x00{len(marks)-1}\x00"

    s = re.sub(r'(https?://[a-zA-Z0-9./\-?=_&]+[a-zA-Z0-9/])', hide_bare_url, s)

    s = re.sub(r"</?(?:%s|a\d+)>" % "|".join(TEX_INLINE), hide, s)
    s = "".join(TEX_ESC.get(c, c) for c in s)

    def back(m):
        t = marks[int(m.group(1))]
        if t.startswith("$") or t.startswith(r"\footnote") or t.startswith(r"\href") or t.startswith(r"\url") or t == "}":
            return t
        name = re.match(r"</?([a-z]+)", t).group(1)
        close = t.startswith("</")
        if name == "a":
            i = int(re.match(r"</?a(\d+)>", t).group(1))
            url = (links or [None] * i)[i - 1] if i <= len(links or []) else None
            if not url:
                return ""
            if close:
                return "}"
            return r"\href{%s}{" % url.replace("%", r"\%").replace("#", r"\#")
        return "}" if close else "\\%s{" % TEX_INLINE[name]

    s = re.sub(r"\x00(\d+)\x00", back, s)
    return s


def _tex_preamble(meta, st, code):
    """Преамбула: шрифты подбираются на месте сборки.

    Ставить один шрифт нельзя — у читателя может не оказаться именно его, а
    падение на первой строке хуже, чем другая гарнитура. `\\IfFontExistsTF`
    перебирает: Noto закрывает почти все письменности, DejaVu — латиницу,
    кириллицу и греческий, Liberation есть почти везде.
    """
    fonts = []
    for cmd, names in (("setmainfont", TEX_FONTS["rm"]),
                       ("setsansfont", TEX_FONTS["sf"]),
                       ("setmonofont", TEX_FONTS["tt"])):
        line = ""
        for n in reversed(names):
            line = "\\IfFontExistsTF{%s}{\\%s{%s}}{%s}" % (n, cmd, n, line)
        fonts.append(line)
    extra = TEX_SCRIPT.get(code)
    if extra:
        fonts.append("\\IfFontExistsTF{%s}{\\setmainfont{%s}}{}" % (extra, extra))
    lang = TEX_BABEL.get(code)
    psize = meta.get("page_size")
    if psize:
        geom = rf"\usepackage[paperwidth={psize[0]}bp,paperheight={psize[1]}bp,margin=18mm]{{geometry}}"
    else:
        geom = r"\usepackage[a5paper,margin=18mm]{geometry}"
        
    out = [r"\documentclass[10pt,oneside]{book}",
           r"% Собирается lualatex или xelatex: fontspec нужен ради письменностей,",
           r"% которых pdflatex не знает. Заголовки рубленым, текст с засечками,",
           r"% листинги моноширинным — как в книгах и заведено.",
           r"\usepackage{fontspec}",
           *fonts,
           geom,
           r"\usepackage{graphicx}",
           r"\usepackage[normalem]{ulem}",
           r"\usepackage{multirow}",
           r"\usepackage{titlesec}",
           r"\usepackage{tabulary}",
           # Длинный адрес — вторая причина строк, вылезающих за поле:
           # разбить его без этого нечем.
           r"\PassOptionsToPackage{hyphens}{url}",
           r"\usepackage[colorlinks=true,allcolors=blue]{hyperref}",
           # Последнее средство: где перенести нельзя, TeX растянет пробелы,
           # а не вытолкнет строку на поля.
           # Правил переноса для языка может не оказаться (в texlive они
           # отдельным пакетом), и тогда длинное слово вытолкнет строку на
           # поле. Разрядка между словами — цена меньшая, чем текст за полем.
           r"\emergencystretch=4em",
           r"\sloppy",
           r"\hbadness=10000"]
    if lang:
        # Babel подключаем, только если языковые данные и правда стоят: без
        # них он падает на первой же строке. Без переносов русское слово не
        # разбить, и строка вылезает за поле — на одной книге так поехали
        # сотни строк.
        out.append(r"\IfFileExists{babel-%s.tex}{\usepackage[%s]{babel}}"
                   r"{\IfFileExists{%sb.ldf}{\usepackage[%s]{babel}}{}}"
                   % (lang, lang, lang, lang))
    # Свои подписи вместо английских: babel закомментирован, и без этого
    # оглавление в русской книге называется Contents.
    out += [r"\renewcommand{\contentsname}{%s}"
            % _tex(st.get("toc_title", "Оглавление")),
            r"\setlength{\parindent}{1.2em}",
            r"\setlength{\parskip}{0.5em}",
            r"\titleformat{\chapter}{\huge\sffamily\bfseries\raggedright}{}{0pt}{}",
            r"\titleformat{\section}{\Large\sffamily\bfseries\raggedright}{}{0pt}{}",
            r"\titleformat{\subsection}{\large\sffamily\raggedright}{}{0pt}{}",
            r"\titleformat{\subsubsection}{\normalsize\sffamily\bfseries\raggedright}{}{0pt}{}",
            r"\setcounter{secnumdepth}{-1}",
            # Колонтитул — название текущего раздела. Класс `book` держит там
            # имя главы, а глав у нас нет: без этого на каждой странице висело
            # бы «Оглавление», поставленное \tableofcontents.
            # Свой колонтитул: у myheadings он вполкегля основного текста.
            # В книжной вёрстке колонтитул набирают мельче — иначе он спорит
            # с текстом за внимание.
            r"\makeatletter",
            r"\newcommand{\ps@bt}{%",
            r"  \renewcommand{\@oddhead}{\footnotesize\slshape\rightmark"
            r"\hfil\thepage}%",
            r"  \renewcommand{\@evenhead}{\footnotesize\slshape\thepage"
            r"\hfil\rightmark}%",
            r"  \renewcommand{\@oddfoot}{}\renewcommand{\@evenfoot}{}}",
            r"\makeatother",
            r"\pagestyle{bt}",
            r"\begin{document}"]
    return "\n".join(out)


def write_tex(path, meta, items, notes, images, note_prefix, st=None, cover=None, **kw):
    """Книга исходником LaTeX. Компиляция — дело читателя: шрифты и движок у
    всех свои, и обещать, что соберётся везде, нельзя."""
    st = st or {}
    code = meta.get("target_lang", "ru")
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    
    note_seq = kw.get("note_seq") or []
    notes_map = kw.get("notes_map") or {}
    new_anchor_to_text = {a: t for a, n, t, src in note_seq if src}
    notes_dict = {f"#{orig_id}": new_anchor_to_text[new_a]
                  for orig_id, (new_a, _) in notes_map.items()
                  if new_a in new_anchor_to_text}

    o = [_tex_preamble(meta, st, code)]
    if cover:
        name = "%s.img/cover.%s" % (os.path.splitext(os.path.basename(path))[0],
                                    _cover_mime(cover)[1])
        o.append(r"\begin{titlepage}\centering")
        o.append(r"\includegraphics[width=\textwidth,height=0.8\textheight,"
                 r"keepaspectratio]{%s}\end{titlepage}" % name)
    o.append(r"\title{%s}" % _tex(title, notes_dict=notes_dict))
    
    # Append edition and publisher under the author if present
    author_tex = _tex(author, notes_dict=notes_dict)
    edition = meta.get("edition")
    publisher = meta.get("publisher")
    extras = []
    if edition:
        extras.append(_tex(edition, notes_dict=notes_dict))
    if publisher:
        extras.append(_tex(publisher, notes_dict=notes_dict))
    if extras:
        author_tex += r" \\ \vspace{0.5cm} " + r" \\ ".join(extras)
    
    o.append(r"\author{%s}" % author_tex)
    
    # Без этого LaTeX ставит на титул сегодняшнее число, и оно читается как
    # дата издания. Если год известен, ставим его, иначе пусто.
    year = meta.get("year") or ""
    o.append(r"\date{%s}" % _tex(year, notes_dict=notes_dict))
    o.append(r"\maketitle")
    nums = {b: i for i, b in enumerate(notes, 1)}
    toc_added = False
    in_abstract = False
    for kind, text, bid, links, *sp in items:
        if not toc_added and not bid.startswith("_about"):
            o.append(r"\clearpage")
            o.append(r"\tableofcontents")
            o.append(r"\clearpage")
            toc_added = True

        if kind == "title":
            t = _tex(text, links, notes_dict=notes_dict)
            clean_text = re.sub(r"<a\d+>.*?</a\d+>", "", text).strip()
            clean_t = _tex(clean_text)
            
            if in_abstract:
                o.append(r"\end{quotation}")
                in_abstract = False
                
            is_abstract = clean_text.lower() in (
                "аннотация", "abstract", "абстракт", 
                "résumé", "zusammenfassung", "resumen", 
                "riassunto", "resumo", "samenvatting", 
                "streszczenie"
            )
            
            if bid.startswith("_") and not is_abstract:
                o.append(r"\clearpage")
            head = " ".join(clean_text.split())
            if len(head) > TEX_HEAD:
                head = head[:TEX_HEAD].rsplit(" ", 1)[0] + "…"
            level = sp[1] if len(sp) > 1 and sp[1] is not None else 1
            star = "*" if bid.startswith("_about") else ""
            
            if is_abstract:
                if star: o.append(r"\chapter*{%s}\markright{%s}" % (t, _tex(head)))
                else: o.append(r"\chapter[%s]{%s}\markright{%s}" % (clean_t, t, _tex(head)))
                o.append(r"\begin{quotation}")
                in_abstract = True
            elif level == "1" or level == 1:
                if star: o.append(r"\chapter*{%s}\markright{%s}" % (t, _tex(head)))
                else: o.append(r"\chapter[%s]{%s}\markright{%s}" % (clean_t, t, _tex(head)))
            elif level == "2" or level == 2:
                if star: o.append(r"\section*{%s}" % t)
                else: o.append(r"\section[%s]{%s}" % (clean_t, t))
            elif level == "3" or level == 3:
                if star: o.append(r"\subsection*{%s}" % t)
                else: o.append(r"\subsection[%s]{%s}" % (clean_t, t))
            else:
                if star: o.append(r"\subsubsection*{%s}" % t)
                else: o.append(r"\subsubsection[%s]{%s}" % (clean_t, t))
        elif kind == "subtitle":
            o.append(r"\subsection*{%s}" % _tex(text, notes_dict=notes_dict))
        elif kind == "gap":
            o.append(r"\medskip")
        elif kind == "break":
            o.append(r"\begin{center}* * *\end{center}")
        elif kind == "image" and text in images and _tex_pic(text):
            o.append(r"\begin{center}\includegraphics[width=0.9\textwidth,"
                     r"keepaspectratio]{%s.img/%s}\end{center}"
                     % (os.path.splitext(os.path.basename(path))[0], text))
        elif kind == "verse":
            o.append(r"\begin{verse}%s\end{verse}"
                     % r"\\".join(_tex(l, notes_dict=notes_dict) for l in text.splitlines()))
        elif kind == "code":
            o.append(r"\begin{verbatim}" + "\n" + text + "\n" + r"\end{verbatim}")
        elif kind == "table":
            o.append(_tex_table(text, sp[0] if sp else None))
        elif kind == "note":
            continue                       # сноски встают по месту, ниже
        else:
            note = ""
            if bid in nums:
                v = notes[bid]
                body = v["text"] if isinstance(v, dict) else v
                if not body.startswith(note_prefix):
                    body = note_prefix + body
                body = _md_inline(body)
                note = r"\footnote{%s}" % _tex(body, notes_dict=notes_dict)
            p_tex = _tex(text, links, notes_dict=notes_dict)
            if bid.startswith("_about") or bid.startswith("_details"):
                p_tex = r"\noindent " + p_tex
            o.append(p_tex + note + "\n")
    if in_abstract:
        o.append(r"\end{quotation}")
    o.append(r"\end{document}")
    open(path, "w", encoding="utf-8").write("\n".join(o) + "\n")
    # Папка своя у каждой книги: собери несколько в один каталог — и
    # одноимённые cover.jpg, author.jpg, logo.jpg затрут друг друга.
    d = os.path.splitext(os.path.abspath(path))[0] + ".img"
    used = {t for k, t, *_ in items if k == "image"}
    if cover or (images and used):
        os.makedirs(d, exist_ok=True)
    for name, raw in (images or {}).items():
        if name in used and _tex_pic(name):
            open(os.path.join(d, name), "wb").write(raw)
    if cover:
        open(os.path.join(d, "cover." + _cover_mime(cover)[1]), "wb").write(cover)


def _tex_pic(name):
    """Годится ли картинка для LaTeX. `graphicx` знает png, jpeg и pdf; gif и
    webp он не откроет, и сборка встанет — такую картинку пропускаем."""
    return name.lower().endswith((".png", ".jpg", ".jpeg", ".pdf"))


def _tex_table(text, spans=None):
    rows = [_cells(r) for r in text.splitlines()]

    def width(i, cells):
        sp = (spans or [])[i] if spans and i < len(spans) else None
        if not sp or len(sp) != len(cells):
            return len(cells)
        return sum(c[0] for c in sp)
    n = max((width(i, r) for i, r in enumerate(rows)), default=1)
    out = [r"\begin{center}\footnotesize\setlength{\extrarowheight}{3pt}\begin{tabulary}{\textwidth}{|" + "|".join(["L"] * n) + r"|}\hline"]
    for i, cells in enumerate(rows):
        line = []
        for j, c in enumerate(cells):
            body = _tex(c)
            sp = (spans or [])[i][j] if spans and i < len(spans) \
                and len(spans[i]) == len(cells) else [1, 1]
            if sp[1] > 1:
                body = r"\multirow{%d}{*}{%s}" % (sp[1], body)
            if sp[0] > 1:
                # Need to add vertical bars to multicolumn as well if it spans multiple cols
                # Use proportional p{} column to allow text wrapping instead of rigid l
                body = r"\multicolumn{%d}{|p{\dimexpr\linewidth*%d/%d\relax}|}{%s}" % (sp[0], sp[0], n, body)
            line.append(body)
        # Строку добиваем пустыми ячейками: у TeX число столбцов объявлено
        # заранее, и короткая строка ломает всю таблицу.
        line += [""] * (n - width(i, cells))
        out.append(" & ".join(line) + r" \\ \hline")
    out.append(r"\end{tabulary}\end{center}")
    return "\n".join(out)



TEX_ENGINES = ("lualatex", "xelatex")


def write_pdf(path, meta, items, notes, images, note_prefix, st=None,
              cover=None, tmp=None, **kw):
    """Pdf — это собранный LaTeX, и собирает его TeX, а не мы.

    Своей вёрстки конвейер не делает: перенос строк, разбиение на страницы,
    встраивание шрифтов — это месяцы работы и всё равно хуже, чем у TeX.
    Поэтому рядом кладётся `.tex`, и он остаётся лежать в любом случае: не
    нашлось движка или сборка не удалась — у человека на руках исходник и
    строка, которой его собрать.
    """
    from . import lang
    import shutil
    import subprocess
    import tempfile
    # Черновики — в рабочую папку книги, а не рядом с готовым файлом: там
    # им и место, и человек не разбирает потом, что из этого книга, а что
    # подсобное. Наружу выходит один pdf.
    d = tmp or tempfile.mkdtemp(prefix="booktrans-")
    os.makedirs(d, exist_ok=True)
    tex = os.path.join(d, os.path.splitext(os.path.basename(path))[0] + ".tex")
    write_tex(tex, meta, items, notes, images, note_prefix, st, cover, **kw)
    engine = next((e for e in TEX_ENGINES if shutil.which(e)), None)
    if not engine:
        print("  " + lang.T("pdf_no_engine", os.path.basename(tex),
                            " или ".join(TEX_ENGINES)))
        return
    print("  " + lang.T("pdf_run", engine), end="", flush=True)
    for _ in range(2):        # второй проход наполняет оглавление
        subprocess.run([engine, "-interaction=nonstopmode",
                        os.path.basename(tex)], cwd=d,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = os.path.splitext(os.path.basename(tex))[0]
    made = os.path.join(d, base + ".pdf")
    if os.path.exists(made):
        shutil.copyfile(made, path)
        os.unlink(made)
    for ext in (".aux", ".toc", ".out", ".log"):
        f = os.path.join(d, base + ext)
        # Запись об ошибках оставляем только когда книга не собралась: без
        # неё разбираться не с чем.
        if os.path.exists(f) and not (ext == ".log" and not os.path.exists(path)):
            os.unlink(f)
    print(lang.T("pdf_done" if os.path.exists(path) else "pdf_failed", tex))






import xml.etree.ElementTree as ET

def _render_math_to_images(items, images):
    import subprocess, tempfile, hashlib
    math_re = re.compile(r'\$\$(.*?)\$\$|\$([^\$]+?)\$')
    formulas = set()
    for item in items:
        if item[0] in ("p", "title", "table", "verse"):
            for m in math_re.finditer(item[1]):
                f = m.group(1)
                if f is None and not is_math(m.group(2)):
                    continue          # два доллара в прозе — не формула
                formulas.add((f if f is not None else m.group(2)).strip())
    if not formulas:
        return items
    formulas = sorted(list(formulas))
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return items

    formula_images = {}
    with tempfile.TemporaryDirectory() as td:
        tex_path = os.path.join(td, "math.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(r"""\documentclass[12pt]{article}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage[active,tightpage,displaymath,math]{preview}
\begin{document}
""")
            for form in formulas:
                clean_form = form.replace('\n', ' ')
                f.write(r"\begin{preview}$" + clean_form + r"$\end{preview}" + "\n")
            f.write(r"\end{document}")
        subprocess.run(["lualatex", "-interaction=nonstopmode", "math.tex"], cwd=td, capture_output=True)
        pdf_path = os.path.join(td, "math.pdf")
        if os.path.exists(pdf_path):
            pdf = pdfium.PdfDocument(pdf_path)
            import io
            from PIL import Image
            for i in range(min(len(pdf), len(formulas))):
                page = pdf[i]
                bitmap = page.render(scale=3)
                pil_image = bitmap.to_pil()
                img_name = f"math_{hashlib.md5(formulas[i].encode()).hexdigest()[:8]}.png"
                img_byte_arr = io.BytesIO()
                pil_image.save(img_byte_arr, format='PNG')
                images[img_name] = img_byte_arr.getvalue()
                formula_images[formulas[i]] = img_name

    if not formula_images:
        return items

    new_items = []
    for item in items:
        if item[0] in ("p", "title", "table", "verse"):
            t = item[1]
            def repl(m):
                if m.group(1) is None and not is_math(m.group(2)):
                    return m.group(0)
                f = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
                if f in formula_images:
                    name = formula_images[f]
                    # We output the unescaped fake tag which _inline will escape, 
                    # wait! If we output `<imgmath name="..."/>`, _inline does escape(s).
                    # So we should output `<imgmath name="..."/>` and _inline will see `&lt;imgmath name=&quot;...&quot;/&gt;`
                    return f'<imgmath name="{name}"/>'
                return m.group(0)
            t = math_re.sub(repl, t)
            new_items.append((item[0], t) + item[2:])
        else:
            new_items.append(item)
    return new_items

def write_fb2(dest, meta, items, notes, images, note_prefix, st=None, cover=None, **kw):
    items = _render_math_to_images(items, images)
    blocks = kw['blocks']
    tr = kw['tr']
    partial = kw['partial']
    log = kw['log']
    note_seq = kw['note_seq']
    nid = kw['nid']
    notes_map = kw['notes_map']
    lang = kw['lang']
    about_head = kw['about_head']
    about_body = kw['about_body']
    details_head = kw['details_head']
    details_body = kw['details_body']
    esc = kw['esc']
    span_attr = kw['span_attr']
    code = meta.get("target_lang", "ru")

    def aid(b):
        return f' id="{b["id"]}"' if b["id"] in nid or b["id"] in (notes_map or {}) else ""
    o = []
    w = o.append
    w('<?xml version="1.0" encoding="utf-8"?>')
    w('<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
      'xmlns:l="http://www.w3.org/1999/xlink">')
    w("<description><title-info>")
    w(f"<genre>{esc(meta.get('genre', 'prose_contemporary'))}</genre>")
    au = (meta.get("author_target") or meta.get("author") or "").split()
    first, last = (au[0], " ".join(au[1:])) if len(au) > 1 else ("", au[0] if au else "")
    w(f"<author><first-name>{esc(first)}</first-name><last-name>{esc(last)}</last-name></author>")
    name = (meta.get("title_target") or meta.get("title")
            or st.get("untitled", "Без названия"))
    w(f"<book-title>{esc(name)}</book-title>")
    if cover:
        w('<coverpage><image l:href="#cover.jpg"/></coverpage>')
    w(f"<lang>{esc(meta.get('target_lang', 'ru'))}</lang>")
    if meta.get("lang"):
        w(f"<src-lang>{esc(meta['lang'][:2])}</src-lang>")
    w(f"<translator><nickname>{esc(st.get('translator', 'машинный перевод'))}"
      "</nickname></translator>")
    if meta.get("series"):
        num = f' number="{meta["series_no"]}"' if meta.get("series_no") else ""
        w(f'<sequence name="{esc(meta["series"])}"{num}/>')
    w("</title-info><document-info>")
    w("<author><nickname>booktrans</nickname></author>")
    w(f"<date>{esc(str(meta.get('year', '')))}</date>")
    w(f"<id>{esc(meta.get('uid', 'booktrans-001'))}</id><version>1.0</version>")
    w("</document-info>")
    if meta.get("title"):
        w("<publish-info>")
        w(f"<book-name>{esc(meta['title'])}</book-name>")
        if meta.get("author"):
            w(f"<author>{esc(meta['author'])}</author>")
        if meta.get("publisher"):
            w(f"<publisher>{esc(meta['publisher'])}</publisher>")
        if meta.get("year"):
            w(f"<year>{esc(str(meta['year'])[:4])}</year>")
        if meta.get("isbn"):
            w(f"<isbn>{esc(meta['isbn'])}</isbn>")
        w("</publish-info>")
    w("</description><body>")

    # --- о переводе: читатель должен узнать это сразу, а не в конце ---
    # Строки берём из файла целевого языка: немецкой книге русская врезка
    # ни к чему.
    head, body = about_head, about_body
    w("<section>")
    w(f"<title><p>{esc(head)}</p></title>")
    for line in body:
        # Пустая строка — отбивка между смысловыми кусками раздела: сплошной
        # стеной абзацев его читать нельзя. Пустым абзацем, а не
        # `<empty-line/>`: читалки рисуют его в три-четыре знака высотой, а
        # абзац без текста занимает ровно строку.
        if not line:
            w("<p></p>")
            continue
        # Через общий разворот разметки, как и всё прочее: раздел собран не из
        # блоков книги, но и выделение, и ссылки в нём должны выйти такими же.
        w(f"<p>{_inline(line, FB2_INLINE)}</p>")
    w("</section>")

    open_sec = False
    in_poem = False

    def close_poem():
        nonlocal in_poem
        if in_poem:
            w("</stanza></poem>")
            in_poem = False

    was_title, after_img = False, False
    for b in blocks:
        text = tr.get(b["id"], "")
        if after_img and b["kind"] != "image":
            # Короткая строка сразу за снимком — это подпись под ним: её
            # оставляем прижатой, а отбиваем уже после неё.
            if b["kind"] == "p" and 0 < len(text) <= CAPTION and open_sec:
                w(f"<p>{esc(text, b.get('links'), notes_map)}</p>")
                w("<empty-line/>")
                after_img = False
                continue
            w("<empty-line/>")
            after_img = False
        if b["kind"] in ("p", "verse", "code") and text.strip():
            was_title = False       # пустая строка и картинка заголовки не разделяют
        if b["kind"] == "title":
            close_poem()
            if was_title:
                # Второй заголовок подряд — это подзаголовок: имя автора под
                # названием, время и место. Открой он свою секцию, предыдущая
                # осталась бы пустой, а в оглавлении читалки — пустой строкой.
                w(f"<subtitle{aid(b)}>{esc(text, b.get('links'), notes_map)}</subtitle>")
            else:
                if open_sec:
                    w("</section>")
                # Заголовок сам `id` нести не может — по схеме его нет у
                # <title>, — поэтому цель ссылки помечается на секции.
                w(f"<section{aid(b)}>")
                w(f"<title><p>{esc(text, b.get('links'), notes_map)}</p></title>")
                open_sec = True
            was_title = True
            continue
        elif b["kind"] == "subtitle":
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            w(f"<subtitle{aid(b)}>{esc(text)}</subtitle>")
        elif b["kind"] == "verse":
            # Стихи в fb2 — это <poem>/<stanza>/<v>. Такой ветки не было
            # вовсе, и стихотворные строки просто исчезали из книги: у одной
            # из 139 абзацев осталось 28.
            if not open_sec:
                w("<section>")
                open_sec = True
            if not in_poem:
                w("<poem><stanza>")
                in_poem = True
            w(f"<v>{esc(text, b.get('links'), notes_map)}</v>")
        elif b["kind"] == "code":
            # В fb2 нет <pre>: листинг идёт строкой на абзац, а отступ держится
            # неразрывными пробелами — обычные читалка схлопнет.
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            for line in text.splitlines() or [""]:
                pre = len(line) - len(line.lstrip(" "))
                w(f"<p><code>{' ' * pre}{esc(line.strip())}</code></p>")
        elif b["kind"] == "table":
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            w("<table>")
            for i, row in enumerate(text.splitlines()):
                cells = [c.strip() for c in re.split(r"(?<!\\)\|", row)]
                w("<tr>" + "".join(
                    f"<td{span_attr(b.get('spans'), i, j, len(cells))}>"
                    f"{esc(c.replace(chr(92) + '|', '|'), b.get('links'), notes_map)}</td>"
                    for j, c in enumerate(cells)) + "</tr>")
            w("</table>")
        elif b["kind"] == "break":
            if in_poem:
                w("</stanza><stanza>")       # пустая строка делит строфы
            elif open_sec:
                w("<empty-line/>")
        elif b["kind"] == "image":
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            if b["text"] in (images or {}):
                # Пустая строка перед: по схеме картинка блочная, но читалки
                # вольны прижать её к соседнему абзацу, и текст оказывается с
                # ней в одной строке.
                if not after_img:
                    w("<empty-line/>")
                w(f'<image l:href="#{esc(b["text"])}"/>')
                after_img = True
                continue
        elif b["kind"] == "note":
            close_poem()
            continue                      # авторские сноски идут в примечания
        elif b["kind"] == "p":
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            a = ""
            if b["id"] in nid:
                num = next(n for k, n, _, _ in note_seq if k == nid[b["id"]])
                a = f'<a l:href="#{nid[b["id"]]}" type="note">[{num}]</a>'
            w(f"<p{aid(b)}>{esc(text, b.get('links'), notes_map)}{a}</p>")
    close_poem()
    if open_sec:
        w("</section>")

    # --- детали перевода: в конце, а не в начале. Читателю они не нужны,
    # а тому, кто ищет, чья работа перед ним, нужны обязательно.
    dhead, dbody = details_head, details_body
    if dhead:
        w("<section>")
        w(f"<title><p>{esc(dhead)}</p></title>")
        for line in dbody:
            w(f"<p>{esc(line)}</p>")
        w("</section>")
    w("</body>")

    if note_seq:
        # сноски, добавленные конвейером, помечаем: читатель должен видеть,
        # что пояснение не от автора. Авторские сноски из книги метки не
        # получают — они и есть авторский текст.
        pref = note_prefix
        w('<body name="notes">')
        # Заголовок у тела, а не у секций: по схеме fb2 это `body = (image?,
        # title?, epigraph*, section+)`, и оглавление читалка строит по нему.
        # Без него раздел попадал в оглавление безымянным — как называется
        # список сносок, каждая программа решала сама. Строка та же, что у
        # txt, html и epub: они её берут давно, fb2 один её не брал.
        w(f'<title><p>{esc(st.get("notes_title", "Примечания"))}</p></title>')
        for anchor, num, body, from_source in note_seq:
            if not from_source and not body.startswith(pref):
                body = pref + body
            w(f'<section id="{anchor}"><title><p>{num}</p></title>'
              f'<p>{esc(body)}</p></section>')
        w("</body>")

    def binary(name, raw):
        mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        data = base64.b64encode(raw).decode()
        w(f'<binary id="{esc(name)}" content-type="{mime}">')
        for i in range(0, len(data), 76):
            w(data[i:i + 76])
        w("</binary>")

    if cover:
        binary("cover.jpg", cover)
    spots = [b for b in blocks if b["kind"] == "image"]
    used = {b["text"] for b in spots}
    n_img = 0
    for name, raw in (images or {}).items():
        if name in used and name != "cover.jpg":
            binary(name, raw)
            n_img += 1
    if n_img:
        # Двух чисел мало кому нужно, но одного тут недостаточно: из файла
        # картинок извлекается больше, чем попадает в книгу (обложка, знак
        # издательства), а разделитель глав — это одна картинка на два
        # десятка мест. Одно число читалось как пропажа остальных.
        log("  " + lang.T("images_n", n_img, len(spots)))
    w("</FictionBook>")

    open(dest, "w", encoding="utf-8").write("\n".join(o))
    try:
        ET.parse(dest)
    except ET.ParseError as e:
        raise SystemExit(f"собранный fb2 невалиден: {e}")
    # О готовом файле говорит `build_book`, один раз и для всех форматов.

def write_fb2_zip(dest, *a, **kw):
    """Fb2 в архиве — так его и раздают библиотеки, и так его понимают почти
    все читалки. Вдвое легче: fb2 — простой XML-файл, сжимать его самому
    нечем, а картинки лежат в нём текстом, в base64, и весят на треть больше
    своего.

    Внутри архива — одноимённый файл без `.zip`: читалка ищет именно его.
    """
    plain = dest[:-len(".zip")]
    write_fb2(plain, *a, **kw)
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(plain, os.path.basename(plain))
    finally:
        os.unlink(plain)


WRITERS = {".tex": write_tex, ".pdf": write_pdf, ".txt": write_txt,
           ".html": write_html, ".htm": write_html, ".epub": write_epub,
           ".fb2": write_fb2, ".fb2.zip": write_fb2_zip}
