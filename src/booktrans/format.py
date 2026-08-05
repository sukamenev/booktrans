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

WINDOW = 200        # кусков в одном запросе
HEAD, TAIL = 110, 60    # сколько знаков куска показывать с начала и с конца
KINDS = {"+", "title", "skip", "verse", "toc", "code"}
RUN = 3             # столько же похожих заголовков — уже колонтитул
SAME = 0.8          # с какого сходства строки считаются одной и той же
TITLE_MAX = 200     # длиннее — это абзац, а не заголовок


def _show(i, p):
    p = re.sub(r"\s+", " ", p).strip()
    if len(p) > HEAD + TAIL + 3:
        p = p[:HEAD] + " … " + p[-TAIL:]
    return f"{i} {p}"


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


def _toc_lines(out):
    """Названия глав, выписанные моделью из оглавления.

    Правилом их оттуда не достать: страница оглавления бывает в два столбца,
    и `pdftotext -layout` кладёт на одну строку и главу, и запись чужого
    столбца — «Baby John Lilly 5 : Education into Becoming Human».
    """
    got = []
    for m in TOC_TAG.finditer(out):
        for line in m.group(1).splitlines():
            s = _title(line.strip(" -•*|"))
            if "`" in s or "<<<" in s:
                continue                 # модель пересказала само задание
            if 2 <= len(s) <= 120 and s not in got:
                got.append(s)
    return got


def plan(paras, run, log):
    """Пометки для каждого куска и названия глав из оглавления.

    `run` — вызов модели: (prompt) → текст."""
    marks, toc = {}, []
    for lo in range(0, len(paras), WINDOW):
        part = paras[lo:lo + WINDOW]
        body = "\n".join(_show(lo + i + 1, p) for i, p in enumerate(part))
        out = run(body)
        marks.update(_parse(out, lo + 1, lo + len(part)))
        toc += [s for s in _toc_lines(out) if s not in toc]
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


def reconcile(paras, marks, names=()):
    """Сверить найденные заголовки с оглавлением. Возвращает, что вышло.

    `names` — список глав, выписанный моделью. Разбор помеченных строк остаётся
    запасным путём: он годится, пока оглавление свёрстано в один столбец.
    """
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

    if not names:
        found = {named[keys[i - 1]] for i in marks
                 if marks[i] == "title" and keys[i - 1] in named}
    return {"toc": len(toc), "added": added, "dropped": dropped, "cuts": cuts,
            "lost": [t for t in toc if t not in found], "names": list(toc)}


def _split(p, spans):
    """Разрезать кусок по местам, найденным в оглавлении: [(вид, текст), ...].

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
            out.append(("p", head))
        if kind == "title":
            # Номер страницы прилипает к названию вплотную: «From Physics to
            # Biology 53».
            out.append(("title", re.sub(r"\s+\d{1,4}$", "", p[a:b].strip())))
        at = b
    tail = p[at:].strip()
    if tail:
        out.append(("p", tail))
    return out


def apply(paras, marks, cuts=None):
    """Склеить и разметить по пометкам: [(вид, текст), ...].

    Разрезы (места, где заголовок влит в абзац) приходят либо отдельно, либо
    под ключом `cuts` в самих пометках — так они переживают перезапуск, не
    заводя второго файла."""
    out, cuts = [], cuts or marks.get("cuts") or {}
    for i, p in enumerate(paras, 1):
        kind = marks.get(i, "p")
        if kind in ("skip", "toc"):
            continue
        if i in cuts:
            out += _split(p, cuts[i])
            continue
        # Продолжение приклеивается только к прозе. К заголовку — никогда:
        # на одной книге за заголовком шло оборванное слово, помеченное `+`,
        # за ним следующее, и в заголовок уехала глава целиком — 22 261 знак.
        # `+t` ставит только сверка с оглавлением: там известно, что это
        # продолжение названия главы. `+` от модели к заголовку не клеится
        # никогда — так глава уезжала в заголовок целиком.
        if kind == "+t" and out and out[-1][0] == "title":
            out[-1] = (out[-1][0], out[-1][1] + " " + p)
            continue
        if kind == "+" and out and out[-1][0] == "p":
            sep = "" if out[-1][1].endswith("-") else " "
            out[-1] = (out[-1][0], out[-1][1].rstrip("-") + sep + p)
            continue
        # Заголовок длиной в абзац — это не заголовок: по нему режется книга,
        # и целая глава ушла бы в оглавление.
        if kind == "title" and len(p) > TITLE_MAX:
            kind = "p"
        if kind == "title":
            # Колонтитул несёт номер страницы в той же строке: «From Physics
            # to Biology       53».
            p = re.sub(r"\s{2,}\d{1,4}\s*$", "", p)
        out.append(("p" if kind == "+" else kind, p))
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
