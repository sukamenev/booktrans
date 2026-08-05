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
            s = s.replace(f"&lt;a{i}&gt;", f'<a l:href="{escape(url)}">')
            s = s.replace(f"&lt;/a{i}&gt;", "</a>")
    s = re.sub(r"&lt;/?a\d+&gt;", "", s)      # ярлык без адреса — снять
    return s


def build_fb2(work, meta, blocks, cover, dest, log, partial=False, images=None):
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

    # Список литературы в книгу идёт слово в слово: он не переводился и
    # непереведённым не считается.
    for b in blocks:
        if b.get("asis"):
            tr.setdefault(b["id"], b["text"])
    missing = [b["id"] for b in blocks
               if b["kind"] not in ("break", "image") and b["id"] not in tr]
    if missing and not partial:
        raise SystemExit(f"не переведено {len(missing)} блоков, например {missing[:6]}")
    src = {b["id"]: b["text"] for b in blocks}
    for i in missing:
        tr[i] = src[i]
    if missing:
        log("  " + lang.T("preview_left", len(missing)))

    order = {b["id"]: i for i, b in enumerate(blocks)}
    notes = all_notes(work, order)
    notes = {k: v for k, v in notes.items() if k in tr}

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

    # Строки для читателя — на языке перевода, и нужны они обоим путям:
    # и сборщику fb2 ниже, и писателям остальных форматов.
    code = meta.get("target_lang", "ru")
    st = lang.book_strings(code)

    # формат по расширению: fb2 собирается ниже, остальные — в output.py
    ext = os.path.splitext(dest)[1].lower()
    if ext in output.WRITERS:
        head, body = about_lines(work, st, code)
        items = [("title", head, "_about", None)]
        items += [("p", t, f"_about{i}", None) for i, t in enumerate(body)]
        items += [(b["kind"], tr.get(b["id"], ""), b["id"], b.get("links"))
                  for b in blocks]
        dhead, dbody = details_lines(work, st, blocks)
        if dhead:
            items += [("title", dhead, "_details", None)]
            items += [("p", t, f"_details{i}", None) for i, t in enumerate(dbody)]
        kw = {"cover": cover} if ext == ".epub" else {}
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
    w(f"<book-title>{esc(meta.get('title_target') or meta.get('title')
                or st.get('untitled', 'Без названия'))}</book-title>")
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

    for b in blocks:
        text = tr.get(b["id"], "")
        if b["kind"] == "title":
            close_poem()
            if open_sec:
                w("</section>")
            w("<section>")
            w(f"<title><p>{esc(text, b.get('links'), notes_map)}</p></title>")
            open_sec = True
        elif b["kind"] == "subtitle":
            close_poem()
            if not open_sec:
                w("<section>")
                open_sec = True
            w(f"<subtitle>{esc(text)}</subtitle>")
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
                w(f'<image l:href="#{esc(b["text"])}"/>')
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
            w(f"<p>{esc(text, b.get('links'), notes_map)}{a}</p>")
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
    used = {b["text"] for b in blocks if b["kind"] == "image"}
    n_img = 0
    for name, raw in (images or {}).items():
        if name in used and name != "cover.jpg":
            binary(name, raw)
            n_img += 1
    if n_img:
        log("  " + lang.T("images_n", n_img))
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
    t = strip(t)
    if group:
        t = GROUPED.sub(lambda m: GSEP.sub("", m.group()), t)
    return Counter(re.findall(r"\d+", t))


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
    if not items:
        return
    log("")
    log(T("review_head", len(items)))
    # В консоли — первая мысль замечания, целиком — в файле рядом с кусками.
    # Замечание бывает на полстраницы, и вываливать сорок таких в поток значит
    # смыть остальной отчёт.
    p = os.path.join(work, "review.md")
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
    seen = set()
    for it in items:
        k = it["term"].lower()
        if k in seen:
            continue
        seen.add(k)
        flag = "  !! по памяти" if "памят" in it["text"].lower() else ""
        log(f"  [{it['block']}] {it['term']}{flag}")
        log(f"      {_cut(it['text'], 150)}")
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


def qa(work, blocks, log, T=None, src_lang=None, to="ru"):
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
    lost = []
    for i, s in src.items():
        if i not in tr:
            continue
        # Тот же знак бывает и разделителем разрядов, и десятичной запятой:
        # «.003 per cent» → «0,003 процента». Какое прочтение верное, из текста
        # не видно, поэтому число считаем уцелевшим, если оно нашлось хоть при
        # одном из двух.
        a, b = _nums(s), _nums(tr[i]) | _nums(tr[i], group=False)
        if a - b:
            lost.append((i, sorted(a - b)))
    if lost:
        problems += len(lost)
        log("   " + T("qa2_bad", len(lost)))
        for i, x in lost[:8]:
            log(f"     {i}  {x}")
        _more(log, len(lost) - 8, T)
    else:
        log("   " + T("qa2_ok"))

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
    ssc, tsc = lang.script_of(src_lang), lang.script_of(to)
    if ssc and tsc and ssc != tsc:
        rng = lang.SCRIPTS[ssc]
        pat = re.compile(rf"[{rng}]{{4,}}")
        left = [i for i, t in tr.items() if len(pat.findall(strip(t))) >= 3]
        if left:
            log("   " + T("qa4_bad", len(left)))
            for i in left[:8]:
                log(f"     {i}: {_cut(strip(tr[i]), 80)}")
            _more(log, len(left) - 8, T)
        else:
            log("   " + T("qa4_none"))
    else:
        log("   " + T("qa4_skip", ssc or "?"))

    log(T("qa5"))
    rules = {}
    tp = f"{work}/terms.json"
    if os.path.exists(tp):
        rules = {k: v for k, v in json.load(open(tp, encoding="utf-8")).items()
                 if not k.startswith("_")}
    if not rules:
        log("   " + T("qa5_none"))
    else:
        for en, ru in rules.items():
            ids = [i for i, s in src.items() if re.search(re.escape(en), s, re.I)]
            bad = [i for i in ids if i in tr and not re.search(ru, tr[i], re.I)]
            if bad:
                problems += len(bad)
                log("   " + T("qa5_bad", repr(en), ru, len(bad), len(ids)))
                for i in bad[:4]:
                    log(f"     {i}: {strip(tr[i])[:90]}")

    log("")
    log(T("qa_total", problems))
    return problems
