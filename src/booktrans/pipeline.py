"""Нарезка, перевод, редактура. Все проходы возобновляемые."""
import collections
import difflib
import concurrent.futures as cf
import hashlib
import json
import os
import re
import threading
import time

from .agent import Fatal

# Прерывание с клавиатуры. При работе в несколько потоков одного Ctrl+C мало:
# рабочие потоки не видят исключения главного, продолжают запросы, и человек
# жмёт снова и снова, а в конце получает трассировку из недр threading.
# Флаг решает это просто: потоки сами останавливаются на ближайшей проверке.
STOP = threading.Event()
from .lang import T

TARGET_WORDS = 2600
MAX_WORDS = 3600
# Второй ограничитель, по числу блоков. Считать один размер по словам мало:
# у стихов строка — отдельный блок в три-четыре слова, и в кусок на 800 слов
# их набивается под две сотни. Столько идентификаторов модель в точности не
# возвращает — на Белянине из такого куска пропало 52 строки разом.
MAX_BLOCKS = 70
# Если строфы не размечены, стихи режем группами по столько строк. Разрез
# посреди четверостишия непоправим: рифмующаяся строка уходит в другой
# запрос, и переводчик её уже не видит.
VERSE_GROUP = 4
# Для стихов предел куда строже, чем для прозы. Замерено на живой книге:
# кусок из 58 стихотворных строк переводился 1220 секунд и стоил $2.24, тогда
# как прозаический кусок втрое большего объёма — 104 секунды и $0.31. Рифма
# и размер обходятся модели вдесятеро дороже, и держать их помногу в одном
# запросе незачем: строфы друг от друга не зависят.
MAX_VERSE = 24
TAIL_PARAS = 3
LOOKAHEAD_WORDS = 150
TERMS_BUDGET = 6000
TERMS_TAIL = 1200      # сколько сверх отобранного добавить свежих терминов
DIGEST_EVERY = 8        # как часто пересжимать конспект
DIGEST_BUDGET = 4000    # предел его размера в знаках
HEAD_KINDS = ("title", "subtitle")


def words(s):
    return len(re.sub(r"<[^>]+>", " ", s).split())


def strip(s):
    return re.sub(r"<[^>]+>", "", s)


def fingerprint(t):
    """Отпечаток исходного текста блока.

    Идентификатор блока позиционный: `s51.b0002` — это пятьдесят первый
    раздел, второй абзац. Стоит книге перечитаться с другой разметкой — и
    тот же идентификатор указывает уже на другой текст. Готовность считалась
    по одним идентификаторам, поэтому кусок объявлялся переведённым, а перевод
    доставался чужому абзацу: на живой статье так съехало восемнадцать блоков,
    и в книгу они попали с чужим текстом, молча.

    Отпечаток кладётся рядом с переводом и сверяется при возобновлении.
    """
    return hashlib.sha1(
        re.sub(r"\s+", " ", strip(t)).strip().encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- нарезка

def _cut_points(blocks):
    """Где резать можно. Внутри стихотворения — только по границам строф,
    а если их нет — через каждые VERSE_GROUP строк."""
    ok = set()
    run = 0
    for i, b in enumerate(blocks):
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if b["kind"] == "verse":
            run += 1
        else:
            run = 0
        if nxt is None or nxt["kind"] != "verse" or b["kind"] != "verse":
            ok.add(i)                       # граница стихов и прозы
            continue
        if run % VERSE_GROUP == 0:
            ok.add(i)                       # ровно столько-то строк — можно
    return ok


def _split_section(blocks):
    """Одну секцию — на примерно равные части, а не «до предела и огрызок»."""
    total = sum(words(b["text"]) for b in blocks)
    n_blocks = sum(1 for b in blocks if b["kind"] in ("p", "verse", "note", "table"))
    if total <= MAX_WORDS and n_blocks <= MAX_BLOCKS:
        return [blocks]
    n = max(1, round(total / TARGET_WORDS))
    while total / n > MAX_WORDS:
        n += 1
    # ...и столько же раз, сколько нужно, чтобы блоков в куске было немного
    n = max(n, -(-n_blocks // MAX_BLOCKS))
    target = total / n
    ok = _cut_points(blocks)
    parts, cur, cw, done = [], [], 0, 0
    for i, b in enumerate(blocks):
        cur.append(b)
        cw += words(b["text"])
        rest = total - done - cw
        # Предел по блокам сильнее предполагаемого числа частей: стихи
        # сбиваются в конец главы, и без этого весь остаток лёг бы
        # в последний кусок одной кучей.
        n_cur = sum(1 for x in cur if x["kind"] in ("p", "verse", "note", "table"))
        n_verse = sum(1 for x in cur if x["kind"] == "verse")
        if n_verse >= MAX_VERSE and rest > 0 and i in ok:
            parts.append(cur)              # стихов набралось предельно
            done += cw
            cur, cw = [], 0
            continue
        if n_cur >= MAX_BLOCKS and rest > 0 and i in ok:
            parts.append(cur)
            done += cw
            cur, cw = [], 0
            continue
        if len(parts) == n - 1:
            continue
        if cw >= target * 0.75:
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if (cw >= target or (nxt and nxt["kind"] == "break")) and i in ok:
                if rest >= target * 0.5:
                    parts.append(cur)
                    done += cw
                    cur, cw = [], 0
    if cur:
        parts.append(cur)
    return parts


def make_chunks(blocks):
    """Границу секции (заголовка) не пересекаем: в одном запросе — один кусок
    текста с одной интонацией. Заголовки прилипают к следующей секции."""
    sections, cur = [], []
    for b in blocks:
        if b["kind"] == "title" and any(x["kind"] == "p" for x in cur):
            sections.append(cur)
            cur = []
        cur.append(b)
    if cur:
        sections.append(cur)

    # Крошечные разделы подряд склеиваем. Такое бывает в глоссариях и
    # указателях, где каждая статья набрана подзаголовком: у одной книги так
    # вышло сорок кусков по семьдесят слов, и каждый платил полную цену за
    # системный промпт со справочником. Настоящие главы не трогаем — только
    # то, что заведомо мельче четверти куска.
    small = TARGET_WORDS // 4
    merged, buf = [], []
    for sec in sections:
        w = sum(words(b["text"]) for b in sec)
        if w < small:
            buf.append(sec)
            if sum(words(b["text"]) for s2 in buf for b in s2) >= TARGET_WORDS:
                merged.append([b for s2 in buf for b in s2])
                buf = []
            continue
        if buf:
            merged.append([b for s2 in buf for b in s2])
            buf = []
        merged.append(sec)
    if buf:
        merged.append([b for s2 in buf for b in s2])
    sections = merged

    chunks = []
    for sec in sections:
        for part in _split_section(sec):
            chunks.append({"blocks": part})
    last = ""
    for i, c in enumerate(chunks, 1):
        c["index"] = i
        c["words"] = sum(words(b["text"]) for b in c["blocks"])
        heads = [strip(b["text"]) for b in c["blocks"] if b["kind"] == "title"]
        if heads:
            last = heads[0][:40]
            c["label"] = last
        else:
            # продолжение длинного раздела: показываем тот же заголовок,
            # иначе в выводе идут безымянные прочерки
            c["label"] = f"{last} (прод.)" if last else ""
    return chunks


def translatable(blocks):
    """В модель идут абзацы и стихотворные строки. Заголовки — отдельной
    таблицей: короткие строки в начале запроса модель принимает за шапку
    промпта и теряет. Помеченное `asis` (список литературы) не идёт вовсе:
    оно попадёт в книгу как есть."""
    return [b for b in blocks
            if b["kind"] in ("p", "verse", "note", "table") and not b.get("asis")]


# ---------------------------------------------------------------- общее

# Чем помечается блок в запросе: проза, стих, таблица. Метка нужна модели —
# по ней видно, что строки не разрывать и разделители не трогать.
MARK = {"verse": "V", "table": "T"}


def parse_blocks(out, expected=None, allowed=None, extra_tag=None):
    tail = ""
    if extra_tag:
        m = re.search(rf"<<<{extra_tag}>>>\s*(.*)$", out, re.S)
        if m:
            tail = m.group(1).strip()
        out = re.split(rf"<<<{extra_tag}>>>", out)[0]
    got = {}
    for m in re.finditer(r"<<<[PVT]\s+(\S+?)>>>\s*(.*?)(?=<<<[PVT]\s+\S+?>>>|$)", out, re.S):
        got[m.group(1)] = m.group(2).strip()
    empty = [k for k, v in got.items() if not v]
    if expected is not None:
        missing = [i for i in expected if i not in got]
        extra = [i for i in got if i not in expected]
        if missing or extra or empty:
            # Сплошной хвост пропущенного — это не сбой разметки, а обрыв:
            # модель дошла до какого-то места и дальше писать не стала.
            # Чаще всего упирается в содержание — насилие, телесность,
            # откровенная сцена. Сказать об этом надо прямо, иначе человек
            # часами проверяет размеры кусков и бюджеты токенов, как было.
            if missing and not extra and not empty \
                    and missing == expected[expected.index(missing[0]):]:
                raise Truncated(missing[0], len(missing), len(expected))
            raise ValueError(f"не сошлись абзацы: пропущено {missing[:4]} ({len(missing)}), "
                             f"лишних {extra[:4]}, пустых {empty[:4]}")
    if allowed is not None:
        unknown = [i for i in got if i not in allowed]
        if unknown or empty:
            raise ValueError(f"неизвестные идентификаторы {unknown[:4]}, пустые {empty[:4]}")
    return got, tail


def parse_notes_blocks(out, allowed):
    """Блоки <<<NOTE id вид>>> из ответа. Общий разбор для сносок и редактуры."""
    items = []
    for m in re.finditer(r"<<<NOTE\s+(\S+?)\s+(reference|fact|term|source)>>>\s*"
                         r"TERM:\s*(.*?)\n\s*TEXT:\s*(.*?)(?=<<<|\Z)", out, re.S):
        bid, kind, term, text = m.groups()
        if bid in allowed and text.strip():
            items.append({"block": bid, "kind": kind, "term": term.strip(),
                          "text": " ".join(text.split())})
    return items


class Refused(RuntimeError):
    """Модель дважды остановилась на одном месте: скорее всего, не хочет
    переводить это содержание, а не ошибается с разметкой."""

    def __init__(self, first, n, total):
        self.first, self.n, self.total = first, n, total
        super().__init__(f"перевод остановлен на блоке {first}")


class Truncated(ValueError):
    """Ответ оборвался: с какого-то блока и до конца куска ничего нет."""

    def __init__(self, first, n, total):
        self.first, self.n, self.total = first, n, total
        super().__init__(f"ответ оборван на блоке {first}: "
                         f"не хватает {n} из {total}")


def _run(agent, system, prompt, retries, parse_fn, log):
    cur = prompt
    stops, last = [], None
    for attempt in range(1, retries + 1):
        if STOP.is_set():
            raise KeyboardInterrupt
        try:
            t0 = time.time()
            out, meta = agent.run(system, cur)
            return parse_fn(out), meta, time.time() - t0
        except Truncated as e:
            log("\n    " + T("retry", attempt, e))
            # Оборвалось дважды на одном и том же месте — дело не в
            # случайности и не в размере куска: модель не хочет переводить
            # именно это. Дальнейшие попытки бесполезны и стоят денег.
            # Подряд они идти не обязаны: на живой книге модель вставала на
            # b0038, b0034, b0002, b0034, b0038 — пять раз об одно и то же,
            # и ни разу дважды кряду.
            if e.first in stops:
                raise Refused(e.first, e.n, e.total) from None
            stops.append(e.first)
            last = e
            cur = prompt + (f"\n\n---\n\nВАЖНО: прошлая попытка отвергнута — {e}\n"
                            "Верни РОВНО требуемые идентификаторы, каждый один раз.")
        except Fatal:
            raise                       # повторять нечего: беда не в тексте
        except Exception as e:
            log("\n    " + T("retry", attempt, e))
            cur = prompt + (f"\n\n---\n\nВАЖНО: прошлая попытка отвергнута — {e}\n"
                            "Верни РОВНО требуемые идентификаторы, каждый один раз.")
    if last is not None:
        # Кусок обрывался до последней попытки — это отказ, а не сбой связи.
        # Через Refused его подхватит запасная модель; RuntimeError валил
        # весь прогон, и подстраховка, ради которой её и задают, не звалась.
        raise Refused(last.first, last.n, last.total) from None
    raise RuntimeError("исчерпаны попытки")


# ---------------------------------------------------------------- перевод

def _mentions(term, text):
    """Встречается ли термин оригинала в этом тексте.

    Ключом бывает и словосочетание, и перечисление через запятую, а в тексте
    слово стоит в другом числе или с притяжательным окончанием. Поэтому ищем
    по началу слова и по огрызку без последней буквы: «thallows» так найдётся
    в «thallow», а «baldo nuts» — по слову «baldo».
    """
    for part in re.split(r"[,/;]| и | and ", term):
        for w in sorted(part.split(), key=len, reverse=True)[:2]:
            w = w.strip("«»\"'()[].:;!?—-")
            # В письме без пробелов имя длиной в два знака — обычное дело,
            # и границы слова там нет: ищем подстрокой.
            solid = any("⺀" <= c <= "鿿" or "가" <= c <= "힯"
                        for c in w)
            if len(w) < (2 if solid else 4):
                continue
            stem = w[:-1] if len(w) > 4 and w.endswith("s") else w
            pat = re.escape(w) if solid else rf"\b{re.escape(stem)}"
            if re.search(pat, text, re.I):
                return True
    return False


def accumulated_terms(state, upto, text=None):
    """Термины из ранее переведённых кусков.

    Без этого модель не видит своих же решений и даёт одному термину разные
    переводы в разных частях книги. Побеждает первое вхождение.

    Справочник растёт всю книгу и в предел не влезает: на романе средней
    длины его набирается втрое больше. Обрезать список с конца — худшее,
    что можно сделать: выпадают как раз недавние имена, которые вот-вот
    встретятся снова, а место занимают термины из первой главы. Поэтому
    сначала идёт то, что есть в самом куске (`text`), а остаток предела
    добивается свежими.
    """
    seen = {}
    for k in sorted((int(x) for x in state.get("terms", {}))):
        if k >= upto:
            break
        for line in state["terms"][str(k)].splitlines():
            line = line.strip()
            if "=" not in line or line.lower().startswith(("нет", "none")):
                continue
            en, ru = (p.strip() for p in line.split("=", 1))
            if en and ru and ru != "—" and en not in seen:
                seen[en] = ru
    hot, cold = [], []
    for en, ru in seen.items():
        s = f"{en} = {ru}"
        (hot if text and _mentions(en, text) else cold).append(s)
    if text is None:              # отбирать не по чему — берём свежие
        hot, cold = cold[::-1], []
    out, size = [], 0
    for s in hot:
        if size + len(s) <= TERMS_BUDGET:
            out.append(s)
            size += len(s)
    # Немного свежего сверх отобранного: имя может стоять в куске в другой
    # форме, чем записано, и отбор его прозевает. Но добивать остаток предела
    # балластом незачем — термин, которого в куске нет, модели не нужен, а
    # платится он в каждом запросе.
    room = min(TERMS_BUDGET - size, TERMS_TAIL)
    for s in cold[::-1]:
        if len(s) <= room:
            out.append(s)
            room -= len(s)
    return out


def condense(state, upto, agent, retries, log):
    """Накопительный конспект вместо скользящего окна.

    Окно последних N сводок теряет начало книги: при переводе 51-го куска
    события первых сорока просто не видны, и забытое обстоятельство
    всплывает ошибкой. Здесь общий конспект раз в несколько кусков
    пересжимается заново — ранние события не исчезают, а ужимаются
    до строчки и доживают до конца.
    """
    digest = state.get("digest", "")
    done = state.get("digest_upto", 0)
    fresh = [state["sum"][str(k)] for k in range(done + 1, upto)
             if str(k) in state["sum"]]
    if len(fresh) < DIGEST_EVERY:
        return (digest + "\n\n" + "\n\n".join(fresh)).strip()

    prompt = (
        "Ниже конспект прочитанной части книги и свежие заметки о том, что "
        "случилось дальше. Сведи их в один связный конспект.\n\n"
        "Требования: сохранить всё, что может понадобиться дальше — кто есть "
        "кто, что произошло, что изменилось, чем дело кончилось; ранние "
        "события можно ужать до строчки, но не выбрасывать; уложиться "
        f"примерно в {DIGEST_BUDGET} знаков; писать сплошным текстом, "
        "без заголовков и списков.\n\n"
        "## Конспект\n\n" + (digest or "(пока пусто)") +
        "\n\n## Что было дальше\n\n" + "\n\n".join(fresh))
    log("  " + T("digest_go"), end="")
    (new, _), meta, dt = _run(agent, "", prompt, retries, lambda o: (o.strip(), ""), log)
    state["digest"] = new
    state["digest_upto"] = upto - 1
    cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
    log(T("digest_done", len(new), f"{dt:.0f}", f"{meta['model']}{cost}"))
    return new


def translate_prompt(chunk, nxt, summary, tail, terms, task):
    # стихи помечаем отдельно: иначе модель их не отличит и переведёт прозой,
    # а редактор потом «выправит» ритм окончательно
    src = "\n".join(f"<<<{MARK.get(b['kind'], 'P')} {b['id']}>>>\n{b['text']}"
                    for b in translatable(chunk["blocks"]))
    parts = [task, f"Фрагмент {chunk['index']}."
                   + (f" Раздел: {chunk['label']}." if chunk["label"] else "")]
    if summary:
        parts.append("## Что было в книге до этого места\n\n"
                     "Фон для связности. Не переводить и не пересказывать в ответе.\n\n" + summary)
    if tail:
        parts.append("## Последние абзацы уже готового перевода\n\n"
                     "Продолжай этим же стилем и ритмом; не повторяй только что "
                     "использованные обороты.\n\n" + tail)
    if terms:
        parts.append("## Термины, уже принятые в предыдущих фрагментах\n\n"
                     "Твои же решения из ранее переведённых кусков. Встретишь любой — "
                     "бери отсюда дословно, нового варианта не придумывай.\n\n" + "\n".join(terms))
    parts.append("## Фрагмент для перевода\n\n"
                 "Переведи каждый абзац. Верни все идентификаторы, в том же порядке.\n\n" + src)
    if nxt:
        ahead = " ".join(strip(b["text"]) for b in translatable(nxt["blocks"])[:4])
        ahead = " ".join(ahead.split()[:LOOKAHEAD_WORDS])
        if ahead:
            parts.append("## Что идёт дальше (переводить НЕ надо)\n\n"
                         "Нужно только чтобы не оборвать мысль.\n\n" + ahead)
    return "\n\n---\n\n".join(parts)


# Сколько отказов подряд считать не свойством места, а поломкой. Один отказ —
# это спорная сцена; три подряд означают, что дело уже не в книге: сменилась
# политика модели, кончилась квота, отвалился агент. Идти дальше — жечь деньги.
REFUSE_ROW = 3


def _save(path, obj):
    """Записать файл куска целиком или никак.

    При `--jobs > 1` соседний поток читает хвост предыдущего куска ровно
    тогда, когда этот его пишет, и половина файла — это разбор json с
    ошибкой и падение всего прохода. Подмена готового файла атомарна.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _backups(fallback):
    """Запасные модели списком.

    Проходы получают цепочку: первая модель делает книгу, до второй доходят
    единицы кусков, до третьей — считанные. Одиночную модель принимаем тоже:
    так подстраховка задавалась раньше.
    """
    if not fallback:
        return []
    return list(fallback) if isinstance(fallback, (list, tuple)) else [fallback]


def _chain_run(who, system, prompt, retries, parse, log):
    """`_run` по цепочке: следующая модель подхватывает и отказ, и сбой.

    Проход, работающий одним запросом на большой кусок книги, без этого падал
    целиком: поставщик отвечал «high traffic» пять попыток подряд, и прогон
    кончался, хотя вторая модель цепочки была задана и стояла рядом.
    """
    last = None
    for k, a in enumerate(who):
        if k:
            log("")
            log("    " + T("refused_retry", getattr(a, "model", "?")), end="")
        try:
            return _run(a, system, prompt, retries, parse, log)
        except (Refused, RuntimeError, Fatal) as e:
            last = e
    raise last


def _stop_row(refused, log, force=False, what="translate"):
    """Три отказа подряд.

    Продавить можно только редактуру. Отказ в переводе оставляет в книге
    дыру, а следующий кусок вдобавок лишается хвоста предыдущего и строки
    конспекта — идти дальше вслепую значит портить и то, что переводится.
    Непроредактированный кусок остаётся переведённым и читаемым.
    """
    refused[0] += 1
    if refused[0] < REFUSE_ROW or force:
        return False
    log("")
    log("  " + T("refused_row", refused[0]))
    log("  " + T("refused_row_" + what))
    return True


# Перевод короче этой доли оригинала или длиннее этой — потеря или чужой
# текст. Порог мягкий: на коротких строках отношение шумит, поэтому есть и
# нижняя граница длины.
LOSS_LOW, LOSS_HIGH, LOSS_MIN = 0.5, 2.5, 60


def _regrow(agent, system, task, blocks, res, retries, log):
    """Переспросить блоки, чья длина разошлась с оригиналом.

    Разбор следит только за тем, чтобы идентификаторы были на месте, а что
    внутри них — нет. На живой книге из восьми подозрительных абзацев все
    четыре проверенных оказались испорчены: два обрезаны на полуслове, в
    третьем от записи осталась строка, в четвёртом стоял текст соседнего.
    """
    src = {b["id"]: b["text"] for b in blocks if b["id"] in res}
    off = lambda i, v: len(strip(v)) / max(len(strip(src[i])), 1)   # noqa: E731
    bad = [i for i, v in res.items()
           if i in src and len(strip(src[i])) >= LOSS_MIN
           and not LOSS_LOW <= off(i, v) <= LOSS_HIGH]
    if not bad:
        return res, 0
    body = "\n\n".join(f"<<<P {i}>>>\n{src[i]}" for i in bad)
    prompt = (task + "\n\n---\n\nЭти абзацы уже переводились, и перевод вышел "
              "заметно короче или длиннее оригинала: часть текста потерялась "
              "или попала чужая. Переведи их заново, целиком, ничего не "
              "пропуская и не добавляя от себя.\n\n" + body)
    try:
        (again, _), _, _ = _run(agent, system, prompt, retries,
                                lambda o: parse_blocks(o, expected=bad), log)
    except (Refused, RuntimeError, Fatal):
        return res, 0
    # Берём новое только там, где оно ближе к длине оригинала: переспрос
    # тоже может выйти хуже, и молча заменять им готовое незачем.
    n = 0
    for i in bad:
        if again.get(i) and abs(off(i, again[i]) - 1) < abs(off(i, res[i]) - 1):
            res[i], n = again[i], n + 1
    return res, n


def translate(work, chunks, agent, system, task, retries, log, only=None,
              fallback=None):
    os.makedirs(f"{work}/tr", exist_ok=True)
    os.makedirs(f"{work}/prompts", exist_ok=True)
    sp = f"{work}/state.json"
    state = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {"sum": {}, "terms": {}}
    # готовность считаем по блокам, а не по именам файлов: если нарезка
    # изменилась, старый файл покроет не те блоки и оставит дыру
    have, old = {}, False
    if os.path.isdir(f"{work}/tr"):
        for n in sorted(os.listdir(f"{work}/tr")):
            if not n.endswith(".json"):
                continue
            x = json.load(open(f"{work}/tr/{n}", encoding="utf-8"))
            fp = x.get("src") or {}
            old = old or not fp
            have.update({k: fp.get(k) for k in x["tr"]})
    if old:
        log("  " + T("no_fingerprint"))

    done = skipped = 0
    refused = [0]
    for i, c in enumerate(chunks):
        idx = c["index"]
        if only and idx not in only:
            continue
        out_path = f"{work}/tr/{idx:04d}.json"
        # Готово — это когда и блок тот же, и текст в нём тот же. Отпечатка
        # нет только у файлов от прежних версий: там верим на слово.
        if not only and all(
                b["id"] in have
                and have[b["id"]] in (None, fingerprint(b["text"]))
                for b in translatable(c["blocks"])):
            skipped += 1
            continue
        summary = condense(state, idx, agent, retries, log)
        tail = ""
        prev = f"{work}/tr/{idx - 1:04d}.json"
        if os.path.exists(prev):
            vals = [v for v in json.load(open(prev, encoding="utf-8"))["tr"].values() if v.strip()]
            tail = "\n\n".join(vals[-TAIL_PARAS:])
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        here = " ".join(b["text"] for b in translatable(c["blocks"]))
        prompt = translate_prompt(c, nxt, summary, tail,
                                  accumulated_terms(state, idx, here), task)
        open(f"{work}/prompts/{idx:04d}.txt", "w", encoding="utf-8").write(prompt)

        expected = [b["id"] for b in translatable(c["blocks"])]
        log(f"[{idx:04d}/{len(chunks):04d}] {c['label'][:24]:24s} "
            + T("words_n", f"{c['words']:5d}") + " ... ", end="")
        try:
            (res, extra), meta, dt = _run(
                agent, system, prompt, retries,
                lambda o: _parse_translate(o, expected), log)
        except Refused as e:
            # Показываем сам текст: по нему сразу видно, почему модель встала,
            # и не нужно гадать про размеры кусков и бюджеты токенов.
            src = next((b["text"] for b in c["blocks"] if b["id"] == e.first), "")
            src = re.sub(r"<[^>]+>", "", src)[:150]
            log("")
            log("    " + T("refused", e.first, e.n, e.total))
            log(f"      {src}…")
            # Подстраховка: то же задание следующей модели цепочки. Отказ —
            # свойство модели, а не текста, и у следующей такого запрета может
            # не быть. Идём по цепочке до первой, которая возьмётся.
            backups = _backups(fallback)
            got, last = None, e.first
            for fb in backups:
                log("    " + T("refused_retry", getattr(fb, "model", "?")))
                try:
                    got = _run(fb, system, prompt, retries,
                               lambda o: _parse_translate(o, expected), log)
                    break
                except (Refused, RuntimeError) as e2:
                    last = getattr(e2, "first", last)
            if got is None:
                log("    " + (T("refused_both", last) if backups
                              else T("refused_hint", idx)))
                if _stop_row(refused, log):
                    break
                continue
            (res, extra), meta, dt = got
        refused[0] = 0                 # кусок взят — счётчик отказов сбрасываем
        extra, found = extra
        res, n_grew = _regrow(agent, system, task, translatable(c["blocks"]),
                              res, retries, log)
        if n_grew:
            log("\n    " + T("regrown", n_grew))
        summ, terms = _split_meta(extra)
        _save(out_path, {"index": idx, "model": meta["model"],
                         "cost_usd": meta["cost_usd"], "footnotes": found,
                         "tr": res,
                         "src": {b["id"]: fingerprint(b["text"])
                                 for b in translatable(c["blocks"])}})
        # Пустой конспект — признак сломанного протокола, а не молчания
        # модели: значит, служебные ярлыки в ответе не совпали с теми, что
        # ищет разборщик. Молча это не проходит, потому что конспект и список
        # терминов — то, что держит книгу единой между кусками, и книга
        # соберётся как ни в чём не бывало, только термины разойдутся.
        if not summ.strip():
            log("")
            log("    " + T("no_summary"))
        state["sum"][str(idx)] = summ
        if terms:
            state["terms"][str(idx)] = terms
        json.dump(state, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done += 1
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        log(T("ready_in", f"{dt:.0f}", f"{meta['model']}{cost}"))
    return done, skipped


def _parse_translate(out, expected):
    """Ответ переводчика: абзацы + сноски + служебный блок."""
    ids = {i for i in expected}
    found = parse_notes_blocks(out, ids)
    body = re.split(r"<<<NOTE\s", out)[0]
    res, extra = parse_blocks(body, expected=expected, extra_tag="META")
    m = re.search(r"<<<META>>>\s*(.*)$", out, re.S)
    if m:
        extra = m.group(1).strip()
    return res, (extra, found)


def _split_meta(extra):
    summ = re.search(r"SUMMARY:\s*(.*?)(?=TERMS:|$)", extra, re.S)
    terms = re.search(r"TERMS:\s*(.*)$", extra, re.S)
    return (summ.group(1).strip() if summ else extra.strip(),
            terms.group(1).strip() if terms else "")


# ---------------------------------------------------------------- редактура

def edit(work, chunks, agent, system, task, retries, log, only=None, jobs=1,
         fallback=None, force=False):
    os.makedirs(f"{work}/ed", exist_ok=True)
    now = getattr(agent, "model", None) or ""

    # Черновик собираем по всей книге, а не из файла с тем же номером.
    # Нарезка могла измениться — от новой версии конвейера или от другого
    # --chunk-words, — и тогда tr/0004.json покрывает уже не тот кусок.
    # Прежде это кончалось пустым запросом: редактор честно отвечал, что
    # править нечего, пустой файл правки ложился поверх старого, и сделанная
    # редактура пропадала кусок за куском.
    raw, whose = {}, {}
    d = f"{work}/tr"
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            x = json.load(open(f"{d}/{n}", encoding="utf-8"))
            for k, v in x["tr"].items():
                raw[k] = v
                whose[k] = x.get("model") or ""

    # Что уже отредактировано — тоже по блокам, а не по номерам файлов. Кусок,
    # на котором правка оборвалась, считаем сделанным только до места обрыва.
    ready, stuck = set(), {}
    d = f"{work}/ed"
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            x = json.load(open(f"{d}/{n}", encoding="utf-8"))
            bl = x.get("blocks") or []
            at = x.get("stopped_at") or len(bl)
            fp = x.get("src") or {}
            # Правка сделана по переводу; перевели заново — править надо снова.
            ready.update(i for i in bl[:at]
                         if fp.get(i) in (None, fingerprint(raw.get(i, ""))))
            for i in bl[at:]:
                stuck[i] = x.get("model") or ""

    todo = []
    skipped = 0
    for c in chunks:
        idx = c["index"]
        if only and idx not in only:
            continue
        ids = [b["id"] for b in translatable(c["blocks"])]
        # Кусок, на котором правка оборвалась, готовым не считаем, если
        # редактировать будет другая модель: прежняя упёрлась в содержание,
        # а новая, скорее всего, возьмётся. Той же моделью переделывать
        # незачем — упрётся снова и потратит деньги зря.
        if not only and ids:
            left = [i for i in ids if i not in ready]
            if not left or all(stuck.get(i) == now for i in left):
                skipped += 1
                continue
        if any(i in raw for i in ids):
            todo.append(c)

    done = total = 0
    lock = threading.Lock()

    n_all = len(chunks)
    by_index = {c["index"]: c for c in chunks}
    refused, halt = [0], [False]

    def one(c):
        nonlocal done, total
        if STOP.is_set() or halt[0]:
            return
        idx = c["index"]
        who = (c["label"] or "—")[:24]
        out_path = f"{work}/ed/{idx:04d}.json"
        ids = [b["id"] for b in translatable(c["blocks"])]
        draft = {i: raw[i] for i in ids if i in raw}
        if not draft:
            return
        # Кусок, который основная модель переводить отказалась, она откажется
        # и править: отказ вызван содержанием, а оно никуда не делось.
        # Редактируем той же моделью, что перевела. Замерено: на таком куске
        # основная модель дала 2 правки из 41, и обе в первых двух абзацах.
        mine = agent
        by = whose.get(ids[0], "")
        if by:
            mine = next((f for f in _backups(fallback)
                         if by == getattr(f, "model", None)), mine)
        with lock:
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} " + T("ed_start", f"{len(draft):3d}"))
        # при jobs>1 предыдущий кусок может быть ещё не отредактирован —
        # берём что есть; шов чуть хуже, зато проходы идут одновременно
        tail = ""
        prev = by_index.get(idx - 1)
        if prev:
            # Хвост берём по идентификаторам предыдущего куска: файл правки с
            # тем же номером мог остаться от другой нарезки, и в шов попал бы
            # кусок совсем из другого места книги.
            pids = [b["id"] for b in translatable(prev["blocks"])]
            ptxt = {i: raw[i] for i in pids if i in raw}
            ep = f"{work}/ed/{idx - 1:04d}.json"
            if os.path.exists(ep):
                for k, e in json.load(open(ep, encoding="utf-8"))["edits"].items():
                    if k in ptxt:
                        ptxt[k] = e["new"]
            tail = "\n\n".join([v for v in ptxt.values() if v.strip()][-TAIL_PARAS:])
        # Конспект сюжета, накопленный при переводе. Редактору он нужен не
        # меньше: правя местоимения, обращения и связки, легко исказить смысл,
        # если не знаешь, кто в сцене, что уже случилось и кем персонажи
        # приходятся друг другу.
        digest, terms = "", []
        sp = f"{work}/state.json"
        if os.path.exists(sp):
            st = json.load(open(sp, encoding="utf-8"))
            parts = [st.get("digest", "")]
            parts += [st["sum"][str(k)] for k in range(st.get("digest_upto", 0) + 1, idx)
                      if str(k) in st.get("sum", {})]
            digest = "\n\n".join(p for p in parts if p).strip()
            # Написания, принятые при переводе. Переводчик набирал их по ходу
            # книги и к последним кускам своего же начала уже не помнил —
            # оттуда «балдо» в одной главе и «бальдо» в следующей. Редактор
            # работает после всех и видит справочник целиком, поэтому свести
            # разнобой к одному виду может только он.
            terms = accumulated_terms(
                st, 1 << 30, " ".join(b["text"] for b in translatable(c["blocks"])))
        # только русский текст: оригинал намеренно не показываем, иначе
        # правка идёт в сторону чужого синтаксиса, а не хорошего русского
        pairs = [f"<<<{MARK.get(b['kind'], 'P')} {b['id']}>>>\n{draft[b['id']]}"
                 for b in translatable(c["blocks"]) if b["id"] in draft]
        parts = [task, f"Фрагмент {idx}." + (f" Раздел: {c['label']}." if c["label"] else "")]
        if digest:
            parts.append("## Что было в книге до этого места\n\n"
                         "Правя местоимения, обращения и связки, сверяйся с этим: "
                         "кто в сцене, что уже произошло, кем персонажи приходятся "
                         "друг другу. Править или пересказывать этот текст не надо.\n\n"
                         + digest)
        if tail:
            parts.append("## Хвост предыдущего, уже отредактированного куска\n\n"
                         "Нужен для гладкого стыка. Править его не надо.\n\n" + tail)
        if terms:
            parts.append(
                "## Написания, принятые в этой книге\n\n"
                "Слева — как в оригинале, справа — как решено писать. Список "
                "собран по всей книге, включая куски после этого. Встретишь в "
                "тексте другое написание того же имени или понятия — приведи "
                "к этому, молча: это не повод для замечания.\n\n"
                + "\n".join(terms))
        parts.append("## Фрагмент\n\n" + "\n\n".join(pairs))
        prompt = "\n\n---\n\n".join(parts)
        open(f"{work}/prompts/{idx:04d}.edit.txt", "w", encoding="utf-8").write(prompt)

        def _stopped(res, ids):
            """Оборвалась ли правка. Редактура по замыслу возвращает только
            изменённые абзацы, и оборванный ответ выглядит ровно как
            «посмотрел всё, править нечего». Отличить можно по месту
            последней правки: на здоровом куске они идут по всему тексту,
            а когда модель упирается в содержание — обрываются в начале и
            дальше пусто. Замерено на одной книге: обычный кусок — 27 правок
            из 70, последняя на 70-м блоке; кусок со спорной сценой — 2 из
            41, обе в первых двух."""
            if not res or len(ids) < 20:
                return 0
            last = max((ids.index(k) + 1 for k in res if k in ids), default=0)
            return last if (len(ids) - last) / len(ids) > 0.6 else 0

        ids = list(draft)          # ключи словаря и есть идентификаторы
        (res, notes), meta, dt = _run(
            mine, system, prompt, retries,
            lambda o: parse_blocks(o, allowed=set(draft), extra_tag="NOTES"), log)
        stopped = _stopped(res, ids)
        for fb in _backups(fallback):
            # Оборвалась правка — передаём кусок следующей модели цепочки, как
            # и при отказе перевода. Иначе кусок остался бы наполовину
            # нетронутым, а при следующем запуске зачёлся бы готовым: файл-то
            # записан.
            if not stopped:
                break
            if fb is mine:
                continue          # этой моделью кусок только что и правился
            with lock:
                log("    " + T("edit_stopped", stopped, len(ids)))
                log("    " + T("refused_retry", getattr(fb, "model", "?")))
            (res2, notes2), meta2, dt2 = _run(
                fb, system, prompt, retries,
                lambda o: parse_blocks(o, allowed=set(draft), extra_tag="NOTES"), log)
            if len(res2) > len(res):
                res, notes, meta, dt = res2, notes2, meta2, dt2
                stopped = _stopped(res, ids)
        out = {"index": idx, "model": meta["model"], "cost_usd": meta["cost_usd"],
               "notes": notes, "blocks": ids,
               "src": {k: fingerprint(v) for k, v in draft.items()},
               "edits": {k: {"old": draft[k], "new": v} for k, v in res.items()}}
        if stopped:
            # Помечаем в файле: предупреждение в выводе живёт до конца
            # прогона, а кусок надо будет перередактировать и завтра.
            out["stopped_at"] = stopped
        _save(out_path, out)
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        with lock:
            done += 1
            total += len(res)
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} "
                + T("ed_done", f"{len(res):3d}", f"{len(draft):3d}",
                    f"{dt:.0f}", f"{meta['model']}{cost}"))
            if stopped:
                log("    " + T("edit_stopped", stopped, len(ids)))
                # Три оборванных куска подряд — дело уже не в книге, как и при
                # переводе. При jobs>1 запущенные куски доработают: счёт идёт
                # по приходящим ответам, а не по очереди.
                halt[0] = _stop_row(refused, log, force, "edit")
            else:
                refused[0] = 0

    if jobs > 1 and len(todo) > 1:
        log("  " + T("in_threads", jobs))
        # Не `with`: при выходе он ждёт завершения всех запущенных задач, и
        # Ctrl+C не доходит до обработчика, пока не вернётся последний
        # запрос к модели. Человек жмёт снова и снова и в конце получает
        # трассировку из недр threading. Здесь ожидания нет: очередь
        # отменяется, флаг STOP останавливает потоки, а сделанное уже на диске.
        ex = cf.ThreadPoolExecutor(max_workers=jobs)
        try:
            list(ex.map(one, todo))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        for c in todo:
            one(c)
    return done, skipped, total


def current(work, idx):
    tp = f"{work}/tr/{idx:04d}.json"
    if not os.path.exists(tp):
        return {}
    base = json.load(open(tp, encoding="utf-8"))["tr"]
    ep = f"{work}/ed/{idx:04d}.json"
    if os.path.exists(ep):
        for k, e in json.load(open(ep, encoding="utf-8"))["edits"].items():
            base[k] = e["new"]
    return base


def all_translations(work):
    """Черновик с наложенной редактурой + счётчик правок."""
    tr, edited = {}, 0
    d = f"{work}/tr"
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                tr.update(json.load(open(f"{d}/{n}", encoding="utf-8"))["tr"])
    d = f"{work}/ed"
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            for k, e in json.load(open(f"{d}/{n}", encoding="utf-8"))["edits"].items():
                if k in tr:
                    tr[k] = e["new"]
                    edited += 1
    return tr, edited


# ---------------------------------------------------------------- сноски

def notes(work, chunks, agent, system, task, retries, log, only=None, jobs=1):
    """Предложение сносок и проверка фактов.

    Идёт по кускам, накапливая уже предложенное: одно и то же понятие не должно
    получить сноску дважды. Результат — work/notes.json, который сборщик
    накладывает на текст. Ставит сноски сборщик, а не модель: иначе длинная
    глава, переведённая несколькими запросами, получит пояснение по разу
    на каждый.
    """
    os.makedirs(f"{work}/nt", exist_ok=True)
    todo, skipped = [], 0
    for c in chunks:
        idx = c["index"]
        if only and idx not in only:
            continue
        if os.path.exists(f"{work}/nt/{idx:04d}.json") and not only:
            skipped += 1
            continue
        todo.append(c)

    # Список «уже объяснено» снимается один раз, до начала: при работе в
    # несколько потоков куски не увидят предложений друг друга. Это не беда —
    # окончательная дедупликация по термину всё равно делается при сборке,
    # так что дублей в книге не будет, лишними окажутся лишь предложения.
    already = []
    for n in sorted(os.listdir(f"{work}/nt")):
        if n.endswith(".json"):
            for it in json.load(open(f"{work}/nt/{n}", encoding="utf-8"))["notes"]:
                already.append(it["term"])

    done = total = 0
    lock = threading.Lock()
    n_all = len(chunks)

    def one(c):
        nonlocal done, total
        if STOP.is_set():
            return
        idx = c["index"]
        who = (c["label"] or "—")[:24]
        out_path = f"{work}/nt/{idx:04d}.json"
        tr = current(work, idx)
        if not tr:
            return
        with lock:
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} " + T("nt_start"))

        pairs = [f"<<<P {b['id']}>>>\nОРИГИНАЛ: {b['text']}\nПЕРЕВОД:  {tr[b['id']]}"
                 for b in translatable(c["blocks"]) if b["id"] in tr]
        parts = [task, f"Фрагмент {idx}." + (f" Раздел: {c['label']}." if c["label"] else "")]
        if already:
            parts.append("## Уже объяснено раньше — не повторять\n\n" + "\n".join(sorted(set(already))))
        parts.append("## Фрагмент\n\n" + "\n\n".join(pairs))
        prompt = "\n\n---\n\n".join(parts)
        open(f"{work}/prompts/{idx:04d}.notes.txt", "w", encoding="utf-8").write(prompt)

        def parse_notes(out):
            items = []
            for m in re.finditer(
                    r"<<<NOTE\s+(\S+?)\s+(reference|fact|term|source)>>>\s*TERM:\s*(.*?)\n"
                    r"TEXT:\s*(.*?)(?=<<<NOTE|\Z)", out, re.S):
                bid, kind, term, text = m.groups()
                if bid in tr and text.strip():
                    items.append({"block": bid, "kind": kind,
                                  "term": term.strip(), "text": " ".join(text.split())})
            return items, ""

        (items, _), meta, dt = _run(agent, system, prompt, retries, parse_notes, log)
        _save(out_path, {"index": idx, "model": meta["model"],
                         "cost_usd": meta["cost_usd"], "notes": items})
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        with lock:
            done += 1
            total += len(items)
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} "
                + T("nt_done", f"{len(items):2d}", f"{dt:.0f}",
                    f"{meta['model']}{cost}"))

    if jobs > 1 and len(todo) > 1:
        log("  " + T("in_threads", jobs))
        # Не `with`: при выходе он ждёт завершения всех запущенных задач, и
        # Ctrl+C не доходит до обработчика, пока не вернётся последний
        # запрос к модели. Человек жмёт снова и снова и в конце получает
        # трассировку из недр threading. Здесь ожидания нет: очередь
        # отменяется, флаг STOP останавливает потоки, а сделанное уже на диске.
        ex = cf.ThreadPoolExecutor(max_workers=jobs)
        try:
            list(ex.map(one, todo))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        for c in todo:
            one(c)
    return done, skipped, total


def all_notes(work, order):
    """Все сноски в порядке следования по книге, по одной на блок."""
    got, seen = {}, set()
    src = []
    for sub, key in (("tr", "footnotes"), ("ed", "footnotes"), ("nt", "notes")):
        d = f"{work}/{sub}"
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                src += json.load(open(f"{d}/{n}", encoding="utf-8")).get(key) or []
    for it in src:
        key = it["term"].lower()
        if key in seen:
            continue                    # одно понятие объясняем один раз
        seen.add(key)
        got.setdefault(it["block"], []).append(it)
    merged = {}
    for bid in sorted(got, key=lambda x: order.get(x, 10 ** 9)):
        # Метку «Прим. переводчика» получают все сноски этого прохода, включая
        # выходные данные цитат. Прежде их оставляли без метки как «не
        # примечание, а библиография», но у книги, где авторских сносок нет
        # вовсе, читатель принимал такую сноску за авторскую. Авторские сюда
        # не попадают: они приходят блоками из самой книги.
        merged[bid] = {"text": " ".join(i["text"] for i in got[bid]),
                       "source_only": False,
                       # Цитата по чужому переводу: машина не может
                       # подтвердить, что текст взят из издания, а не
                       # восстановлен по памяти. Читателю говорят об этом
                       # прямо у цитаты, а не только в отчёте.
                       "source": any(i.get("kind") == "source" for i in got[bid])}
    return merged


def format_marks(work, path, agent, task, encoding, ask, log, fallback=None):
    """Разметка книги без разметки. Считается один раз и лежит в работе."""
    from . import extract, format as fmt
    p = f"{work}/marks.json"
    if os.path.exists(p):
        log("  " + T("marks_known"))
        return _load_marks(p)
    paras = extract.plain_paragraphs(path, encoding, ask)
    if not paras:
        return None
    # Разметка идёт окнами по две сотни кусков, и на толстой книге их под
    # сотню: прогон, упавший на девяностом, раньше начинал заново с первого.
    # Годится только та половина работы, что считалась по этому же тексту, —
    # отсюда сверка числа кусков.
    half = f"{work}/marks.part.json"
    resume, at = None, 0
    if os.path.exists(half):
        d = json.load(open(half, encoding="utf-8"))
        if d.get("paras") == len(paras):
            at = d["done"]
            resume = ({int(k): v for k, v in d["marks"].items()}, d["toc"], at)

    def keep(marks, toc, done):
        json.dump({"paras": len(paras), "done": done, "toc": toc,
                   "marks": {str(k): v for k, v in marks.items()}},
                  open(half, "w", encoding="utf-8"), ensure_ascii=False)

    log("  " + T("marks_more", at, len(paras)) if at
        else "  " + T("marks_start", len(paras)), end="")
    t = time.time()
    cost = [0.0]

    who = [agent] + _backups(fallback)

    def run(body, k=0):
        if k:
            log("")
            log("  " + T("refused_retry", getattr(who[k], "model", "?")), end="")
        out, meta = who[k].run("", task + "\n\n---\n\n" + body)
        cost[0] += meta.get("cost_usd") or 0
        return out

    # Оба вызова — вне перебора: внутри него `photo_pages` запускала
    # `pdfimages` по разу на каждый кусок, и на книге в две тысячи кусков
    # разметка «висела» минуты, не сказав ни слова.
    with_photo = extract.photo_pages(path)
    photo = {i for i, n in enumerate(extract.piece_pages(path, encoding, ask), 1)
             if n in with_photo}
    marks, names = fmt.plan(paras, run, log, photo, tries=len(who),
                            resume=resume, save=keep)
    log(T("took", f"{time.time() - t:.0f}",
          f"{getattr(agent, 'model', '?')}" + (f", ${cost[0]:.2f}" if cost[0] else "")))
    cuts = _check_toc(work, paras, marks, names, log)
    kinds = collections.Counter(marks.values())
    log("  " + T("marks_done", kinds.get("title", 0), kinds.get("skip", 0),
                 kinds.get("+", 0)))
    out = {str(k): v for k, v in marks.items() if isinstance(k, int)}
    out["_cuts"] = {str(k): v for k, v in cuts.items()}
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    if os.path.exists(half):
        os.unlink(half)
    marks["cuts"] = cuts
    note_source(work, formatter=getattr(agent, "model", None) or "?")
    return marks


def _load_marks(p):
    """Пометки и разрезы из файла. Разрезы лежат под отдельным ключом: без
    них заголовок, влитый в абзац, при пересборке потерялся бы."""
    d = json.load(open(p, encoding="utf-8"))
    cuts = d.pop("_cuts", None) or {}
    marks = {int(k): v for k, v in d.items()}
    marks["cuts"] = {int(k): v for k, v in cuts.items()}
    return marks


def _check_toc(work, paras, marks, names, log):
    """Сверка с оглавлением. Оно у книги одно, и обмануться ему негде: главу,
    которой там нет, придумала разметка, а названную и не найденную — потеряла.
    """
    from . import format as fmt
    r = fmt.reconcile(paras, marks, names)
    if not r["toc"]:
        return {}
    json.dump(r["names"], open(f"{work}/toc.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("  " + T("toc_found", r["toc"], r["toc"] - len(r["lost"]), r["added"]))
    if r["lost"]:
        log("  " + T("toc_lost", _few(r["lost"])))
    if r["dropped"]:
        log("  " + T("toc_dropped", _few(r["dropped"])))
    return r["cuts"]


def _few(names, n=3, cut=60):
    """Первые несколько названий, остальные числом.

    Название режем: за оглавление разметка порой принимает список книг
    автора, и тогда «запись» выходит в абзац длиной.
    """
    head = ", ".join(T("quoted", s[:cut] + ("…" if len(s) > cut else ""))
                     for s in names[:n])
    return head + (T("and_more", len(names) - n) if len(names) > n else "")


CODE_LINES = 300        # строк листинга в одном запросе


def _parse_code(out, allowed):
    """Записи <<<C номер строка>>> из ответа: {номер листинга: [(строка, ориг, пер)]}."""
    got = {}
    for m in re.finditer(r"<<<C\s+(\S+?)\s+(\d+)>>>\s*ORIG:\s*(.*?)\n\s*TR:\s*(.*?)(?=<<<|\Z)",
                         out, re.S):
        bid, no, orig, tr = m.groups()
        if bid in allowed:
            got.setdefault(bid, []).append((int(no), orig.strip(), tr.strip()))
    return got


def code_comments(work, blocks, agent, system, task, retries, log):
    """Перевод комментариев в листингах. Код остаётся байт в байт.

    Комментарии ищет модель — знаков комментария у языков сотни, разбором их
    не покрыть. Но подставляет перевод не она: программа берёт названный ею
    кусок, находит его в названной строке и меняет ровно его (см. code.py).
    Не сошлось — строка остаётся как была.

    Готовое лежит в `work/code.json`, поэтому повтор прогона переводит
    только новые листинги.
    """
    from . import code as C
    p = f"{work}/code.json"
    done = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    todo = [b for b in blocks if b["kind"] == "code" and b["id"] not in done]
    if not todo:
        if done:
            log("  " + T("code_known", len(done)))
        return done
    log("  " + T("code_start", len(todo)), end="")
    t, cost, n = time.time(), 0.0, 0
    for part in _by_lines(todo, CODE_LINES):
        ids = {b["id"] for b in part}
        body = "\n\n".join(
            f"<<<CODE {b['id']}>>>\n" +
            "\n".join(f"{k}| {l}" for k, l in enumerate(b["text"].split("\n"), 1))
            for b in part)
        try:
            (got, _), meta, _ = _run(agent, system, task + "\n\n---\n\n" + body,
                                     retries, lambda o: (_parse_code(o, ids), ""), log)
        except (Refused, RuntimeError):
            # Комментарии — не та потеря, ради которой стоит валить прогон:
            # непереведённый останется на языке оригинала, и это видно.
            log("")
            log("  " + T("code_failed", len(part)))
            continue
        cost += meta.get("cost_usd") or 0
        for b in part:
            text, k = C.splice(b["text"], got.get(b["id"], []))
            done[b["id"]] = text
            n += k
    _save(p, done)
    log(T("took", f"{time.time() - t:.0f}",
          f"{getattr(agent, 'model', '?')}" + (f", ${cost:.2f}" if cost else "")))
    log("  " + T("code_done", n, len(todo)))
    return done


def _by_lines(blocks, limit):
    """Листинги пачками: длинный уходит один, короткие — вместе."""
    out, cur, k = [], [], 0
    for b in blocks:
        m = b["text"].count("\n") + 1
        if cur and k + m > limit:
            out.append(cur)
            cur, k = [], 0
        cur.append(b)
        k += m
    return out + ([cur] if cur else [])


FIX_CHARS = 12000       # знаков книги в одном запросе корректору
FIX_MAX = 80            # длиннее — это уже не поправка, а переписывание
# Ниже сходства — подмена, а не починка. Граница узкая: «hitr«d iietion» →
# «Introduction» это 0.67, а «thou art» → «you are» (осовременивание автора,
# чинить которое не просили) — 0.62. Главный заслон тут промпт, а не число.
FIX_NEAR = 0.65


def fix_ok(old, new):
    """Можно ли принять поправку корректора.

    Порча распознавания — это перепутанная буква и разорванное слово, а не
    новая фраза. Поэтому замена принимается, только если она короткая, похожа
    на исходное и не выдумывает цифр: год `1935` восстановить нельзя, цифра
    распознаётся неверно так же легко, как верно.
    """
    if not old or not new or old == new or len(old) > FIX_MAX:
        return False
    if not re.search(r"[^\W\d_]", old):
        return False                       # чинить нечего: ни одной буквы
    if set(re.findall(r"\d", new)) - set(re.findall(r"\d", old)):
        return False                       # цифры не угадываем
    # Сравниваем по одним буквам: порча сидит как раз в пробелах и мусорных
    # знаках, и они занижают сходство там, где починка верна. «hitr«d iietion»
    # против «Introduction» — 0.54 как есть и 0.67 по буквам, а подмена слова
    # даёт ноль и так и так.
    bare = lambda s: re.sub(r"[\W\d_]", "", s, flags=re.U).lower()   # noqa: E731
    return difflib.SequenceMatcher(None, bare(old), bare(new)).ratio() >= FIX_NEAR


def _parse_fix(out, allowed):
    """Записи <<<F номер>>> из ответа: {номер: [(было, стало), ...]}."""
    got = {}
    for m in re.finditer(r"<<<F\s+(\S+?)>>>\s*ORIG:\s*(.*?)\n\s*FIX:\s*(.*?)(?=<<<|\Z)",
                         out, re.S):
        bid, old, new = m.group(1), m.group(2).strip(), m.group(3).strip()
        if bid in allowed:
            got.setdefault(bid, []).append((old, new))
    return got


def fix_ocr(work, blocks, agent, system, task, retries, log, fallback=None):
    """Правка порчи распознавания — в оригинале, до перевода.

    Иначе переводчик делает две работы разом: разбирает порчу и переводит.
    Разбирает молча и всякий раз по-своему, так что одно искажённое имя в
    разных кусках выходит по-разному; редактор поправить это не может, он
    оригинала не видит вовсе, а разведка успевает собрать справочник по
    испорченному.

    Модель называет замены, подставляет их программа — и только те, что
    сходятся дословно и проходят `fix_ok`. Переписать книгу она не может.
    """
    p = f"{work}/ocrfix.json"
    done = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    todo = [b for b in blocks if b["id"] not in done and not b.get("asis")
            and b["kind"] in ("p", "verse", "note", "table", "title") and b["text"].strip()]
    if not todo:
        if done:
            log("  " + T("fix_known", sum(len(v) for v in done.values())))
        return done
    log("  " + T("fix_start", len(todo)), end="")
    t0, cost, n, bad = time.time(), 0.0, 0, 0
    parts = _by_chars(todo, FIX_CHARS)
    for w, part in enumerate(parts, 1):
        log(f"{w}/{len(parts)} ", end="")
        ids = {b["id"] for b in part}
        body = "\n\n".join(f"<<<F {b['id']}>>>\n{strip(b['text'])}" for b in part)
        got = None
        for k, who in enumerate([agent] + _backups(fallback)):
            if k:
                log("")
                log("  " + T("refused_retry", getattr(who, "model", "?")), end="")
            try:
                (got, _), meta, _ = _run(who, system, task + "\n\n---\n\n" + body,
                                         retries, lambda o: (_parse_fix(o, ids), ""), log)
                break
            except (Refused, RuntimeError, Fatal):
                pass
        if got is None:
            log("")
            log("  " + T("fix_failed", len(part)))
            continue
        cost += meta.get("cost_usd") or 0
        for b in part:
            keep = [[o, x] for o, x in got.get(b["id"], []) if fix_ok(o, x) and o in b["text"]]
            bad += len(got.get(b["id"], [])) - len(keep)
            done[b["id"]] = keep
            n += len(keep)
        # Пишем после каждого окна: проход идёт десятками окон, и прерванный
        # на тридцатом не должен терять все тридцать.
        _save(p, done)
    log(T("took", f"{time.time() - t0:.0f}",
          f"{getattr(agent, 'model', '?')}" + (f", ${cost:.2f}" if cost else "")))
    log("  " + T("fix_done", n, len(todo)) + (T("fix_bad", bad) if bad else ""))
    note_source(work, fixer=getattr(agent, "model", None) or "?")
    return done


def _by_chars(blocks, limit):
    """Блоки пачками не длиннее предела."""
    out, cur, k = [], [], 0
    for b in blocks:
        if cur and k + len(b["text"]) > limit:
            out.append(cur)
            cur, k = [], 0
        cur.append(b)
        k += len(b["text"])
    return out + ([cur] if cur else [])


def apply_fixes(work, blocks, log=None):
    """Наложить поправки корректора на текст книги.

    `book.json` остаётся нетронутым: там оригинал как он есть, и всегда видно,
    что именно поправлено. Правки лежат отдельно и накладываются при чтении.
    """
    p = f"{work}/ocrfix.json"
    if not os.path.exists(p):
        return blocks
    fixes, n = json.load(open(p, encoding="utf-8")), 0
    for b in blocks:
        # Длинные первыми: короткая общая поправка иначе срабатывает раньше и
        # съедает место у точной. На живой книге «hack» → «back» опередила
        # «hack into HYPERSPACE» → «back into HYPERSPACE».
        for old, new in sorted(fixes.get(b["id"], []), key=lambda x: -len(x[0])):
            if old in b["text"]:
                b["text"] = b["text"].replace(old, new)
                n += 1
    if n and log:
        log("  " + T("fix_applied", n))
    return blocks


OCR_SAMPLE = 3000       # знаков текста, по которым видно, распознан ли он


def ocr_check(work, path, agent, log):
    """Распознан ли текстовый слой pdf. Возвращает, чем именно, или пустое.

    Сперва метаданные: программ распознавания много, но подписываются они
    одинаково, и десяток самых ходовых закрыт списком — это бесплатно.
    Подписи нет — спрашиваем модель, показав ей кусок текста: порчу она
    узнаёт с одного взгляда, а нам гадать по числам не вышло.

    Спрашиваем один раз на книгу: ответ ложится в `work/source.json`.
    """
    from . import extract
    made = extract.ocr_made(path)
    if made:
        return made
    if not path.lower().endswith(".pdf"):
        return ""
    p = f"{work}/source.json"
    was = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    if "ocr" in was:
        # «нет» вместо пустой строки: note_source пустых значений не хранит,
        # а различать «спрашивали, не распознан» и «не спрашивали» надо.
        return "" if was["ocr"] == "нет" else was["ocr"]
    txt = extract._pdf_text(path)
    lo = len(txt) // 3
    sample = txt[lo:lo + OCR_SAMPLE]
    ask = ("Перед тобой кусок текста из книги. Скажи, распознан ли он машиной "
           "с бумаги или набран начисто.\n\nПризнаки распознавания: перепутанные "
           "буквы («IIc» вместо «He»), слова, разорванные пробелом («Proj ect»), "
           "цифры вместо букв («SCIENT15T»), мусорные знаки внутри слов.\n\n"
           "Ответь первым словом РАСПОЗНАН или НАБРАН, дальше — до пяти примеров "
           "порчи, если она есть.\n\n---\n\n" + sample)
    try:
        out, _ = agent.run("", ask)
    except Exception:                                  # noqa: BLE001
        return ""
    made = "по тексту" if re.match(r"\W*РАСПОЗНАН", out.strip(), re.I) else ""
    note_source(work, ocr=made or "нет")
    if made:
        log("  " + T("ocr_seen", " ".join(out.split()[1:12])))
    return made


def note_source(work, **kw):
    """Чем читали и чем размечали книгу — для раздела в её конце."""
    p = f"{work}/source.json"
    was = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    was.update({k: v for k, v in kw.items() if v})
    json.dump(was, open(p, "w", encoding="utf-8"), ensure_ascii=False)


# ---------------------------------------------------------------- разведка

SCOUT_WORDS = 18000     # крупный блок: на вход много, на выход мало
SCOUT_BUDGET = 24000    # предел справочника в знаках: он идёт в КАЖДЫЙ запрос
# Выше этого справочник пересжимается отдельным запросом: просьбу уложиться
# в предел сведение исполняет плохо — на одной книге вышло полтора предела.
SCOUT_MAX = int(SCOUT_BUDGET * 1.25)


# Коды жанров fb2, которые конвейер принимает от разведки. Список нарочно
# короткий: он идёт в промпт, а тот и без того несёт большой справочник.
# Ответ вне списка отбрасывается — в fb2 это поле читают программы, и
# «фантастика» вместо `sf` делает его негодным.
GENRES = {
    "sf", "sf_space", "sf_cyberpunk", "sf_social", "sf_horror", "sf_fantasy",
    "sf_heroic", "sf_detective", "sf_action", "sf_history",
    "det_classic", "det_political", "det_action", "thriller",
    "love_contemporary", "love_history", "love_detective",
    "prose_history", "prose_contemporary", "prose_classic", "prose_rus_classic",
    "child_prose", "child_adv", "poetry", "nonfiction", "sci_popular",
    "adv_history", "adv_maritime", "adv_animal", "adv_western",
    "humor_prose", "humor_satire",
}


def scout_meta(work):
    """Выходные данные, вычитанные разведкой из самого текста.

    Нужны там, где формат их не хранит: у txt и pdf метаданных нет вовсе,
    и без этого в заголовок книги попадает имя файла. Берётся только как
    запасной вариант — порядок старшинства задан в booktrans.
    """
    p = f"{work}/scout.md"
    if not os.path.exists(p):
        return {}
    txt = open(p, encoding="utf-8").read()
    # Уровень заголовка не задан жёстко: модель ставит то «##», то «#», и
    # книга из-за одной решётки выходила с заглавием оригинала. Заодно
    # принимаем перевод названия раздела — по-английски и по-немецки.
    m = re.search(r"#{1,4}\s*(?:ВЫХОДНЫЕ ДАННЫЕ|IMPRINT|METADATA|IMPRESSUM)"
                  r"(.*?)(?=\n#{1,4}\s|\Z)", txt, re.S | re.I)
    if not m:
        return {}
    out = {}
    allowed = {"title", "author", "year", "publisher", "series", "series_no",
               "genre"}
    for line in m.group(1).splitlines():
        # Терпим к оформлению: модель любит завернуть строку в маркированный
        # список и выделить ключ полужирным. Сам ключ при этом остаётся
        # латинским, и по нему всё находится.
        mm = re.match(r"\s*[-*]?\s*\**\s*(\w+)\s*\**\s*[=:]\s*(.+?)\s*$", line)
        if not mm:
            continue
        key, v = mm.group(1), mm.group(2).strip().strip("`\"\'*")
        # Ключ один: title_target. Кода языка тут быть не должно — «tr»
        # читается и как «translated», и как турецкий, а при переводе на
        # турецкий это стало бы прямой путаницей.
        if key not in allowed | {"title_target", "author_target"}:
            continue
        if key == "genre":
            # Сверяем со словарём: выдуманный код хуже умолчания, потому что
            # выглядит как настоящий и никем не проверяется.
            v = v.strip().strip("`").lower()
            if v not in GENRES:
                continue
        if v and not v.startswith("("):
            out[key] = v
    return out


def scout(work, blocks, agent, system, task, retries, log, to='ru',
          src_name=None, fallback=None):
    """Крупноблочный проход ДО перевода.

    Собирает голоса персонажей, имена собственные и повторяющиеся термины.
    Дёшево: на вход идут десятки тысяч слов, на выходе — короткий разбор.
    Результат склеивается в work/scout.md и дальше уходит в системный промпт
    каждого запроса, так что решения по именам и интонациям принимаются
    один раз на всю книгу, а не заново в каждом куске.
    """
    out_path = f"{work}/scout.md"
    who = [agent] + _backups(fallback)
    if os.path.exists(out_path):
        log("  " + T("scout_done_already"))
        # Готовый справочник всё равно проверяем на двоящиеся термины: он мог
        # быть собран прежней версией, а платить за разведку заново незачем.
        merged = open(out_path, encoding="utf-8").read()
        merged = _condense_scout(merged, who, system, retries, log, out_path)
        forked = _forked(merged, to)
        if forked:
            merged = _unfork(merged, forked, who, system, retries, log, out_path)
        return merged

    paras = [b for b in blocks if b["kind"] in ("p", "title")]
    parts, cur, cw = [], [], 0
    for b in paras:
        cur.append(b)
        cw += words(b["text"])
        if cw >= SCOUT_WORDS:
            parts.append(cur)
            cur, cw = [], 0
    if cur:
        parts.append(cur)

    findings = []
    for i, part in enumerate(parts, 1):
        text = "\n\n".join(strip(b["text"]) for b in part)
        # Имя исходного файла — подсказка, а не истина. В библиотеках в него
        # кладут автора и заглавие, но порядок бывает любым: «Заглавие —
        # Автор», «Автор. Заглавие», иногда с номером из каталога. Разбирать
        # это правилами — значит путать автора с заглавием через раз, и
        # молча. Модель книгу читает и разберётся сама.
        hint = ""
        if src_name and i == 1:
            hint = (f"\n\n## Имя исходного файла\n\n`{src_name}`\n\n"
                    "В нём часто закодированы автор и заглавие, но порядок и "
                    "написание бывают любыми, а иногда там номер из каталога "
                    "или мусор. Бери это как подсказку: сведения из самого "
                    "текста книги всегда важнее. Если в тексте выходных "
                    "данных нет, а имя файла читается уверенно — используй его.")
        prompt = (f"{task}{hint}\n\n---\n\n## Часть {i} из {len(parts)}\n\n{text}")
        log("  " + T("scout_block", i, len(parts),
                     f"{sum(words(b['text']) for b in part):6d}"), end="")
        (res, _), meta, dt = _chain_run(who, system, prompt, retries,
                                        lambda o: (o, ""), log)
        findings.append(res)
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))

    if len(findings) > 1:
        log("  " + T("scout_merge"), end="")
        merge = (
            "Ниже — разборы разных частей одной книги, сделанные по отдельности. "
            "Сведи их в один справочник.\n\n"
            "Требования:\n"
            "- убрать повторы; если по одному имени или термину предложены разные "
            "варианты, выбрать один. В столбце перевода должен стоять "
            "**ровно один** вариант: ни косой черты, ни «или», ни скобок с "
            "запасным словом. Косая черта законна только там, где перечень "
            "есть и в оригинале: «duo / trio» → «дуэт / трио»;\n"
            f"- **уложиться примерно в {SCOUT_BUDGET} знаков**. Этот справочник "
            "уходит в каждый запрос на перевод, поэтому он должен быть плотным: "
            "таблицы вместо прозы, строка на сущность, никаких рассуждений и "
            "пересказа сюжета;\n"
            "- ничего не добавлять от себя. В сводном справочнике не должно "
            "появиться ни одного сведения, которого нет ни в одном из разборов: "
            "они сделаны по тексту книги, а твоя память — нет;\n"
            "- сохранить всё, что влияет на выбор слова: имена, род, склонение, "
            "свойства предметов, перемены, обращения, голоса рассказчиков, "
            "опасные места. Второстепенные подробности, не влияющие на перевод, "
            "выбросить;\n"
            "- формат разделов тот же, что в разборах.\n\n---\n\n"
            + "\n\n---\n\n".join(findings))
        (merged, _), meta, dt = _chain_run(who, system, merge, retries,
                                           lambda o: (o, ""), log)
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))
    else:
        merged = findings[0] if findings else ""

    open(out_path, "w", encoding="utf-8").write(merged)
    # Модель разведки нигде больше не записана, а в книге её надо назвать.
    json.dump({"model": meta.get("model")},
              open(f"{work}/scout.json", "w", encoding="utf-8"), ensure_ascii=False)
    log("  " + T("scout_ref", out_path, len(merged)))
    merged = _condense_scout(merged, who, system, retries, log, out_path)

    # Двоящиеся термины ловим до перевода, а не после. Запись вида
    # «одиночка / синглтон» — это не решение, и переводчик, работая кусками,
    # будет выбирать заново в каждом: у одного романа так вышло 45 раз одно
    # слово и 29 раз другое. Правится это одной строкой в справочнике — но
    # только если о ней знать.
    #
    # Ищем узко. Строка вида «duo / trio / quartet | дуэт / трио / квартет» —
    # это перечень, косая черта в нём законна. Раздвоение — когда в оригинале
    # термин один, а вариантов перевода несколько.
    forked = _forked(merged, to)
    if forked:
        merged = _unfork(merged, forked, who, system, retries, log, out_path)
        forked = _forked(merged, to)
    if forked:
        log("  " + T("scout_forked", len(forked)))
        for _, line in forked[:8]:
            log(f"      {line}")
    return merged


def _rows(text):
    """Строки таблиц справочника. По ним видно, не выпало ли при сжатии
    имя или термин: их строк должно стать не меньше."""
    return {re.sub(r"\s+", "", line.split("|")[1])
            for line in text.splitlines()
            if line.startswith("|") and line.count("|") > 2 and "---" not in line}


SECTION_MIN = 1500      # раздел мельче не сжимаем: возни больше, чем выгоды


def _sections(md):
    """Справочник по разделам. Первый кусок — шапка до первого заголовка."""
    out, cur = [], []
    for line in md.splitlines(keepends=True):
        if line.startswith("## ") and cur:
            out.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        out.append("".join(cur))
    return out


def _condense_scout(merged, who, system, retries, log, out_path):
    """Пересжать справочник, если он перерос предел.

    Просьба «уложиться в столько-то знаков» стоит в промпте сведения, но
    исполняется она плохо: на одной книге вышло полтора предела. А платится
    справочник в каждом запросе на перевод — надбавка постоянная. Отдельный
    запрос делает ровно одно дело и потому справляется.

    Сжимаем по разделам, а не файл целиком. Замерено на живой книге: одним
    запросом модель выбросила 128 строк таблиц из 183, охранник справедливо
    отверг весь результат — и справочник в полтора предела ушёл в каждый из
    девяноста четырёх запросов. По разделам этого «всё или ничего» нет:
    биографии ужимаются, а таблица имён, где резать нечего, остаётся как
    была. Начинаем с самого толстого раздела и останавливаемся, как только
    уложились.

    Порядок, что резать, знать заранее можно: он один для всех книг. Первым
    уходит очевидное — «Aldous Huxley = Олдос Хаксли» переводчик передаст
    верно и без подсказки, а место в каждом запросе занимает. Следом пересказ
    событий: сюжет конвейер и так помнит отдельным конспектом, который
    накапливается по ходу перевода.
    """
    if len(merged) <= SCOUT_MAX:
        return merged
    log("  " + T("scout_condense", len(merged), SCOUT_BUDGET), end="")
    parts = _sections(merged)
    t0, cost, model = time.time(), 0.0, "?"
    for i in sorted(range(len(parts)), key=lambda k: -len(parts[k])):
        over = sum(map(len, parts)) - SCOUT_BUDGET
        if over <= 0 or len(parts[i]) < SECTION_MIN:
            break
        # Сколько просить, зависит от природы раздела. Таблица имён короче
        # строк не станет, а строку из неё разрешено потерять только каждую
        # третью — больше трети с неё и не спрашиваем, иначе ответ придётся
        # отвергнуть. Проза ужимается и вдвое.
        floor = len(parts[i]) * 2 // 3 if _rows(parts[i]) else len(parts[i]) // 2
        short, meta = _shrink(parts[i], max(len(parts[i]) - over, floor),
                              agent, system, retries, log)
        cost += meta.get("cost_usd") or 0
        model = meta.get("model") or model
        if short:
            parts[i] = short
    now = "".join(parts)
    log(T("took", f"{time.time() - t0:.0f}",
          model + (f", ${cost:.2f}" if cost else "")))
    if now == merged:
        log("  " + T("scout_condense_no", len(_rows(merged)), len(_rows(merged))))
        log("  " + T("scout_big", out_path))
        return merged
    open(out_path, "w", encoding="utf-8").write(now)
    log("  " + T("scout_condense_ok", len(merged), len(now),
                 len(_rows(merged)), len(_rows(now))))
    if len(now) > SCOUT_MAX:
        log("  " + T("scout_big", out_path))
    return now


def _shrink(part, want, agent, system, retries, log):
    """Сжать один раздел справочника. Вернуть его же, если вышло плохо."""
    ask = (
        f"Это раздел справочника по книге. Справочник уходит в каждый запрос "
        f"на перевод, поэтому его укорачивают. В разделе {len(part)} знаков, "
        f"надо около {want}.\n\n"
        "Обязано остаться:\n"
        "- всё, чей перевод неочевиден: выдуманные слова, имена и названия, "
        "переданные на слух, и всё, что можно передать двумя способами. Ради "
        "этих строк справочник и существует;\n"
        "- таблица терминов книги;\n"
        "- род, склонение, обращения на «ты» и «вы»;\n"
        "- свойства существ, вещей и мест, от которых зависит выбор слова;\n"
        "- как говорит тот, у кого есть прямая речь;\n"
        "- опасные места, цитаты и канонические переводы.\n\n"
        "Резать в таком порядке:\n"
        "1. очевидное — то, что переводчик передаст верно и без подсказки: "
        "известные люди, страны и города, крупные учреждения, обиходные "
        "слова с единственным принятым соответствием. Исключение: если в "
        "целевом языке приняты два написания (Huxley — и Хаксли, и Гексли), "
        "строка остаётся. Справочник тут нужен не ради правильности, а ради "
        "единства по всей книге;\n"
        "2. пересказ событий и биографий — что происходит по ходу книги, "
        "конвейер помнит отдельно, здесь это лишнее;\n"
        "3. кандидаты в сноски — оставить слово и половину строки о том, "
        "чем оно трудно;\n"
        "4. пояснения длиннее строки — ужать до строки;\n"
        "5. людей без прямой речи — до одной строки.\n\n"
        "Заголовок раздела сохранить, порядок строк не менять. Ничего не "
        "дописывать: нового сведения появиться не должно. В ответе — только "
        "сам раздел, с первой же его строки: ни вступления, ни повторения "
        "этих указаний.\n\n---\n\n" + part)
    (short, _), meta, _dt = _chain_run(who, system, ask, retries,
                                       lambda o: (o, ""), log)

    # Всё, что модель приписала перед разделом, отрезаем по его заголовку.
    # Замерено: один прогон вернул справочник, а перед ним — весь системный
    # промпт целиком, девять тысяч знаков. Ни на длину, ни на число строк это
    # не влияет, и обе проверки ниже такое пропускают.
    head = next((l for l in part.splitlines() if l.startswith("#")), "")
    if head and head in short:
        short = short[short.index(head):]

    # Сжатие принимаем не на слово. Вычеркнуть таблицу целиком — самый простой
    # способ уложиться в предел, и обнаружилось бы это уже в переводе. Порог
    # широкий: очевидные строки выбрасываются по заданию, и придирчивая мера
    # запретила бы ровно то, ради чего сжатие затевалось.
    was, now = _rows(part), _rows(short)
    if len(short) >= len(part) or len(was - now) > len(was) / 3:
        return None, meta
    return short, meta


def _forked(merged, to):
    """Строки справочника, где на один термин оригинала дано несколько
    переводов. Возвращает пары (термин, строка целиком)."""
    from .lang import SCRIPTS, script_of
    rng = SCRIPTS.get(script_of(to) or "", "")
    if not rng:
        return []
    has_target = re.compile(rf"[{rng}]")
    sep = re.compile(r"\s/\s|\bили\b|\bor\b")
    out, inside = [], False
    for line in merged.splitlines():
        if line.startswith("#"):
            inside = bool(re.search(r"ИМЕНА|ТЕРМИН|NAMES|TERMS", line, re.I))
            continue
        if not inside or line.count("|") < 3 or "---" in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        orig, trans = cols[1], cols[2]
        if len(trans) > 70 or not has_target.search(trans):
            continue
        if len(sep.split(trans)) > len(sep.split(orig)):
            out.append((orig, re.sub(r"\s+", " ", line.strip())[:88]))
    return out


def _unfork(merged, forked, who, system, retries, log, out_path):
    """Выбрать по одному переводу за человека и переписать справочник.

    Раздвоенная запись — не решение, а отложенный выбор, и делать его будет
    переводчик, который видит один кусок книги вместо всей: у одного романа
    так вышло 45 раз одно слово и 29 раз другое. Спросить модель здесь дёшево
    (десяток строк на входе), а решение это разовое и на всю книгу.
    """
    log("  " + T("scout_unfork", len(forked)), end="")
    ask = ("В справочнике по книге на один термин оригинала дано несколько "
           "переводов. Выбери по одному — тот, что лучше ляжет в текст книги "
           "и привычнее читателю на целевом языке.\n\n"
           "Ответ — только строками вида `термин = выбранный перевод`, "
           "по одной на каждый случай, без пояснений.\n\n"
           + "\n".join(line for _, line in forked))
    (res, _), meta, dt = _chain_run(who, system, ask, retries,
                                    lambda o: (o, ""), log)
    cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
    log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))

    picks = {}
    for line in res.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().strip("`*|-— ")
        v = v.strip().strip("`*| ")
        if k and v:
            picks[k.lower()] = v
    if not picks:
        return merged

    want = {orig.lower() for orig, _ in forked}
    out, done = [], []
    for line in merged.splitlines():
        cols = line.split("|")
        if len(cols) > 3:
            orig = cols[1].strip().lower()
            if orig in want and orig in picks:
                cols[2] = f" {picks[orig]} "
                line = "|".join(cols)
                done.append(f"{cols[1].strip()} → {picks[orig]}")
        out.append(line)
    if not done:
        return merged
    # Кроме таблицы, тот же термин поминается в справочнике прозой, и там он
    # остаётся двойным. Переписывать прозу подстановкой опасно, поэтому выбор
    # закрепляем отдельным разделом в конце: он короткий и старше остального.
    out.append("")
    out.append("## ОКОНЧАТЕЛЬНЫЙ ВЫБОР ПО ТЕРМИНАМ")
    out.append("")
    out.append("Ниже — термины, у которых выше по справочнику осталось "
               "несколько вариантов перевода. Пишутся только так; всё, что "
               "сказано о них выше, этому подчиняется.")
    out.append("")
    out += [f"- {x}" for x in done]
    merged = "\n".join(out)
    open(out_path, "w", encoding="utf-8").write(merged)
    for x in done:
        log(f"      {x}")
    return merged


# ---------------------------------------------------------------- заголовки

def headings(work, blocks, agent, system, retries, log):
    """Заголовки и подзаголовки — одним запросом на всю книгу.

    Их немного (десятки), и они обязаны совпадать дословно между собой:
    десять вхождений одного имени рассказчика не должны выглядеть по-разному.
    Отдельно от прозы ещё и потому, что короткая строка в начале запроса
    принимается моделью за шапку промпта и молча теряется.
    """
    path = f"{work}/headings.json"
    have = {}
    if os.path.exists(path):
        have = {k: v for k, v in json.load(open(path, encoding="utf-8")).items()
                if not k.startswith("_")}
    uniq = []
    for b in blocks:
        if b["kind"] in HEAD_KINDS and b["text"] not in have and b["text"] not in uniq:
            uniq.append(b["text"])
    if not uniq:
        return have
    log("  " + T("heads_todo", len(uniq)), end="")
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(uniq, 1))
    prompt = (
        "Переведи заголовки и подзаголовки книги. Это номера глав, названия "
        "разделов, имена рассказчиков в шапках, строки места и времени.\n\n"
        "Правила: держать регистр оригинала (написанное прописными остаётся "
        "прописными); имена и термины брать из справочника выше; номера глав "
        "переводить как принято по-русски.\n\n"
        "Верни строго по строке на каждый пункт, в том же порядке и с тем же "
        "номером, без пояснений:\n1. перевод\n2. перевод\n\n"
        "## Заголовки\n\n" + listing)

    def parse_heads(out):
        got = {}
        for m in re.finditer(r"^\s*(\d+)[.)]\s*(.+)$", out, re.M):
            i = int(m.group(1))
            if 1 <= i <= len(uniq):
                got[uniq[i - 1]] = m.group(2).strip()
        missing = [t for t in uniq if t not in got]
        if missing:
            # Обрезаем: заголовок бывает и в тысячи знаков, если разметка
            # приняла за него абзац, и такой список смывает весь вывод.
            short = [re.sub(r"\s+", " ", t)[:70] + ("…" if len(t) > 70 else "")
                     for t in missing[:3]]
            raise ValueError(f"не переведено {len(missing)} заголовков: {short}")
        return got, ""

    (got, _), meta, dt = _run(agent, system, prompt, retries, parse_heads, log)
    have.update(got)
    json.dump(have, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
    log(T("ready_in", f"{dt:.0f}", f"{meta['model']}{cost}"))
    return have


# ---------------------------------------------------------------- структура

def detect_structure(work, styles, agent, task, retries, log):
    """Определение вёрстки моделью: какой стиль чем является.

    Эвристика по именам классов всегда угадывает — у каждого издательства
    разметка своя, и заголовок бывает и <h1>, и <p class="CN">, и
    <p class="Chap-Title-ct">. Модель смотрит на перепись стилей (десяток
    строк с примерами, не книгу) и решает. Результат лежит в
    work/structure.json и правится руками.
    """
    path = f"{work}/structure.json"
    if os.path.exists(path):
        got = {k: v for k, v in json.load(open(path, encoding="utf-8")).items()
               if not k.startswith("_")}
        log("  " + T("struct_known", len(got)) + " " + T("delete_to_redo", path))
        return got
    if not styles:
        return {}

    listing = []
    for r in styles:
        ex = " / ".join(s.replace("\n", " ")[:70] for s in r["samples"])
        listing.append(f'{r["tag"]}|{r["cls"]}  ×{r["count"]}  → {ex}')
    prompt = task + "\n\n---\n\n## Перепись стилей\n\n" + "\n".join(listing)
    open(f"{work}/prompts/structure.txt", "w", encoding="utf-8").write(prompt)

    log("  " + T("struct_styles", len(styles)), end="")

    def parse_map(out):
        got = {}
        for m in re.finditer(r"^\s*([a-z0-9]+)\|([^=\n]*?)\s*="
                             r"\s*(title|subtitle|p|verse|note|skip)\s*$",
                             out, re.M | re.I):
            got[f"{m.group(1)}|{m.group(2).strip()}"] = m.group(3).lower()
        if not got:
            raise ValueError("не разобрал ни одной строки вида тег|класс = вид")
        return got, ""

    (got, _), meta, dt = _run(agent, "", prompt, retries, parse_map, log)

    known = {f'{r["tag"]}|{r["cls"]}' for r in styles}
    got = {k: v for k, v in got.items() if k in known}
    counts = {}
    for r in styles:
        counts[got.get(f'{r["tag"]}|{r["cls"]}', "?")] = \
            counts.get(got.get(f'{r["tag"]}|{r["cls"]}', "?"), 0) + r["count"]

    out = {"_comment": "Разметка книги: тег|класс = title|subtitle|p|verse|note|skip. "
                       "Определена моделью, правится руками. Удалите файл, "
                       "чтобы определить заново."}
    out.update(got)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
    log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))
    log("  " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())
                         if k != "?"))
    return got
