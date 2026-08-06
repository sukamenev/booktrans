"""Запись готовой книги: fb2, epub, html, txt.

На вход всем — единый набор: метаданные, блоки с уже подставленным переводом,
сноски и картинки. Формат выбирается по расширению выходного файла.
"""
import base64
import os
import re
import zipfile
from xml.sax.saxutils import escape

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
    return re.sub(r"&lt;/?a\d+&gt;", "", s)


def _plain(s):
    return re.sub(r"<[^>]+>", "", s)


# ---------------------------------------------------------------- txt

def _cells(row):
    """Ячейки строки таблицы. Разделитель — « | »; экранированный не считается."""
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def _table_html(text, inline):
    rows = []
    for row in text.splitlines():
        rows.append("<tr>" + "".join(f"<td>{_inline(c, inline)}</td>"
                                     for c in _cells(row)) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def write_txt(path, meta, items, notes, images, note_prefix, st=None):
    st = st or {}
    out = []
    title = meta.get("title_target") or meta.get("title") or ""
    if title:
        out += [title.upper(), ""]
    if meta.get("author_target") or meta.get("author"):
        out += [meta.get("author_target") or meta["author"], ""]
    out.append("")
    nums = {b: i for i, b in enumerate(notes, 1)}
    for kind, text, bid, links in items:
        if kind == "title":
            out += ["", "", _plain(text).upper(), ""]
        elif kind == "subtitle":
            out += [_plain(text), ""]
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
            out += [f"{i}. {body}", ""]
    open(path, "w", encoding="utf-8").write("\n".join(out).strip() + "\n")


# ---------------------------------------------------------------- html

CSS = """body{max-width:38em;margin:2em auto;padding:0 1em;
font:1.05em/1.6 Georgia,'Times New Roman',serif;color:#1a1a1a;background:#fdfdfb}
h1{font-size:1.6em;margin:2.5em 0 .3em;font-weight:normal;letter-spacing:.05em}
h2{font-size:1em;color:#666;font-weight:normal;margin:.2em 0 1.5em;font-style:italic}
p{margin:0 0 .9em;text-align:justify;hyphens:auto}
p.v{margin:0 0 .1em 2em;text-align:left;font-style:italic;text-indent:-1em}
pre{font:.85em/1.4 'DejaVu Sans Mono',Consolas,monospace;background:#f4f4f0;
border-left:3px solid #ddd;padding:.6em .8em;margin:1.2em 0;overflow-x:auto;
white-space:pre-wrap;word-wrap:break-word}
img{max-width:100%;height:auto;display:block;margin:1.5em auto}
hr{border:0;text-align:center;margin:2em 0}hr:after{content:'* * *';color:#999}
sup a{text-decoration:none;color:#06c;font-size:.75em}
.notes{margin-top:4em;border-top:1px solid #ddd;padding-top:1em;font-size:.9em;color:#444}
.notes li{margin-bottom:.6em}
@media(prefers-color-scheme:dark){body{background:#16161a;color:#ddd}
h2{color:#999}.notes{color:#bbb;border-color:#333}sup a{color:#7ab}}"""


def write_html(path, meta, items, notes, images, note_prefix, st=None):
    st = st or {}
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    code = meta.get("target_lang", "ru")
    o = ["<!doctype html>", f'<html lang="{code}"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{escape(title)}</title><style>{CSS}</style></head><body>"]
    o.append(f"<h1>{escape(title)}</h1>")
    if author:
        o.append(f"<p><em>{escape(author)}</em></p>")
    nums = {b: i for i, b in enumerate(notes, 1)}
    for kind, text, bid, links in items:
        if kind == "title":
            o.append(f"<h1>{_inline(text, HTML_INLINE)}</h1>")
        elif kind == "subtitle":
            o.append(f"<h2>{_inline(text, HTML_INLINE)}</h2>")
        elif kind == "break":
            o.append("<hr>")
        elif kind == "image" and text in images:
            data = base64.b64encode(images[text]).decode()
            o.append(f'<img src="data:{_mime(text)};base64,{data}" alt="">')
        elif kind == "image" and re.match(r"https?://|//", text):
            o.append(f'<img src="{escape(text)}" alt="">')   # картинка по сети
        elif kind == "table":
            o.append(_table_html(text, HTML_INLINE))
        elif kind == "verse":
            o.append(f'<p class="v">{_inline(text, HTML_INLINE)}</p>')
        elif kind == "code":
            o.append(f"<pre>{escape(text)}</pre>")
        elif kind == "p":
            mark = (f'<sup><a href="#n{nums[bid]}" id="r{nums[bid]}">[{nums[bid]}]</a></sup>'
                    if bid in nums else "")
            o.append(f"<p>{_inline(text, HTML_INLINE, links)}{mark}</p>")
    if notes:
        o.append('<div class="notes"><h2>' + escape(st.get("notes_title", "Примечания")) + '</h2><ol>')
        for i, (bid, txt) in enumerate(notes.items(), 1):
            body = txt["text"] if isinstance(txt, dict) else txt
            src_only = isinstance(txt, dict) and txt.get("source_only")
            if not src_only and not body.startswith(note_prefix):
                body = note_prefix + body
            o.append(f'<li id="n{i}">{escape(body)} <a href="#r{i}">↑</a></li>')
        o.append("</ol></div>")
    o.append("</body></html>")
    open(path, "w", encoding="utf-8").write("\n".join(o))


# ---------------------------------------------------------------- epub

def write_epub(path, meta, items, notes, images, note_prefix, st=None, cover=None):
    st = st or {}
    code = meta.get("target_lang", "ru")
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    uid = meta.get("uid") or "booktrans-" + re.sub(r"\W+", "-", title.lower())[:40]
    nums = {b: i for i, b in enumerate(notes, 1)}

    # режем на файлы по заголовкам: читалки грузят книгу по частям
    parts, cur, titles = [], [], []
    for it in items:
        if it[0] == "title" and cur:
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

    files = {}
    for i, part in enumerate(parts, 1):
        o = []
        for kind, text, bid, links in part:
            if kind == "title":
                o.append(f"<h1>{_inline(text, HTML_INLINE)}</h1>")
            elif kind == "subtitle":
                o.append(f"<h2>{_inline(text, HTML_INLINE)}</h2>")
            elif kind == "break":
                o.append("<hr/>")
            elif kind == "image" and text in images:
                o.append(f'<img src="img/{escape(text)}" alt=""/>')
            elif kind == "table":
                o.append(_table_html(text, HTML_INLINE))
            elif kind == "verse":
                o.append(f'<p class="v">{_inline(text, HTML_INLINE)}</p>')
            elif kind == "code":
                o.append(f"<pre>{escape(text)}</pre>")
            elif kind == "p":
                mark = (f'<sup><a href="notes.xhtml#n{nums[bid]}">[{nums[bid]}]</a></sup>'
                        if bid in nums else "")
                o.append(f"<p>{_inline(text, HTML_INLINE, links)}{mark}</p>")
        files[f"ch{i:03d}.xhtml"] = xhtml("".join(o), titles[i - 1])

    if notes:
        o = ["<h1>" + escape(st.get("notes_title", "Примечания")) + "</h1><ol>"]
        for i, (bid, txt) in enumerate(notes.items(), 1):
            body = txt["text"] if isinstance(txt, dict) else txt
            src_only = isinstance(txt, dict) and txt.get("source_only")
            if not src_only and not body.startswith(note_prefix):
                body = note_prefix + body
            o.append(f'<li id="n{i}">{escape(body)}</li>')
        o.append("</ol>")
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

    man, spine = [], []
    if cover:
        man.append('<item id="cover-img" href="img/cover.jpg" '
                   'media-type="image/jpeg" properties="cover-image"/>')
    for i in range(1, len(parts) + 1):
        man.append(f'<item id="ch{i}" href="ch{i:03d}.xhtml" '
                   'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i}"/>')
    if notes:
        man.append('<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="notes"/>')
    for j, name in enumerate(images):
        man.append(f'<item id="img{j}" href="img/{escape(name)}" media-type="{_mime(name)}"/>')
    man.append('<item id="css" href="style.css" media-type="text/css"/>')
    man.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               'properties="nav"/>')

    seq = ""
    if meta.get("series"):
        seq = (f'<meta property="belongs-to-collection" id="s">{escape(meta["series"])}</meta>'
               '<meta refines="#s" property="collection-type">series</meta>')
        if meta.get("series_no"):
            seq += f'<meta refines="#s" property="group-position">{meta["series_no"]}</meta>'
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="uid"><metadata '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">{escape(uid)}</dc:identifier>'
           f'<dc:title>{escape(title)}</dc:title>'
           f'<dc:creator>{escape(author)}</dc:creator>'
           f'<dc:language>{code}</dc:language>'
           f'<dc:contributor>Booktrans</dc:contributor>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
           + seq + "</metadata><manifest>" + "".join(man) +
           '</manifest><spine>' + "".join(spine) + "</spine></package>")

    with zipfile.ZipFile(path, "w") as z:
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
        if cover:
            z.writestr("OEBPS/img/cover.jpg", cover)


WRITERS = {".txt": write_txt, ".html": write_html, ".htm": write_html,
           ".epub": write_epub}
