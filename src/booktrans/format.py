"""Разметка книг без разметки: pdf и простой текст.

В epub и fb2 структура записана самим издательством, а в pdf её нет вовсе.
Правилами она не выводится: на одной книге колонтитул «THE SCIENTIST»
распознался пятнадцатью разными способами и все пятнадцать стали заголовками
глав, а настоящие заголовки не нашлись ни разу.

Через модель проходит не текст, а его опись: куски пронумерованы, обратно
приходят одни пометки. Текст остаётся байт в байт — подменить его модель не
может при всём желании.
"""
import collections
import difflib
import re

from .agent import AgentError

WINDOW = 200        # кусков в одном запросе
HEAD, TAIL = 110, 60    # сколько знаков куска показывать с начала и с конца
KINDS = {"+", "+t", "title", "skip", "verse", "toc", "code"}
RUN = 3             # столько же похожих заголовков — уже колонтитул
SAME = 0.8          # с какого сходства строки считаются одной и той же
TITLE_MAX = 200     # длиннее — это абзац, а не заголовок
NOTHING = 80        # ответ короче и без пометок — «исключений нет», а не отказ

# Абзац, разорванный концом страницы. Метка `+` для этого и есть, но модель
# ставит её не всегда: на живой книге из 1733 абзацев 217 остались разорваны
# посреди фразы, и переводчик получал по половине предложения. Признак
# машинный и надёжнее пометки: слово с переносом или обрыв без знака, а
# следом строчная буква. Прописная — это уже новый абзац, даже после
# переноса: продолжение потерялось, и склейка дала бы «He obAs a consequence».
GLUE_HYPH = re.compile(r"[^\W\d_]-$", re.U)      # …в постели и слу-
GLUE_OPEN = re.compile(r"[^\W\d_][,;]?$", re.U)  # …подцепил компас


def _show(i, p, photo=False):
    p = re.sub(r"\s+", " ", p).strip()
    if len(p) > HEAD + TAIL + 3:
        p = p[:HEAD] + " … " + p[-TAIL:]
    return f"{i} {'[фото] ' if photo else ''}{p}"


def _parse(out, lo, hi):
    """Ответ модели → {номер куска: метка}. Чужие номера отбрасываем."""
    got = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s*$", line)
        if not m:
            continue
        i, kind = int(m.group(1)), m.group(2).strip("`*.,")
        if lo <= i <= hi and kind in KINDS:
            got[i] = kind
    return got


TOC_TAG = re.compile(r"<<<TOC>>>(.*?)(?=<<<|\Z)", re.S)
MARKLINE = re.compile(r"\s*\d+\s+(\+t?|title|skip|verse|toc|code)\s*$")


def _toc_lines(out):
    """Названия глав, выписанные моделью из оглавления.

    Правилом их оттуда не достать: страница оглавления бывает в два столбца,
    и `pdftotext -layout` кладёт на одну строку и главу, и запись чужого
    столбца — «Baby John Lilly 5 : Education into Becoming Human».
    """
    got = []
    for m in TOC_TAG.finditer(out):
        for line in m.group(1).splitlines():
            # Модель порой дописывает пометки уже после списка глав, и они
            # уходили в оглавление главами: «2020 skip», «2105 title».
            if MARKLINE.match(line):
                break
            s = _title(line.strip(" -•*|"))
            if "`" in s or "<<<" in s or s.endswith(":"):
                continue                 # модель пересказала само задание
            if 2 <= len(s) <= 120 and s not in got:
                got.append(s)
    return got


def plan(paras, run, log, photo=(), tries=1, resume=None, save=None):
    """Пометки для каждого куска и названия глав из оглавления.

    `photo` — номера кусков, стоящих на странице с фотографией: подпись под
    ней без этого выглядит мусором.

    `run` — вызов модели: (prompt, попытка) → текст. Попытка нужна затем, что
    отказ разметки ничего не бросает: модель отвечает словами, а не пометками,
    и окно в две сотни кусков возвращается пустым. Двести кусков подряд без
    единой пометки — это не книга без заголовков, это несостоявшийся ответ, и
    тогда окно передаётся следующей модели цепочки.

    Сбой у поставщика (502, отвалившийся вход) — тоже повод взять следующую
    модель: она нередко у другого поставщика и стоит. Ошибка отдаётся наружу
    только если её выдала вся цепочка.

    `resume` — что успел прошлый прогон: (пометки, оглавление, докуда дошёл).
    `save` — куда складывать то же самое после каждого окна. Окон бывает под
    сотню, и падение на девяностом не должно стоить восьмидесяти девяти.
    """
    marks, toc, at = resume or ({}, [], 0)
    marks, toc = dict(marks), list(toc)
    n = (len(paras) + WINDOW - 1) // WINDOW
    for w, lo in enumerate(range(0, len(paras), WINDOW), 1):
        if lo < at:
            continue
        part = paras[lo:lo + WINDOW]
        body = "\n".join(_show(lo + i + 1, p, lo + i + 1 in photo)
                         for i, p in enumerate(part))
        # Окон бывает десяток, по семь-восемь секунд каждое, и всё это время
        # конвейер молчал — со стороны неотличимо от зависания.
        log(f"{w}/{n} ", end="")
        got, lines, err = {}, [], None
        for k in range(tries):
            try:
                out = run(body, k)
            except AgentError as e:
                err = e
                continue
            err = None
            got = _parse(out, lo + 1, lo + len(part))
            lines = _toc_lines(out)
            # Молчание законно и обычно: о куске, про который ничего не
            # сказано, сказано «обычный абзац», и на двух сотнях кусков сплошной
            # прозы правильный ответ — пустой. А вот ответ многословный и без
            # единой пометки — это отказ или пересказ задания своими словами.
            if got or lines or len(out.strip()) < NOTHING:
                break
        if err is not None:
            raise err
        marks.update(got)
        toc += [s for s in lines if s not in toc]
        if save:
            save(marks, toc, lo + len(part))
    return marks, toc


LEAD = re.compile(r"[.·‧․\s]{3,}\d*\s*$")   # отточие с номером страницы
# Запись оглавления: название и номер страницы. Со сколькими-то точками между
# ними — надёжно; без них годится любой пробел, но так дробится и «Часть 1».
LEADER = re.compile(r"(.+?)\s*[.·‧․][\s.·‧․]*(\d{1,4})(?=\s|$)")
LOOSE = re.compile(r"(.+?)\s+(\d{1,4})(?=\s|$)")


def _title(s):
    """Строка оглавления без отточия и номера страницы."""
    return re.sub(r"\s+", " ", LEAD.sub("", s)).strip(" .")


def _entries(p):
    """Оглавление приходит и построчно, и одним куском, где строки слиты
    пробелами. Границу записи даёт номер страницы в её конце."""
    for pat in (LEADER, LOOSE):
        got = [m.group(1).strip() for m in pat.finditer(p)]
        if len(got) > 1:
            return got
    return [l.strip() for l in p.splitlines() if l.strip()]


def _key(s, page=False):
    """Ключ для сверки: в оглавлении и в тексте одна глава набрана
    по-разному — отточие, номер страницы, номер главы.

    Номер страницы срезается только у строки оглавления (`page`): в тексте
    цифра в конце заголовка — его часть, и «Глава 1» с «Глава 2» иначе
    становятся одним и тем же.
    """
    s = LEAD.sub(" ", s)
    if page:
        s = re.sub(r"\s+\d{1,4}\s*$", " ", s)      # номер страницы без отточия
    s = re.sub(r"^\s*\d{1,3}\s*[.):]\s*", " ", s)  # номер главы в начале
    return re.sub(r"\W+", "", s, flags=re.U).lower()


def contents(paras, marks):
    """Оглавление книги: {название: ключи}. Оно единственное место, где книга
    сама перечисляет свои главы.

    Ключа два: с числом в конце и без. Заранее не сказать, что это за число —
    номер страницы, который в тексте не повторится, или номер главы, который
    и в тексте стоит («Глава 1»).
    """
    toc = {}
    for i, p in enumerate(paras, 1):
        if marks.get(i) != "toc":
            continue
        for line in _entries(p):
            keys = {k for k in (_key(line), _key(line, page=True)) if len(k) >= 3}
            if keys:
                toc.setdefault(_title(line), set()).update(keys)
    return toc


def _same(ids, keys):
    """Заголовки, похожие друг на друга, — в одну кучу: в плохо распознанном
    pdf колонтитул искажён каждый раз по-своему."""
    groups = []
    for i in ids:
        for g in groups:
            if difflib.SequenceMatcher(None, keys[g[0] - 1], keys[i - 1]).ratio() > SAME:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


TOC_LINE = 260      # длиннее — это абзац, а не строка оглавления


def _trim_toc(paras, marks):
    """Обрезать разметку оглавления по здравому смыслу.

    Модель, увидев список, метит `toc` и дальше по инерции: на живой книге
    так пропали раздел «Editors' Note» с двумя абзацами и целая глава
    «Suckling» — семь кусков подряд, тысяча девятьсот знаков.

    Оглавление в книге одно и идёт подряд, а строка его коротка. Что не
    подходит под это — возвращается в текст: потерянный абзац хуже лишнего.
    """
    hit = sorted(i for i, v in marks.items() if v == "toc")
    if not hit:
        return
    runs, cur = [], [hit[0]]
    for i in hit[1:]:
        if i - cur[-1] <= 2:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    best = max(runs, key=len)
    for run in runs:
        if run is not best:
            for i in run:
                marks[i] = "p"

    # Абзац в конце оглавления — это уже текст следующего раздела. Обрезаем
    # с конца, а не с начала: первая строка оглавления сама бывает длинной,
    # когда страница свёрстана в две колонки и они слились в одну строку.
    trimmed = False
    while len(best) > 1 and len(paras[best[-1] - 1]) > TOC_LINE:
        marks[best.pop()] = "p"
        trimmed = True
    # Прямо перед вернувшимся текстом стоит его заголовок — тот же, что и в
    # оглавлении, оттого и помеченный вместе с ним.
    if trimmed and len(paras[best[-1] - 1]) < 40:
        marks[best[-1]] = "title"


def reconcile(paras, marks, names=()):
    """Сверить найденные заголовки с оглавлением. Возвращает, что вышло.

    `names` — список глав, выписанный моделью. Разбор помеченных строк остаётся
    запасным путём: он годится, пока оглавление свёрстано в один столбец.
    """
    _trim_toc(paras, marks)
    toc = ({n: {_key(n)} for n in names if len(_key(n)) >= 3}
           if names else contents(paras, marks))
    named = {k: name for name, ks in toc.items() for k in ks}
    keys = [_key(p) for p in paras]
    seen = collections.Counter(keys)

    if names:
        added, found, cuts = _by_contents(paras, keys, marks, names)
    else:
        # Глава названа в оглавлении, а на своём месте не отмечена — поднимаем.
        # Только редкую строку: то, что повторяется по всей книге, —
        # колонтитул, даже если оглавление его называет.
        added, found, cuts = 0, set(), {}
        for i, k in enumerate(keys, 1):
            if k in named and seen[k] <= RUN and marks.get(i) not in ("title", "toc", "+"):
                marks[i] = "title"
                added += 1

    # Обратный случай: одинаковых заголовков в книге не бывает. Повтор, которого
    # нет в оглавлении, — колонтитул. Пронумерованные главы («Глава 1», «Глава 2»)
    # тоже похожи друг на друга, но различаются только цифрами — их не трогаем.
    dropped = []
    for ids in _same(sorted(i for i in marks if marks[i] == "title"), keys):
        ks = {keys[i - 1] for i in ids}
        numbered = len(ks) > 1 and len({re.sub(r"\d+", "", k) for k in ks}) == 1
        if len(ids) >= RUN and not ks & set(named) and not numbered:
            # Первое вхождение оставляем заголовком. В невымышленной книге
            # колонтитулом часто служит само название главы, и снять их все
            # значит потерять главу: на одной книге так пропало семь.
            # Первое стоит там, где глава начинается.
            for i in ids[1:]:
                marks[i] = "skip"
            dropped.append(_title(paras[ids[0] - 1]))

    # Заголовок над оглавлением: сами строки выброшены, и раздел «Содержание»
    # остаётся в книге пустым — читатель открывает его и не находит ничего.
    for i in [i for i, v in marks.items() if v == "title"]:
        nxt = next((marks.get(j) for j in range(i + 1, i + 4)
                    if marks.get(j) not in (None, "skip")), None)
        if nxt == "toc" and len(paras[i - 1]) < 40:
            marks[i] = "skip"

    if not names:
        found = {named[keys[i - 1]] for i in marks
                 if marks[i] == "title" and keys[i - 1] in named}
    return {"toc": len(toc), "added": added, "dropped": dropped, "cuts": cuts,
            "lost": [t for t in toc if t not in found], "names": list(toc)}


def _split(p, spans, no):
    """Разрезать кусок по местам, найденным в оглавлении: [(вид, текст, кусок), ...].

    Заголовок главы бывает влит в абзац страницы и отдельной строкой не
    существует вовсе — искать его нечего, надо резать. Колонтитул с тем же
    названием оттуда просто удаляется.
    """
    out, at = [], 0
    for a, b, kind in sorted(spans):
        if a < at:
            continue
        head = p[at:a].strip()
        if head:
            out.append(("p", head, no))
        if kind == "title":
            # Номер страницы прилипает к названию вплотную: «From Physics to
            # Biology       53».
            out.append(("title", re.sub(r"\s+\d{1,4}$", "", p[a:b].strip()), no))
        at = b
    tail = p[at:].strip()
    if tail:
        out.append(("p", tail, no))
    return out


def apply(paras, marks, cuts=None):
    """Склеить и разметить по пометкам: [(вид, текст, номер куска), ...].

    Номер куска — того, с которого блок начался. По нему потом узнаётся
    страница, на которой блок стоит, а по ней — куда встаёт картинка.

    Разрезы (места, где заголовок влит в абзац) приходят либо отдельно, либо
    под ключом `cuts` в самих пометках — так они переживают перезапуск, не
    заводя второго файла."""
    out, cuts = [], cuts or marks.get("cuts") or {}
    for i, p in enumerate(paras, 1):
        kind = marks.get(i, "p")
        if kind in ("skip", "toc"):
            continue
        if i in cuts:
            out += _split(p, cuts[i], i)
            continue
        # `+t` — продолжение названия: вторая строка разорванного заголовка,
        # подзаголовок, имя автора под ним. Длина ограничена: заголовок,
        # доросший до абзаца, разрезал бы книгу не там.
        if kind == "+t" and out and out[-1][0] == "title" \
                and len(out[-1][1]) + len(p) <= TITLE_MAX:
            out[-1] = (out[-1][0], out[-1][1] + " " + p, out[-1][2])
            continue
        # Модель промолчала, а абзац оборван на полуслове — доклеиваем сами.
        # Только к прозе и только к прозе: стих кончается без точки, и склейка
        # свела бы его в один абзац.
        if kind == "p" and out and out[-1][0] == "p" and p[:1].islower() \
                and (GLUE_HYPH.search(out[-1][1]) or GLUE_OPEN.search(out[-1][1])):
            kind = "+"
        # Продолжение приклеивается только к прозе. К заголовку — никогда:
        # на одной книге за заголовком шло оборванное слово, помеченное `+`,
        # за ним следующее, и в заголовок уехала глава целиком — 22 261 знак.
        if kind == "+" and out and out[-1][0] == "p":
            sep = "" if out[-1][1].endswith("-") else " "
            out[-1] = (out[-1][0], out[-1][1].rstrip("-") + sep + p, out[-1][2])
            continue
        # Заголовок длиной в абзац — это не заголовок: по нему режется книга,
        # и целая глава ушла бы в оглавление.
        if kind == "title" and len(p) > TITLE_MAX:
            kind = "p"
        if kind == "title":
            # Колонтитул несёт номер страницы в той же строке: «From Physics
            # to Biology       53».
            p = re.sub(r"\s{2,}\d{1,4}\s*$", "", p)
        out.append(("p" if kind == "+" else kind, p, i))
    return out



PROSE = 60          # заголовок длиннее — скорее всего строка прозы
TOC_GAP, TOC_MANY = 3, 4     # столько названий вплотную — это оглавление
TRUST = 8                    # столько найденных глав — оглавлению можно верить
NEAR = 0.75                  # с какого сходства название считается тем же


def _rx(name):
    """Название главы как образец: пробелы в pdf гуляют, регистр тоже."""
    return re.compile(r"\s+".join(re.escape(w) for w in name.split()), re.I)


def _where(paras, keys, marks, name):
    """Все места, где встречается название: (кусок, начало, конец).

    Начало и конец — по тексту куска; для куска, состоящего из одного лишь
    названия, это он весь. Названия глав стоят и в колонтитулах, поэтому
    вхождений почти всегда несколько.
    """
    k, rx, out = _key(name), _rx(name), []
    for i, p in enumerate(paras, 1):
        if marks.get(i) == "toc":
            continue
        if keys[i - 1] == k:
            out.append((i, 0, len(p)))
        elif len(p) > 100 and len(name.split()) > 1:
            # Внутри абзаца режем осторожно: односложное название («Рождение»,
            # «Переход») встречается в прозе обычным словом, и книга рвалась
            # посреди фразы. Двух слов подряд хватает, но и они должны стоять
            # как заголовок — не строчными.
            out += [(i, m.start(), m.end()) for m in rx.finditer(p)
                    if not m.group().islower()]
    return out


def _by_contents(paras, keys, marks, names):
    """Расставить главы по оглавлению. Возвращает (сколько, что нашлось, разрезы).

    Оглавление — единственное место, где книга сама перечисляет свои главы, и
    здесь оно главное, а не подсказка. Без него выходит то, что вышло на живой
    книге: «A Being» и «Makes a Choice» двумя главами, «Electronics
    Connecting» обрубком, а строка прозы — главой.

    Каждая глава ищется по порядку, после предыдущей. Найденное место —
    заголовок, **все остальные вхождения удаляются**: то же название стоит в
    колонтитуле каждой страницы главы, и разрезать книгу по нему нельзя.
    """
    # Страница содержания: на ней стоят сразу многие названия, и разрезать
    # книгу по ним нельзя. Обычно её помечает разметка, но полагаться на это
    # одно нельзя — цена промаха слишком велика.
    rxs = [_rx(n) for n in names if len(_key(n)) >= 3]
    hit = [i for i, p in enumerate(paras, 1) if any(rx.search(p) for rx in rxs)]
    a2 = 0
    while a2 < len(hit):
        # Оглавление свёрстано в столбик и разбирается на десяток кусков по
        # названию в каждом: они идут вплотную. В самой книге названия глав
        # стоят через десятки кусков, и в такую цепочку не складываются.
        b2 = a2
        while b2 + 1 < len(hit) and hit[b2 + 1] - hit[b2] <= TOC_GAP:
            b2 += 1
        if b2 - a2 + 1 >= TOC_MANY:
            for i in range(hit[a2], hit[b2] + 1):
                marks[i] = "toc"
        a2 = b2 + 1
    # Названия глав идут подряд и в самой книге — там, где главы коротки.
    # Обрезаем и эту разметку тем же правилом, иначе она вернёт в оглавление
    # то, что уже было из него вызволено.
    _trim_toc(paras, marks)

    at, found, starts, cuts = 0, set(), set(), {}
    for name in names:
        if len(_key(name)) < 3:
            continue
        hits = _where(paras, keys, marks, name)
        if not hits:
            # Заголовок разорван по строкам: «A Being» + «Makes a Choice».
            # Пробуем склеить соседние куски здесь же, а не вторым проходом:
            # к нему `at` уже ушёл бы дальше по книге, и место потерялось.
            j = _join(paras, keys, marks, _key(name), at, starts)
            if j:
                found.add(name)
                at = j
            continue
        head = next((h for h in hits if h[0] > at), None) or hits[0]
        for i, a2, b2 in hits:
            whole = (a2, b2) == (0, len(paras[i - 1]))
            if (i, a2, b2) == head:
                if whole:
                    marks[i] = "title"
                    starts.add(i)
                else:
                    cuts.setdefault(i, []).append([a2, b2, "title"])
            elif whole:
                marks[i] = "skip"
            else:
                cuts.setdefault(i, []).append([a2, b2, "drop"])
        found.add(name)
        at = head[0]

    # Оглавление нашлось почти всё — значит ему можно верить и в обратную
    # сторону: длинная строка, которой в нём нет, это проза, а не глава.
    # Понижаем до абзаца, а не выбрасываем: текст автора остаётся в книге.
    # Оглавлению верим, когда нашлось хотя бы TRUST глав. Долей от списка
    # мерить нельзя: модель приносит из него и подписи к иллюстрациям, и
    # разделы, которых в тексте нет, — на живой книге вышло 25 из 72, и
    # проверка молчала.
    if len(found) >= min(TRUST, max(3, len(names) // 2)):
        known = {_key(n) for n in found}
        for i in [i for i, v in marks.items() if v == "title"]:
            if i in starts:
                continue
            k = keys[i - 1]
            # Обрубок названия: разметка приняла за главу первую строку
            # разорванного заголовка — «From Physics» при «From Physics to
            # Biology». Он входит в настоящее название и сам главой не был.
            stump = k and k not in known and any(
                k in x and len(k) < len(x) for x in known)
            if stump or len(paras[i - 1]) > PROSE:
                marks[i] = "p"
    return len(found), found, cuts


def _join(paras, keys, marks, k, at, starts):
    """Склеить два-три соседних куска в заголовок. Возвращает, где он кончился.

    Нестрого: книга и её оглавление расходятся в слове чаще, чем кажется. На
    странице «Conference of Three Beings», в содержании «First Conference of
    Three Beings» — сходство 0.90. Спутать первую главу со второй порядок не
    даёт: каждая ищется после предыдущей.
    """
    for i in range(at, len(paras)):
        joined, best = "", None
        for j in range(i, min(i + 3, len(paras))):
            if len(paras[j]) > 100 or marks.get(j + 1) == "toc":
                break
            joined += keys[j]
            if len(joined) >= 8 and difflib.SequenceMatcher(
                    None, joined, k).ratio() > NEAR:
                best = j
        if best is not None:
            marks[i + 1] = "title"
            starts.add(i + 1)
            for m in range(i + 2, best + 2):
                marks[m] = "+t"
            return best + 1
    return None
