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

    # Глава названа в оглавлении, а на своём месте не отмечена — поднимаем.
    # Только редкую строку: то, что повторяется по всей книге, — колонтитул,
    # даже если оглавление его называет.
    added = 0
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

    found = {named[keys[i - 1]] for i in marks
             if marks[i] == "title" and keys[i - 1] in named}
    return {"toc": len(toc), "added": added, "dropped": dropped,
            "lost": [t for t in toc if t not in found], "names": list(toc)}


def apply(paras, marks):
    """Склеить и разметить по пометкам: [(вид, текст), ...]."""
    out = []
    for i, p in enumerate(paras, 1):
        kind = marks.get(i, "p")
        if kind in ("skip", "toc"):
            continue
        # Продолжение приклеивается только к прозе. К заголовку — никогда:
        # на одной книге за заголовком шло оборванное слово, помеченное `+`,
        # за ним следующее, и в заголовок уехала глава целиком — 22 261 знак.
        if kind == "+" and out and out[-1][0] == "p":
            sep = "" if out[-1][1].endswith("-") else " "
            out[-1] = (out[-1][0], out[-1][1].rstrip("-") + sep + p)
            continue
        # Заголовок длиной в абзац — это не заголовок: по нему режется книга,
        # и целая глава ушла бы в оглавление.
        if kind == "title" and len(p) > TITLE_MAX:
            kind = "p"
        out.append(("p" if kind == "+" else kind, p))
    return out
