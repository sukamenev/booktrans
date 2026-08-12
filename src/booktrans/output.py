"""Запись готовой книги: fb2, epub, html, txt.

На вход всем — единый набор: метаданные, блоки с уже подставленным переводом,
сноски и картинки. Формат выбирается по расширению выходного файла.
"""
import base64
import os
import re
import zipfile
from xml.sax.saxutils import escape
from .tune import TEX_HEAD

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
    for kind, text, bid, links, *sp in items:
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
img.cover{max-height:80vh;width:auto;margin:0 auto 2em}
table{border-collapse:collapse;margin:1.2em 0}
td,th{border:1px solid #ccc;padding:.3em .6em;text-align:left;vertical-align:top}
hr{border:0;text-align:center;margin:2em 0}hr:after{content:'* * *';color:#999}
sup a{text-decoration:none;color:#06c;font-size:.75em}
.notes{margin-top:4em;border-top:1px solid #ddd;padding-top:1em;font-size:.9em;color:#444}
.notes li{margin-bottom:.6em}
@media(prefers-color-scheme:dark){body{background:#16161a;color:#ddd}
h2{color:#999}.notes{color:#bbb;border-color:#333}sup a{color:#7ab}}"""


def write_html(path, meta, items, notes, images, note_prefix, st=None, cover=None):
    st = st or {}
    targets = _targets(items)
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    code = meta.get("target_lang", "ru")
    o = ["<!doctype html>", f'<html lang="{code}"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{escape(title)}</title><style>{CSS}</style></head><body>"]
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
            o.append(f"<h1{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h1>")
        elif kind == "subtitle":
            o.append(f"<h2{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h2>")
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


def write_epub(path, meta, items, notes, images, note_prefix, st=None, cover=None):
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
                o.append(f"<h1{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h1>")
            elif kind == "subtitle":
                o.append(f"<h2{_at(bid, targets)}>{_inline(text, HTML_INLINE)}</h2>")
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

    # Обложка нередко лежит и среди картинок книги: тогда второй копии под
    # именем cover.jpg не нужно — на одной живой книге она весила 2,3 МБ из
    # 5,4. Хватит пометки на той, что уже есть.
    same = next((n for n, raw in images.items() if raw == cover), None) if cover else None
    # В книгу кладём только то, на что в ней ссылаются, плюс обложку. Реклама
    # и титульная картинка из исходника обычно висят на выброшенных блоках, а
    # весят как половина книги: на одной живой epub — 860 КБ из 3,2 МБ.
    used = {t for k, t, *_ in items if k == "image"}
    images = {n: r for n, r in images.items() if n in used or n == same}
    man, spine = [], []
    cmime, cext = _cover_mime(cover) if cover else ("", "")
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
        man.append(f'<item id="img{j}" href="img/{escape(name)}" '
                   f'media-type="{_mime(name)}"{cov}/>')
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
        if cover and not same:
            z.writestr(f"OEBPS/img/cover.{cext}", cover)



# ---------------------------------------------------------------- tex

# Знаки, которые TeX читает как разметку. Промах здесь опаснее прочих: `%`
# молча съедает остаток строки, и текст пропадает при собранном без ошибок
# файле.
TEX_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
           "&": r"\&", "#": r"\#", "^": r"\textasciicircum{}", "_": r"\_",
           "~": r"\textasciitilde{}", "%": r"\%"}
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


def _tex(s, links=None):
    """Текст в TeX: экранирование и разворот инлайновой разметки."""
    marks = {}
    # Ярлыки разметки прячем, чтобы экранирование их не тронуло.
    def hide(m):
        marks[len(marks)] = m.group()
        return f"\x00{len(marks) - 1}\x00"
    s = re.sub(r"</?(?:%s|a\d+)>" % "|".join(TEX_INLINE), hide, s)
    s = "".join(TEX_ESC.get(c, c) for c in s)

    def back(m):
        t = marks[int(m.group(1))]
        name = re.match(r"</?([a-z]+)", t).group(1)
        close = t.startswith("</")
        if name == "a":
            i = int(re.match(r"</?a(\d+)>", t).group(1))
            url = (links or [None] * i)[i - 1] if i <= len(links or []) else None
            if not url:
                return ""
            if close:
                return "}"
            # В адресе особые знаки экранируются иначе, чем в тексте: `#` —
            # это якорь, и hyperref без обратной косой ломается на нём.
            return r"\href{%s}{" % url.replace("%", r"\%").replace("#", r"\#")
        return "}" if close else "\\%s{" % TEX_INLINE[name]
    return re.sub(r"\x00(\d+)\x00", back, s)


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
    out = [r"\documentclass[10pt,oneside]{book}",
           r"% Собирается lualatex или xelatex: fontspec нужен ради письменностей,",
           r"% которых pdflatex не знает. Заголовки рубленым, текст с засечками,",
           r"% листинги моноширинным — как в книгах и заведено.",
           r"\usepackage{fontspec}",
           *fonts,
           r"\usepackage[a5paper,margin=18mm]{geometry}",
           r"\usepackage{graphicx}",
           r"\usepackage[normalem]{ulem}",
           r"\usepackage{multirow}",
           r"\usepackage{titlesec}",
           # Длинный адрес — вторая причина строк, вылезающих за поле:
           # разбить его без этого нечем.
           r"\PassOptionsToPackage{hyphens}{url}",
           r"\usepackage[hidelinks]{hyperref}",
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
            r"\titleformat{\chapter}{\huge\sffamily\bfseries}{}{0pt}{}",
            r"\titleformat{\section}{\Large\sffamily\bfseries}{}{0pt}{}",
            r"\titleformat{\subsection}{\large\sffamily}{}{0pt}{}",
            r"\renewcommand{\thesection}{\arabic{section}}",
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


def write_tex(path, meta, items, notes, images, note_prefix, st=None, cover=None):
    """Книга исходником LaTeX. Компиляция — дело читателя: шрифты и движок у
    всех свои, и обещать, что соберётся везде, нельзя."""
    st = st or {}
    code = meta.get("target_lang", "ru")
    title = meta.get("title_target") or meta.get("title") or st.get("untitled", "Книга")
    author = meta.get("author_target") or meta.get("author") or ""
    o = [_tex_preamble(meta, st, code)]
    if cover:
        name = "%s.img/cover.%s" % (os.path.splitext(os.path.basename(path))[0],
                                    _cover_mime(cover)[1])
        o.append(r"\begin{titlepage}\centering")
        o.append(r"\includegraphics[width=\textwidth,height=0.8\textheight,"
                 r"keepaspectratio]{%s}\end{titlepage}" % name)
    o.append(r"\title{%s}" % _tex(title))
    o.append(r"\author{%s}" % _tex(author))
    # Без этого LaTeX ставит на титул сегодняшнее число, и оно читается как
    # дата издания. Когда книга переведена, сказано в разделе «О переводе».
    o.append(r"\date{}")
    o.append(r"\maketitle")
    o.append(r"\tableofcontents")
    nums = {b: i for i, b in enumerate(notes, 1)}
    for kind, text, bid, links, *sp in items:
        if kind == "title":
            t = _tex(text)
            # Служебные разделы начинаются со своей страницы: «О переводе»
            # сразу за оглавлением читается его продолжением.
            if bid.startswith("_"):
                o.append(r"\clearpage")
            # Длинное название вылезает за поле: колонтитул в строку, и
            # переносить его нечем. Обрезаем сами — так надёжнее любого
            # пакета и не зависит от того, что стоит у читателя.
            head = " ".join(text.split())
            if len(head) > TEX_HEAD:
                head = head[:TEX_HEAD].rsplit(" ", 1)[0] + "…"
            o.append(r"\section{%s}\markright{%s}" % (t, _tex(head)))
        elif kind == "subtitle":
            o.append(r"\subsection*{%s}" % _tex(text))
        elif kind == "break":
            o.append(r"\begin{center}* * *\end{center}")
        elif kind == "image" and text in images and _tex_pic(text):
            o.append(r"\begin{center}\includegraphics[width=0.9\textwidth,"
                     r"keepaspectratio]{%s.img/%s}\end{center}"
                     % (os.path.splitext(os.path.basename(path))[0], text))
        elif kind == "verse":
            o.append(r"\begin{verse}%s\end{verse}"
                     % r"\\".join(_tex(l) for l in text.splitlines()))
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
                note = r"\footnote{%s}" % _tex(body)
            o.append(_tex(text, links) + note + "\n")
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
    out = [r"\begin{center}\begin{tabular}{" + "l" * n + "}"]
    for i, cells in enumerate(rows):
        line = []
        for j, c in enumerate(cells):
            body = _tex(c)
            sp = (spans or [])[i][j] if spans and i < len(spans) \
                and len(spans[i]) == len(cells) else [1, 1]
            if sp[1] > 1:
                body = r"\multirow{%d}{*}{%s}" % (sp[1], body)
            if sp[0] > 1:
                body = r"\multicolumn{%d}{l}{%s}" % (sp[0], body)
            line.append(body)
        # Строку добиваем пустыми ячейками: у TeX число столбцов объявлено
        # заранее, и короткая строка ломает всю таблицу.
        line += [""] * (n - width(i, cells))
        out.append(" & ".join(line) + r" \\")
    out.append(r"\end{tabular}\end{center}")
    return "\n".join(out)



TEX_ENGINES = ("lualatex", "xelatex")


def write_pdf(path, meta, items, notes, images, note_prefix, st=None,
              cover=None, tmp=None):
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
    write_tex(tex, meta, items, notes, images, note_prefix, st, cover)
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


WRITERS = {".tex": write_tex, ".pdf": write_pdf, ".txt": write_txt, ".html": write_html, ".htm": write_html,
           ".epub": write_epub}
