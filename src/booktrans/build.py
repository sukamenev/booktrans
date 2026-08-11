"""Сборка fb2 и проверки."""
import base64
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from xml.sax.saxutils import escape

from . import lang, output
from .pipeline import all_notes, all_translations, strip

CAPTION = 160       # длиннее строка под снимком — уже не подпись
# Оговорка у цитаты, приведённой по чужому переводу. Умолчание на случай,
# если в правилах языка строки нет: молчать тут нельзя — читатель примет
# цитату за сверенную.
SOURCE_CAVEAT = ("Авторство перевода указано машиной по памяти и с изданием не сверено.")
# Вводная строка к списку цитат в «Деталях перевода».
DETAILS_SOURCES = ("Цитаты приведены по опубликованным переводам. Авторство указано машиной по памяти и с изданиями не сверено — проверьте, если это "
                   "важно:")


def _date(ts, code="ru"):
    return lang.fmt_date(ts, code)


def _work_span(work, code="ru"):
    """Когда книгу переводили — по времени файлов кусков, а не по моменту
    сборки: пересобрать fb2 можно и через год."""
    times = []
    for sub in ("tr", "ed", "nt"):
        d = os.path.join(work, sub)
        if os.path.isdir(d):
            times += [os.path.getmtime(os.path.join(d, n))
                      for n in os.listdir(d) if n.endswith(".json")]
    if not times:
        return ""
    a, b = _date(min(times), code), _date(max(times), code)
    return a if a == b else f"{a} — {b}"


def _models(work):
    out = set()
    for sub in ("tr", "ed", "nt"):
        d = os.path.join(work, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                m = json.load(open(os.path.join(d, n), encoding="utf-8")).get("model")
                if m:
                    out.add(m)
    return sorted(out)

def version(code="ru"):
    """Чем именно переведена книга — для раздела «О переводе».

    Читатель, нашедший в переводе ошибку, должен понимать, к какой именно
    сборке она относится: конвейер меняется, и «переведено BookTrans» через
    год не значит ничего.

    Установлен из индекса — номер версии. Взят из репозитория — дата
    последнего изменения: номер там всё равно стоит от прошлого выпуска и
    соврал бы. Нет ни того ни другого — дата самого файла, который перевод
    и делал.

    Возвращает пару: выпуск ли это (тогда номер) и что именно писать.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(here)), ".git")):
        try:
            from importlib.metadata import version as _v
            return True, _v("booktrans")
        except Exception:                             # noqa: BLE001
            pass
    try:
        import subprocess
        r = subprocess.run(["git", "-C", here, "log", "-1", "--format=%ct"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return False, lang.fmt_date(float(r.stdout.strip()), code)
    except Exception:                                 # noqa: BLE001
        pass
    return False, lang.fmt_date(os.path.getmtime(os.path.join(here, "cli.py")), code)


def release_version():
    """Номер выпуска — из индекса, а нет его, так из pyproject рабочей копии."""
    try:
        from importlib.metadata import version as _v
        return _v("booktrans")
    except Exception:                                 # noqa: BLE001
        pass
    p = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "pyproject.toml")
    try:
        m = re.search(r'^version\s*=\s*"([^"]+)"', open(p, encoding="utf-8").read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def banner(code="ru"):
    """Строка, какой конвейер представляется при запуске.

    Номер выпуска и, если работаем из репозитория, дата сборки: по ней видно,
    что это не выпущенная версия, а рабочая копия.
    """
    out = f"{PIPELINE} {release_version()}"
    release, ver = version(code)
    if not release:
        out += f" ({ver})"
    return out + " — " + lang.T("tagline")


def _ranges(nums):
    """1,2,3,7 → «1–3, 7»."""
    out, nums = [], sorted(nums)
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if j == i else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)


def _chapter_of(blocks):
    """Идентификатор блока → номер главы. Главой считаем заголовок верхнего
    уровня; всё до первого заголовка относим к нулевой, служебной."""
    out, n = {}, 0
    for b in blocks:
        if b["kind"] == "title":
            n += 1
        out[b["id"]] = n
    return out


def _pass_models(work, sub, key, chapter):
    """Какие главы какой моделью пройдены: {модель: {глава: сколько блоков}}."""
    out, total = {}, {}
    d = os.path.join(work, sub)
    if not os.path.isdir(d):
        return out, total
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        x = json.load(open(os.path.join(d, n), encoding="utf-8"))
        model = x.get("model") or "?"
        ids = x.get(key) or []
        if isinstance(ids, dict):
            ids = list(ids)
        for i in ids:
            ch = chapter.get(i)
            if ch:
                out.setdefault(model, {}).setdefault(ch, 0)
                out[model][ch] += 1
                total[ch] = total.get(ch, 0) + 1
    return out, total


def _pass_line(work, sub, key, chapter, st, label):
    """Строка вида «Перевод: opus — главы 1–15; gemini — глава 16 (частично)»."""
    by, total = _pass_models(work, sub, key, chapter)
    if not by:
        return ""
    if len(by) == 1:
        return label.format(models=f"{next(iter(by))} ({st['details_all']})")
    parts = []
    for model, chs in sorted(by.items()):
        whole = [c for c, k in chs.items() if k == total[c]]
        part = [c for c in chs if c not in whole]
        bits = []
        if whole:
            bits.append(st["details_chapters"].format(chapters=_ranges(whole)))
        if part:
            bits.append(st["details_partly"].format(chapters=_ranges(part)))
        parts.append(f"{model} — {', '.join(bits)}")
    return label.format(models="; ".join(parts))


def details_lines(work, st, blocks):
    """Раздел «Детали перевода» в конце книги.

    Читателю эти сведения в начале книги не нужны, а тому, кто найдёт в
    переводе огрех, нужны обязательно: по ним видно, какая модель делала
    именно это место.
    """
    chapter = _chapter_of(blocks)
    body = []
    p = os.path.join(work, "source.json")
    if os.path.exists(p):
        src = json.load(open(p, encoding="utf-8"))
        if src.get("reader"):
            body.append(st["details_reader"].format(reader=src["reader"]))
        if src.get("formatter"):
            body.append(st["details_format"].format(models=src["formatter"]))
        if src.get("fixer"):
            body.append(st["details_fix"].format(models=src["fixer"]))
    p = os.path.join(work, "scout.json")
    if os.path.exists(p):
        m = json.load(open(p, encoding="utf-8")).get("model")
        if m:
            body.append(st["details_scout"].format(models=m))
    for sub, key, name in (("tr", "tr", "details_translate"),
                           ("ed", "blocks", "details_edit")):
        line = _pass_line(work, sub, key, chapter, st, st[name])
        if line:
            body.append(line)
    if not body:
        return "", []
    body.append(st["details_caveat"])
    # Цитаты по чужим переводам — списком, чтобы взявшийся сверять видел
    # объём работы, а не искал сноски по всей книге. У самой цитаты стоит
    # своя оговорка; здесь она не повторяется.
    src = _by_work(_source_items(work))
    if src:
        body.append(st.get("details_sources", DETAILS_SOURCES))
        for work_name, group in src:
            body.append("— " + _cut(group[0]["text"], 200))
    return st["details_title"], body


def about_lines(work, st, code):
    """Служебный раздел «О переводе»: заголовок и абзацы.

    Собирается в одном месте, потому что нужен всем форматам: читатель
    epub или txt имеет такое же право знать, чем и когда переведена книга,
    как читатель fb2.
    """
    span = _work_span(work, code)
    release, ver = version(code)
    if not release:
        # «(5 августа 2026)» читается как дата перевода, а это дата сборки
        # конвейера — слово нужно, и оно на языке перевода.
        ver = st.get("about_version", "{date}").format(date=ver)
        who = f"{PIPELINE} ({ver}, {PIPELINE_URL})"
    else:
        who = f"{PIPELINE} {ver} ({PIPELINE_URL})"
    body = [st["about_made"].format(pipeline=who)]
    if span:
        body.append(st["about_date"].format(date=span))
    body += [st["about_quality"], st["about_caveat"],
             st.get("about_notes", ""), st.get("about_disclaimer", "")]
    body = [x for x in body if x]
    return st["about_title"], body


def out_name(meta, fallback):
    """Имя выходного файла: «Фамилия Имя. Заглавие».

    Так книги называют в библиотеках, и в файловом списке они выстраиваются
    по авторам сами собой. Фамилией считается всё, кроме первого слова: для
    «Габриэль Гарсиа Маркес» выйдет «Гарсиа Маркес Габриэль», как и принято.

    Заглавия нет вовсе — берём имя исходного файла и приписываем код языка.
    Без этого перевод fb2 в fb2 затёр бы сам исходник: имя-то одно и то же.
    """
    title = meta.get("title_target") or meta.get("title")
    if not title:
        code = meta.get("target_lang") or "tr"
        return f"{fallback}_{code}"
    au = (meta.get("author_target") or meta.get("author") or "").split()
    if len(au) > 1:
        who = " ".join(au[1:]) + " " + au[0]
    else:
        who = au[0] if au else ""
    name = f"{who}. {title}" if who else title
    # знаки, недопустимые в именах файлов, и лишние пробелы
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "-", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:180]


HEAD_KINDS = ("title", "subtitle")

# FB2 не знает <i> и <b> — в стандарте это <emphasis> и <strong>.
# Модель работает с привычной ей html-нотацией, а сборщик переводит её
# в разметку fb2.
FB2_INLINE = {
    "i": "emphasis", "em": "emphasis",
    "b": "strong", "strong": "strong",
    "s": "strikethrough", "del": "strikethrough", "strike": "strikethrough",
    "sub": "sub", "sup": "sup", "code": "code",
}

NOTE_PREFIX = "Прим. переводчика: "
PIPELINE = "BookTrans"
PIPELINE_URL = "https://github.com/sukamenev/booktrans"


def esc(s, links=None, notes_map=None):
    """Экранирует текст, разворачивая обратно инлайновую разметку.

    Номерные ярлыки <a1>..</a1> заменяются на настоящие ссылки: URL хранится
    в блоке и через модель не проходил, поэтому доезжает побайтово.
    """
    s = escape(s)
    for src, dst in FB2_INLINE.items():
        s = s.replace(f"&lt;{src}&gt;", f"<{dst}>").replace(f"&lt;/{src}&gt;", f"</{dst}>")
    if links:
        for i, url in enumerate(links, 1):
            if "#" in url and not url.startswith("http"):
                # Ссылка на авторскую сноску: адрес внутри книги, и на выходе
                # он должен указывать на нашу собственную нумерацию. Ссылка
                # бывает и в соседний файл — берём только якорь.
                tgt = (notes_map or {}).get(url.split("#", 1)[1])
                if tgt:
                    anchor, num = tgt
                    # Видимый номер тоже наш: в оригинале сноска могла быть
                    # 332-й, а у нас она шестая, и читатель, нажав «332»,
                    # попадал бы не туда.
                    s = re.sub(rf"&lt;a{i}&gt;.*?&lt;/a{i}&gt;",
                               f'<a l:href="#{anchor}" type="note">[{num}]</a>',
                               s, flags=re.S)
                    continue
                if not url.startswith("#"):
                    continue
                # Перекрёстная ссылка внутри книги: цель — блок, и его
                # идентификатор проставлен при сборке.
                s = s.replace(f"&lt;a{i}&gt;", f'<a l:href="{escape(url)}">')
                s = s.replace(f"&lt;/a{i}&gt;", "</a>")
                continue
            s = s.replace(f"&lt;a{i}&gt;", f'<a l:href="{escape(url)}">')
            s = s.replace(f"&lt;/a{i}&gt;", "</a>")
    s = re.sub(r"&lt;/?a\d+&gt;", "", s)      # ярлык без адреса — снять
    return s


def link_targets(blocks):
    """Блоки, на которые ссылаются изнутри книги: им нужен `id` в выходном
    файле, иначе ссылка ведёт в пустоту."""
    return {u[1:] for b in blocks for u in b.get("links", ())
            if u.startswith("#")}


def build_fb2(work, meta, blocks, cover, dest, log, partial=False, images=None):
    targets = link_targets(blocks)

    def aid(b):
        """Атрибут `id`, если на блок ссылаются изнутри книги."""
        return f' id="{b["id"]}"' if b["id"] in targets else ""

    tr, edited = all_translations(work)
    if edited:
        log("  " + lang.T("applied_edits", edited))

    # заголовки — из таблицы, чтобы одинаковые совпадали дословно
    hp = f"{work}/headings.json"
    if os.path.exists(hp):
        heads = {k: v for k, v in json.load(open(hp, encoding="utf-8")).items()
                 if not k.startswith("_")}
        # Ключ ищем и по голому тексту тоже: в заголовке бывает ярлык ссылки
        # на сноску, и от того, разбирали книгу до или после его появления,
        # таблица заголовков не должна ломаться.
        key = lambda t: re.sub(r"\s+", "", strip(t))
        bare = {key(k): v for k, v in heads.items()}
        for b in blocks:
            if b["kind"] not in HEAD_KINDS:
                continue
            v = heads.get(b["text"]) or bare.get(key(b["text"]))
            if v is not None:
                tr[b["id"]] = v

    # сквозные правки — точными строками: regex ломает согласование
    fp = f"{work}/fixups.json"
    if os.path.exists(fp):
        n = 0
        for rule in json.load(open(fp, encoding="utf-8")).get("rules", []):
            # правило может быть привязано к одному блоку: слово бывает верным
            # в одном месте и неверным в другом, глобальная замена тут вредна
            scope = rule.get("blocks")
            for a, b in rule.get("pairs", {}).items():
                keys = scope if scope else list(tr)
                c = sum(tr[k].count(a) for k in keys if k in tr)
                if c:
                    for k in keys:
                        if k in tr:
                            tr[k] = tr[k].replace(a, b)
                    n += c
        if n:
            log("  " + lang.T("sweep_fixes", n))

    # Список литературы и листинги в книгу идут как есть: они не переводились
    # и непереведёнными не считаются. В листинге переведены комментарии — их
    # ставит на место code.swap, кода не касаясь.
    cp = f"{work}/code.json"
    listings = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
    for b in blocks:
        if b.get("asis"):
            tr.setdefault(b["id"], listings.get(b["id"], b["text"]))
    missing = [b["id"] for b in blocks
               if b["kind"] not in ("break", "image") and b["id"] not in tr]
    if missing and not partial:
        raise SystemExit(f"не переведено {len(missing)} блоков, например {missing[:6]}")
    src = {b["id"]: b["text"] for b in blocks}
    for i in missing:
        tr[i] = src[i]
    if missing:
        log("  " + lang.T("preview_left", len(missing)))

    # Строки для читателя — на языке перевода, и нужны они обоим путям: и
    # сборщику fb2 ниже, и писателям остальных форматов. Берутся до сносок:
    # оговорка у цитаты — тоже строка для читателя.
    code = meta.get("target_lang", "ru")
    st = lang.book_strings(code)

    order = {b["id"]: i for i, b in enumerate(blocks)}
    notes = all_notes(work, order)
    notes = {k: v for k, v in notes.items() if k in tr}
    # Оговорка про цитату — здесь, до развилки по форматам: она нужна читателю
    # любой книги, а не только fb2.
    say = st.get("source_caveat", SOURCE_CAVEAT)
    for v in notes.values():
        if v.get("source") and say and say not in v["text"]:
            v["text"] = v["text"].rstrip() + " " + say

    # Сноски бывают двух родов: авторские, пришедшие из самой книги, и
    # предложенные конвейером. Нумерация у них общая и идёт по порядку
    # текста — читателю всё равно, кто какую поставил.
    note_seq = []            # (якорь, номер, текст, из_книги_ли)
    nid = {}                 # блок -> якорь (сноска конвейера)
    notes_map = {}           # якорь из epub -> наш якорь (авторская)
    for b in blocks:
        if b["kind"] == "note":
            a = f"n{len(note_seq) + 1}"
            notes_map[b.get("note_id") or b["id"]] = (a, len(note_seq) + 1)
            note_seq.append((a, len(note_seq) + 1, tr.get(b["id"], b["text"]), True))
        elif b["id"] in notes:
            a = f"n{len(note_seq) + 1}"
            nid[b["id"]] = a
            v = notes[b["id"]]
            note_seq.append((a, len(note_seq) + 1,
                             v["text"] if isinstance(v, dict) else v,
                             bool(isinstance(v, dict) and v.get("source_only"))))
    if notes:
        log("  " + lang.T("notes_n", len(notes)))

    # формат по расширению: fb2 собирается ниже, остальные — в output.py
    ext = os.path.splitext(dest)[1].lower()
    if ext in output.WRITERS:
        head, body = about_lines(work, st, code)
        items = [("title", head, "_about", None)]
        items += [("p", t, f"_about{i}", None) for i, t in enumerate(body)]
        # У картинки в тексте лежит имя файла, а не проза, и перевода у неё
        # нет. Возьми мы `tr`, вышла бы пустая строка — картинки паковались в
        # книгу, но в тексте на них не оставалось ни одной ссылки.
        items += [(b["kind"],
                   b["text"] if b["kind"] == "image" else tr.get(b["id"], ""),
                   b["id"], b.get("links"), b.get("spans")) for b in blocks]
        dhead, dbody = details_lines(work, st, blocks)
        if dhead:
            items += [("title", dhead, "_details", None)]
            items += [("p", t, f"_details{i}", None) for i, t in enumerate(dbody)]
        kw = {"cover": cover} if ext in (".epub", ".html", ".htm", ".pdf") else {}
        if ext == ".pdf":
            kw["tmp"] = work        # черновики LaTeX — в рабочую папку книги
        output.WRITERS[ext](dest, meta, items, notes, images or {},
                            st.get("note_prefix", NOTE_PREFIX).rstrip() + " ",
                            st, **kw)
        log(lang.T("built_file", dest, f"{os.path.getsize(dest) / 1024 / 1024:.1f}",
                   sum(1 for b in blocks if b["kind"] == "p")))
        return

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
    head, body = about_lines(work, st, code)
    w("<section>")
    w(f"<title><p>{esc(head)}</p></title>")
    for i, line in enumerate(body):
        line = esc(line)
        if i == 0:
            line = line.replace(PIPELINE, f"<strong>{PIPELINE}</strong>", 1)
        w(f"<p>{line}</p>")
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
                    f"<td{output.span_attr(b.get('spans'), i, j, len(cells))}>"
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
    dhead, dbody = details_lines(work, st, blocks)
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
        pref = st.get("note_prefix", NOTE_PREFIX).rstrip() + " "
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
    log(lang.T("built_file", dest, f"{os.path.getsize(dest) / 1024 / 1024:.1f}",
               sum(1 for b in blocks if b["kind"] == "p")))


# Разряды тысяч разделяют по-разному: «7,386» по-английски, «7386» или
# «7 386» по-русски, «7.386» по-немецки. Перед сверкой группы склеиваем,
# иначе честный перевод выглядит потерей двух чисел из трёх.
SEPS = ",.\u00a0\u202f\u2009 '\u2019"      # запятая, точка, пробелы, апостроф
GROUPED = re.compile(rf"\b\d{{1,3}}(?:[{re.escape(SEPS)}]\d{{3}})+(?!\d)")
GSEP = re.compile(rf"[{re.escape(SEPS)}]")


def _nums(t, group=True):
    # Теги заменяем пробелом, а не пустотой: «0,4.<sup>116</sup>» иначе даёт
    # «0,4.116», и разделитель разрядов склеивает число с номером сноски в
    # несуществующее 4116 — проверка ругалась на здоровый перевод.
    t = strip(t, " ")
    if group:
        t = GROUPED.sub(lambda m: GSEP.sub("", m.group()), t)
    return Counter(re.findall(r"\d+", t))


def _compound(t, n):
    """Число, слипшееся дефисом со словом: «88-страничный», «2-D skin cells».

    Это составное определение, и языки пишут его по-разному: английский
    разворачивает число в слова (eighty-eight-page), русский оставляет
    цифрой, — так что цифра честно пропадает в одну сторону и появляется в
    другую. Проверка считала это ошибкой, и на хорошо переведённой книге
    раздел краснел на пустом месте.

    Число только впереди: «COVID-19» и «MP-3» — обозначения, там цифра
    обязана уцелеть.
    """
    return bool(re.search(rf"(?<![\w-]){re.escape(n)}-[^\W\d_]", t))


# «part 1», «chapter 2» — отсылка внутрь самой книги, и по-русски она пишется
# словом: «в первой части». Десятилетие тоже: «the 1980s and ’90s» → «в
# восьмидесятых и девяностых». Число тут не сведение, а часть речи, и терять
# его не страшно; а вот тонуть настоящей потере среди сорока таких строк —
# страшно: раздел перестают читать.
SPELLED = (r"(?i)\b(?:part|chapter|book|volume|section|step|rule|principle)\s+{n}\b",
           r"\b(?:19|20)\d0s\b", r"[’']{n}0?s\b")


def _spelled(s, n):
    return any(re.search(p.replace("{n}", re.escape(n)), s) for p in SPELLED)


# Мера, которую перевод обязан пересчитать, и мера, в которую он пересчитывает.
# Пересчёт меняет само число: «165 pounds» → «75 кг». Для проверки это выглядит
# сразу двумя бедами — цифра пропала и цифра появилась, — и оба раза напрасно.
IMPERIAL = (r"(?:pounds?|lbs?|ounces?|oz|inch(?:es)?|feet|foot|ft|yards?|miles?|"
            r"gallons?|pints?|quarts?|acres?|fahrenheit|°\s?F)\b")
METRIC = (r"(?:кг|килограмм|г|грамм|см|сантиметр|мм|миллиметр|м|метр|км|километр|"
          r"л|литр|мл|гектар|градус|°\s?C|цельси|kg|cm|mm|km|ml|[gml])\w*\b")
# «98 degrees Fahrenheit», «сорок градусов Цельсия»: между числом и самой мерой
# стоит слово, и без него правило видит мерой «degrees».
GAP = r"\s*(?:degrees?\s+|градусов?\s+|по\s+)?"
HAS_METRIC = re.compile(rf"\d{GAP}{METRIC}", re.I)
HAS_IMPERIAL = re.compile(rf"\d{GAP}{IMPERIAL}|[a-z]\s+{IMPERIAL}", re.I)


def _power(t, n):
    """Число собралось обратно в степень: «1031» → «10<sup>31</sup>».

    Распознавание роняет верхний индекс, и в оригинале от 10³¹ остаётся
    «1031». Перевод возвращает степень на место — и проверка, снимающая теги
    пробелом, видит «10 31» и объявляет число пропавшим. Починку в ошибки
    записывать нельзя: цифры все на месте, изменилась только разметка.
    """
    return "<sup>" in t and n in Counter(re.findall(r"\d+", strip(t, "")))


def _measure(s, t, n, back=False):
    """Число изменилось, потому что меру пересчитали в систему СИ.

    Проверять надо обе стороны: без пересчёта в переводе это была бы честная
    потеря числа, а без имперской меры в оригинале — честная прибавка.
    Число, стоящее перед самой мерой, ищем точно; остальные числа блока
    правило не покрывает, и потеря среди них останется видна.
    """
    if back:       # число появилось в переводе: перед единицей СИ
        return bool(re.search(rf"(?<!\d){re.escape(n)}{GAP}{METRIC}", t, re.I)
                    and HAS_IMPERIAL.search(s))
    return bool(re.search(rf"(?<!\d){re.escape(n)}{GAP}{IMPERIAL}", s, re.I)
                and HAS_METRIC.search(t))


def _ocr_digit(s, n):
    """Пропавшая цифра — это порча распознавания, а не потеря в переводе.

    В распознанном тексте цифра стоит на месте буквы: «1 have attended» — это
    I, «World War 11» — II, «5t. Paul» — St. Переводчик читает их верно и в
    перевод цифру не несёт, а проверка считала это ошибкой. На живой книге из
    девяноста таких сообщений верным не оказалось ни одного, и раздел, всегда
    красный, перестают читать вовсе.

    Спрашиваем только у распознанных книг: в набранной цифра стоит там, где
    её поставил автор, и пропасть ей не с чего.
    """
    if re.search(rf"[^\W\d_]{re.escape(n)}|{re.escape(n)}[^\W\d_]", s):
        return True          # приклеена к букве: 5t. Paul, fun7ishing, l3
    return len(n) <= 2       # одиночная на месте буквы: 1, 11, 5


def _more(log, n, T):
    """Сколько осталось за кадром. Молчаливый обрыв списка читается как
    «это всё», и человек проверяет восемь строк вместо двадцати двух."""
    if n > 0:
        log("     " + T("qa_more", n))


def _cut(s, n):
    """Обрезка по границе слова: обрывок посреди слова читать нечем."""
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" ,;:.—-") + "…"


def review_report(work, log, T=None):
    T = T or lang.T
    """Места, которые просит посмотреть глазами.

    Редактор пишет такие замечания в каждом куске, но раньше они оседали в
    файлах и до человека не доходили. А это единственное, что машина сама
    проверить не может: сомнительный термин, двусмысленное место, оборот,
    в котором она не уверена.
    """
    items = []
    d = os.path.join(work, "ed")
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            x = json.load(open(os.path.join(d, n), encoding="utf-8"))
            t = (x.get("notes") or "").strip()
            # «замечаний нет» редактор пишет на языке перевода, и списком
            # отрицаний это не покрыть: отсекаем по длине.
            if t and len(t) > 12 and t.lower().rstrip(".") not in (
                    "нет", "none", "keine", "aucune", "无", "なし", "-", "—"):
                items.append((x.get("index"), t))
    p = os.path.join(work, "review.md")
    if not items:
        # Замечания помечены номером куска. Убрали редактуру — файл обязан
        # уйти с ней: от прежней нарезки он остался бы лежать как свежий и
        # показывал бы совсем на другие места книги.
        if os.path.exists(p):
            os.unlink(p)
        return
    log("")
    log(T("review_head", len(items)))
    # В консоли — первая мысль замечания, целиком — в файле рядом с кусками.
    # Замечание бывает на полстраницы, и вываливать сорок таких в поток значит
    # смыть остальной отчёт.
    with open(p, "w", encoding="utf-8") as f:
        for idx, t in items:
            f.write(f"## {idx:04d}\n\n{t}\n\n")
    for idx, t in items:
        log("  " + T("review_item", f"{idx:04d}", _cut(t, 300)))
    log("  " + T("review_file", p))


def unfinished_edits(work, log, T=None):
    """Куски, где правка оборвалась и кусок остался наполовину нетронутым.

    Файл редактуры при этом записан, и возобновление сочтёт кусок готовым —
    сам он никогда не переделается. Поэтому список нужен в конце каждого
    прогона, а не только в тот раз, когда обрыв случился.
    """
    T = T or lang.T
    items = []
    d = os.path.join(work, "ed")
    if not os.path.isdir(d):
        return
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        x = json.load(open(os.path.join(d, n), encoding="utf-8"))
        if x.get("stopped_at"):
            items.append((x.get("index"), x["stopped_at"], len(x.get("blocks") or [])))
    if not items:
        return
    log("")
    log(T("edits_unfinished", len(items)))
    for idx, at, total in items:
        log("  " + T("edits_unfinished_item", f"{idx:04d}", at, total))
    log("  " + T("edits_unfinished_hint",
                 ",".join(str(i) for i, _, _ in items)))


# Название произведения в пояснении редактора: первое, что взято в кавычки
# или в звёздочки. Пишет он свободным текстом — «У. Джеймс, „Принципы
# психологии“, глава IX, перевод И. И. Лапшина», — и название единственное,
# что в этих пояснениях стоит одинаково.
WORK = re.compile(r"[«\"“„]([^«»\"“”„]{3,80})[»\"”“]|\*([^*\n]{3,80})\*")


def _source_items(work):
    """Цитаты, приведённые по чужому переводу: по одной на понятие."""
    out, seen = [], set()
    for sub, key in (("tr", "footnotes"), ("ed", "footnotes"), ("nt", "notes")):
        d = os.path.join(work, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            for it in json.load(open(os.path.join(d, n), encoding="utf-8")).get(key) or []:
                if it.get("kind") == "source" and it["term"].lower() not in seen:
                    seen.add(it["term"].lower())
                    out.append(it)
    return out


def _by_work(items):
    """Цитаты, сгруппированные по названию произведения.

    Порядок сохраняем: первое появление задаёт место группы в списке. Без
    названия — своя группа на каждую, такие ни с чем не сверяются.
    """
    out, at = [], {}
    for it in items:
        m = WORK.search(it["text"])
        key = (m.group(1) or m.group(2)).strip(" .,;:") if m else None
        if key and key.lower() in at:
            out[at[key.lower()]][1].append(it)
            continue
        if key:
            at[key.lower()] = len(out)
        out.append((key or it["term"], [it]))
    return out


def sources_report(work, log, T=None):
    T = T or lang.T
    """Все цитаты, приведённые по чужому переводу, — списком.

    Поиск источника агенту недоступен, и проверить, действительно ли текст
    взят из издания, машина не может. Значит, единственная надёжная проверка —
    человеческая, и список нужен, чтобы она заняла минуты, а не чтение всей
    книги.
    """
    items = []
    for sub, key in (("tr", "footnotes"), ("ed", "footnotes"), ("nt", "notes")):
        d = os.path.join(work, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                for it in json.load(open(os.path.join(d, n), encoding="utf-8")).get(key) or []:
                    if it.get("kind") == "source":
                        items.append(it)
    if not items:
        return
    log("")
    log(T("sources_head", len(items)))
    seen, uniq = set(), []
    for it in items:
        k = it["term"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(it)

    def one(it, pad=""):
        flag = "  !! по памяти" if "памят" in it["text"].lower() else ""
        log(f"{pad}  [{it['block']}] {it['term']}{flag}")
        log(f"{pad}      {_cut(it['text'], 150)}")

    for work, group in _by_work(uniq):
        # Одну и ту же книгу редактор приписывает разным переводчикам, а одну
        # и ту же фразу передаёт в двух кусках по-разному — куски правятся
        # порознь и друг друга не видят. Существует ли перевод, машина
        # проверить не может, но противоречие внутри книги видно и без
        # вывода: достаточно поставить такие места рядом.
        if len(group) < 2:
            one(group[0])
            continue
        log("  " + T("sources_same", work, len(group)))
        for it in group:
            one(it, "  ")
    log("  " + T("sources_hint"))


def usage_report(work, log, T=None):
    T = T or lang.T
    """Что израсходовано: по моделям и по проходам.

    Считается по файлам кусков, а не по счётчику в памяти: пересборка через
    неделю покажет то же самое, и прерванный прогон не потеряет учёт.
    """
    rows = {}
    for sub, name in (("tr", T("pass_tr")), ("ed", T("pass_ed")),
                      ("nt", T("pass_nt"))):
        d = os.path.join(work, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            x = json.load(open(os.path.join(d, n), encoding="utf-8"))
            k = (name, x.get("model") or "?")
            r = rows.setdefault(k, {"n": 0, "usd": 0.0})
            r["n"] += 1
            r["usd"] += x.get("cost_usd") or 0
    if not rows:
        return
    log("")
    log(T("usage"))
    log(f"  {T('usage_pass'):12s} {T('usage_model'):22s} "
        f"{T('usage_reqs'):>9s} {'$':>8s}")
    tot_n = tot_u = 0
    for (name, model), r in sorted(rows.items()):
        log(f"  {name:12s} {model:22s} {r['n']:9d} {r['usd']:8.2f}")
        tot_n += r["n"]
        tot_u += r["usd"]
    log(f"  {T('usage_sum'):12s} {'':22s} {tot_n:9d} {tot_u:8.2f}")
    log("  " + T("usage_note_scout"))
    log("  " + T("usage_note_sub"))


def _script_of_text(texts):
    """Какой письменностью написан оригинал, если язык книги не указан.

    В pdf его нет почти никогда, и проверка на непереведённые куски молча
    пропускалась: «пропущено — оригинал и перевод пишутся одной
    письменностью (?)». Считать буквы дешевле и вернее, чем гадать.
    """
    best, most = None, 0
    joined = " ".join(list(texts)[:400])
    for name, rng in lang.SCRIPTS.items():
        n = len(re.findall(f"[{rng}]", joined))
        if n > most:
            best, most = name, n
    return best if most >= 200 else None


def qa(work, blocks, log, T=None, src_lang=None, to="ru", ocr=False):
    T = T or lang.T
    src = {b["id"]: b["text"] for b in blocks
           if b["kind"] not in ("break", "image") + HEAD_KINDS
           and not b.get("asis")}
    tr, edited = all_translations(work)
    problems = 0

    log(T("qa1"))
    missing = [i for i in src if i not in tr]
    if missing:
        problems += len(missing)
        log("   " + T("qa1_bad", len(missing), len(src), missing[:6]))
    else:
        log("   " + T("qa1_ok", len(src)) + (T("qa1_edited", edited) if edited else ""))

    log(T("qa2"))
    lost, junk, word, gained = [], set(), set(), []
    spelled, si, sup = set(), set(), set()
    for i, s in src.items():
        if i not in tr:
            continue
        # Тот же знак бывает и разделителем разрядов, и десятичной запятой:
        # «.003 per cent» → «0,003 процента». Какое прочтение верное, из текста
        # не видно, поэтому число считаем уцелевшим, если оно нашлось хоть при
        # одном из двух.
        a, b = _nums(s), _nums(tr[i]) | _nums(tr[i], group=False)
        bad = sorted(a - b)
        if ocr:
            junk |= {i for x in bad if _ocr_digit(s, x)}
            bad = [x for x in bad if not _ocr_digit(s, x)]
        word |= {i for x in bad if _compound(s, x)}
        bad = [x for x in bad if not _compound(s, x)]
        spelled |= {i for x in bad if _spelled(s, x)}
        bad = [x for x in bad if not _spelled(s, x)]
        si |= {i for x in bad if _measure(s, tr[i], x)}
        bad = [x for x in bad if not _measure(s, tr[i], x)]
        sup |= {i for x in bad if _power(tr[i], x)}
        bad = [x for x in bad if not _power(tr[i], x)]
        if bad:
            lost.append((i, bad))
        # Обратная сторона: цифра, которой в оригинале не было. Сверить её не
        # с чем — ни оригинала, ни источника у машины нет, — а появиться она
        # может и от правки распознанной даты, и от пересчёта в другие
        # единицы. Поэтому не ошибка, а строка «посмотрите глазами».
        new = sorted(_nums(tr[i]) - (a | _nums(s, group=False)))
        word |= {i for x in new if _compound(tr[i], x)}
        new = [x for x in new if not _compound(tr[i], x)]
        si |= {i for x in new if _measure(s, tr[i], x, back=True)}
        new = [x for x in new if not _measure(s, tr[i], x, back=True)]
        if new:
            gained.append((i, new))
    if lost:
        problems += len(lost)
        log("   " + T("qa2_bad", len(lost)))
        for i, x in lost[:8]:
            log(f"     {i}  {x}")
        _more(log, len(lost) - 8, T)
    elif not (junk or word):
        log("   " + T("qa2_ok"))
    if junk:
        log("   " + T("qa2_ocr", len(junk)))
    if word:
        log("   " + T("qa2_word", len(word)))
    if spelled:
        log("   " + T("qa2_spelled", len(spelled)))
    if si:
        log("   " + T("qa2_si", len(si)))
    if sup:
        log("   " + T("qa2_sup", len(sup)))
    if gained:
        log("   " + T("qa2_new", len(gained)))
        for i, x in gained[:5]:
            log(f"     {i}  {x}")
        _more(log, len(gained) - 5, T)

    log(T("qa3"))
    # Отклонение считаем от собственной середины книги, а не от готовых
    # границ: русский текст длиннее английского примерно на десятую часть,
    # а китайский короче русского втрое, и одни и те же пороги пометили бы
    # всю книгу целиком.
    ratios = {i: len(strip(tr[i])) / len(strip(s))
              for i, s in src.items() if i in tr and len(strip(s)) >= 40}
    odd = []
    if ratios:
        mid = sorted(ratios.values())[len(ratios) // 2] or 1.0
        for i, r in ratios.items():
            if r / mid < 0.55 or r / mid > 2.2:
                odd.append((round(r, 2), i))
        log("   " + T("qa3_mid", f"{mid:.2f}"))
    if odd:
        log("   " + T("qa3_bad", len(odd)))
        for r, i in sorted(odd)[:8]:
            log(f"     {i}  ({r})")
        _more(log, len(odd) - 8, T)
    else:
        log("   " + T("qa3_ok"))

    log(T("qa4"))
    # Ищем в переводе куски, написанные письменностью оригинала. Если
    # оригинал и перевод пишутся одинаково (немецкий → французский),
    # искать нечего — проверку пропускаем, а не выдаём книгу целиком.
    ssc, tsc = lang.script_of(src_lang) or _script_of_text(src.values()), lang.script_of(to)
    if ssc and tsc and ssc != tsc:
        rng = lang.SCRIPTS[ssc]
        pat = re.compile(rf"[{rng}]{{4,}}")
        # Идём по блокам книги, а не по переводам: перевод есть и у того, что
        # потом пометили `asis`, — в книгу он не пойдёт, и спрашивать с него
        # нечего.
        left = [i for i in src if i in tr and len(pat.findall(strip(tr[i]))) >= 3]
        if left:
            log("   " + T("qa4_bad", len(left)))
            # Показываем найденное, а не начало блока. Начало почти всегда на
            # языке перевода, и по нему не видно, на что правило сработало:
            # на именах, на названиях в сноске или на абзаце, который остался
            # непереведённым. Раздел из-за этого читать было нечем.
            for i in left[:8]:
                found, seen = [], set()
                for w in pat.findall(strip(tr[i])):
                    if w.lower() not in seen:
                        seen.add(w.lower())
                        found.append(w)
                log(f"     {i}: {_cut(' · '.join(found), 80)}")
            _more(log, len(left) - 8, T)
        else:
            log("   " + T("qa4_none"))
    else:
        log("   " + T("qa4_skip", ssc or "?"))

    # Раздела нет вовсе, когда нет правил: строка «правил нет» в каждом
    # Ссылка на сноску — единственное место, где потеря видна наверняка:
    # номер стоит и в оригинале, и в переводе, сверять его не с чем не надо.
    # Пропадает она молча, сноска остаётся в конце книги без хозяина, а
    # проверка чисел ловила такое вперемешку с десятками ложных тревог.
    log(T("qa5"))
    marks = re.compile(r"<sup>\s*(\d+)\s*</sup>")
    lost_ref = []
    for i, s in src.items():
        if i not in tr:
            continue
        gone = Counter(marks.findall(s)) - Counter(marks.findall(tr[i]))
        if gone:
            lost_ref.append((i, sorted(gone.elements())))
    total_ref = sum(len(marks.findall(s)) for s in src.values())
    if lost_ref:
        problems += len(lost_ref)
        log("   " + T("qa5_bad", sum(len(x[1]) for x in lost_ref), total_ref))
        for i, ns in lost_ref[:8]:
            log(f"     {i}: {', '.join(ns)}")
        _more(log, len(lost_ref) - 8, T)
    else:
        log("   " + T("qa5_ok", total_ref))

    # прогоне ничего не проверяет и читается как замечание. Про terms.json
    # сказано в README, там ему и место.
    rules = {}
    tp = f"{work}/terms.json"
    if os.path.exists(tp):
        rules = {k: v for k, v in json.load(open(tp, encoding="utf-8")).items()
                 if not k.startswith("_")}
    if rules:
        log(T("qa6"))
        for en, ru in rules.items():
            ids = [i for i, s in src.items() if re.search(re.escape(en), s, re.I)]
            bad = [i for i in ids if i in tr and not re.search(ru, tr[i], re.I)]
            if bad:
                problems += len(bad)
                log("   " + T("qa6_bad", repr(en), ru, len(bad), len(ids)))
                for i in bad[:4]:
                    log(f"     {i}: {strip(tr[i])[:90]}")

    log("")
    log(T("qa_total", problems))
    return problems
