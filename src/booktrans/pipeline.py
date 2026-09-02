"""Нарезка, перевод, редактура. Все проходы возобновляемые."""
import collections
import difflib
import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.parse

from . import agent as agent_mod
from .agent import AgentError, Blocked, Fatal, RateLimited

# Прерывание с клавиатуры. При работе в несколько потоков одного Ctrl+C мало:
# рабочие потоки не видят исключения главного, продолжают запросы, и человек
# жмёт снова и снова, а в конце получает трассировку из недр threading.
# Флаг решает это просто: потоки сами останавливаются на ближайшей проверке.
STOP = threading.Event()
from .lang import T
from . import lang
from .tune import (CODE_LINES, DIGEST_BUDGET, DIGEST_EVERY, DIGEST_MIN,
                   FAIL_PAUSE, FIX_CHARS, FIX_MAX, FIX_NEAR, LOOKAHEAD_WORDS,
                   HUSH_MAX, HUSH_PAUSE,
                   MAX_BLOCKS, MAX_VERSE, MAX_WORDS, MERGE_INPUT, OCR_SAMPLE,
                   REFUSE_ROW,
                   RETRY_PAUSE, SCOUT_BUDGET, SCOUT_CANON_BUDGET, SCOUT_HEADS,
                   SCOUT_ROUNDS,
                   SCOUT_WORDS, SHIFT_BAD, SHIFT_GAP, SHIFT_MIN, SHIFT_WIN,
                   STUB_MIN, STUB_SHARE,
                   TAIL_PARAS, TARGET_WORDS, TERMS_BUDGET, TERMS_TAIL,
                   TWIN_LEN, TWIN_NEAR,
                   VERSE_GROUP, HEAD_CHUNK)

HEAD_KINDS = ("title", "subtitle")


def words(s):
    return len(strip(s, " ").split())


# Тег, а не знак «меньше». В тексте попадается настоящее сравнение —
# «keeping homocysteine under <13 μmol/L», — и жадное `<[^>]+>` съедало от
# него всё до ближайшего `>`, то есть до начала следующего тега: полторы
# фразы с числами. Тег всегда начинается с буквы или косой черты.
TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def strip(s, sep=""):
    return TAG.sub(sep, s)


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


# Чем модель закрывает последний кусок ответа: своей оградкой, тегом
# внутренней разметки (`</invoke>`, `</summary>`) или закрывающей скобкой к
# нашей же метке — прежде `</<<NOTES>>>`, теперь `[[[/NOTES]]]`. Смысла в них
# нет, а в файл замечаний они попадали как есть — в каждом четвёртом куске.
SCAFFOLD = re.compile(r"```\w*|</(?:<<)?[^\s<>]{1,24}(?:>>)?>"
                      r"|\[\[\[/?[A-Z]{1,12}(?:\s+\S+)?\]\]\]")


def _unscaffold(s):
    """Снять строительные леса с хвоста ответа. Только строки, где кроме них
    ничего нет: `</a1>` в конце замечания — это ярлык ссылки, а не леса."""
    lines = s.strip().split("\n")
    while lines and SCAFFOLD.fullmatch(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def parse_blocks(out, expected=None, allowed=None, extra_tag=None):
    tail = ""
    if extra_tag:
        m = re.search(rf"\[\[\[{extra_tag}\]\]\]\s*(.*)$", out, re.S)
        if m:
            tail = _unscaffold(m.group(1))
        out = re.split(rf"\[\[\[{extra_tag}\]\]\]", out)[0]
    got = {}
    for m in re.finditer(r"\[\[\[[PVT]\s+(\S+?)\]\]\]\s*(.*?)(?=\[\[\[[PVT]\s+\S+?\]\]\]|$)", out, re.S):
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
    """Блоки [[[NOTE id вид]]] из ответа. Общий разбор для сносок и редактуры."""
    items = []
    for m in re.finditer(r"\[\[\[NOTE\s+(\S+?)\s+(reference|fact|term|source)\]\]\]\s*"
                         r"TERM:\s*(.*?)\n\s*TEXT:\s*(.*?)(?=\[\[\[|\Z)", out, re.S):
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
    stops, last, err = [], None, None
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
            cur = prompt + "\n\n---\n\n" + \
                lang.prompt("retry_reject")[0].format(err=e)
        except (Fatal, RateLimited, Blocked):
            # Повторять нечего. У Fatal беда не в тексте, а лимит — вообще не
            # осечка запроса, а состояние времени: до срока модель ответит
            # тем же самым. Blocked — фильтр на входе шлюза: тот же промпт
            # бьётся о тот же фильтр слово в слово, пять попыток печатали
            # одно и то же.
            raise
        except ValueError as e:
            # Разбор отверг ответ: без конверта, без вердикта. Слепой повтор
            # того же промпта бился о то же место пять раз подряд — модель
            # не знала, что чинить. Причина отказа едет в повтор.
            log("\n    " + T("retry", attempt, e))
            cur = prompt + "\n\n---\n\n" + \
                lang.prompt("retry_parse")[0].format(err=e)
            err = e
        except Exception as e:
            log("\n    " + T("retry", attempt, e))
            # Сбой на стороне поставщика — переждать. «Please try again in a
            # minute» пять раз подряд за одну секунду это не пять попыток, а
            # одна: сервер за это время не разгрузился. На разборе ответа
            # пауза, наоборот, лишняя — там дело не в сервере, а в тексте.
            if isinstance(e, AgentError) and attempt < retries:
                time.sleep(min(RETRY_PAUSE * attempt, RETRY_PAUSE * 4))
            err = e
            cur = prompt + "\n\n---\n\n" + \
                lang.prompt("retry_reject")[0].format(err=e)
    if last is not None:
        # Кусок обрывался до последней попытки — это отказ, а не сбой связи.
        # Через Refused его подхватит запасная модель; RuntimeError валил
        # весь прогон, и подстраховка, ради которой её и задают, не звалась.
        raise Refused(last.first, last.n, last.total) from None
    # Ни одна попытка не дошла до ответа, и по сообщению непонятно, в чём
    # дело. Спрашиваем саму модель, жива ли она: молчание в ответ на «скажи
    # ok» значит одно и то же, какими бы словами поставщик ни объяснял
    # запрет, — и тогда это не отказ, а ожидание.
    if isinstance(err, AgentError) and not agent_mod.alive(agent):
        log("    " + T("probe_dead", getattr(agent, "model", "?")))
        raise RateLimited(str(err))
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


def boxed(prompt, name, what):
    """Требование обернуть работу маркерами — в конец запроса.

    Проходы со свободным ответом — разведка, сведение, конспект — принимали
    любой текст: сверять там нечего, ответ просто проза. Ошибались они молча
    и одинаково: вместо работы приходила записка о файле, отчёт о сделанном,
    вызов инструмента, а однажды — весь системный промпт целиком.

    Конверт разбирает все эти случаи разом: работа лежит между маркерами,
    остальное отброшено, ответа без маркеров нет вовсе.

    Требование стоит дважды: строкой в начале и полностью в конце. На
    больших запросах разведки модель, прочитав тысячи слов текста, писала
    отчёт сразу, без конверта, — одиночное требование в хвосте тонуло.
    """
    head, _ = lang.prompt("envelope_head")
    tpl, _ = lang.prompt("envelope")
    return head.format(name=name) + "\n\n" + prompt \
        + "\n\n---\n\n" + tpl.format(name=name, what=what)


def unbox(out, name):
    """Работа из конверта.

    Число в маркере подставляет модель, а в промпте на его месте буква `N`.
    Оттого промпт, вернувшийся эхом, за ответ не сойдёт: цифр в нём нет.
    Заодно видно и обрыв — начало пришло, конца нет.

    Какое именно число подставлено, не сверяется. Живая модель на первой
    части написала «4», и повтор всё исправил, но стоил целого запроса —
    полторы минуты и двадцать центов, а на большом куске впятеро дороже.
    От эха бережёт сам факт подстановки, а не совпадение; закрывающий маркер
    обязан нести то же число, что открывающий.
    """
    o = rf"\[\[\[\s*{name}\s+(\d+)\s*\]\]\]"
    m = re.search(o + r"(.*?)" + rf"\[\[\[\s*/\s*{name}\s+\1\s*\]\]\]",
                  out, re.S | re.I)
    if m:
        return m.group(2).strip()
    if re.search(o, out, re.I):
        raise ValueError(f"ответ оборван: маркера конца {name} нет")
    raise ValueError(f"ответ без маркеров {name}: {out.strip()[:120]!r}")


# Служебный вывод агента: попытка позвать инструмент, размышление вслух,
# ответ оболочки. В конспект такое попадать не должно ни при каких условиях.
TOOLCALL = re.compile(
    r"<invoke\s+name=|</?function_calls>|<parameter\s+name=|"
    r"<tool_use|antml:|File does not exist", re.I)


def _parse_digest(out):
    """Конспект ли это.

    На живой книге агент вернул вместо конспекта собственное размышление с
    вызовом инструмента — «сейчас загляну в файл… File does not exist», — и
    эти триста знаков ушли в каждый запрос на перевод начиная с
    семьдесят-третьего куска. Конвейер принял их молча: конспект нигде не
    сверяется, он просто текст. Заметил редактор, и то потому, что читал.
    """
    out = unbox(out, "DIGEST")
    m = TOOLCALL.search(out)
    if m:
        raise ValueError(f"не конспект, а служебный вывод агента: {m.group()!r}")
    if len(out) < DIGEST_MIN:
        raise ValueError(f"конспект короче {DIGEST_MIN} знаков: {out[:80]!r}")
    return out, ""


FILE_URL = re.compile(r"file://(\S+?)(?=[)\]\s]|$)")


def _parse_scout(out):
    """Справочник ли это.

    Через agy думающая модель на большом куске делала работу, складывала её
    в свой файл и отвечала запиской «справочник готов: [файл](file://…)» —
    тысяча знаков вместо двадцати пяти тысяч. Разведка принимала любой текст
    и писала эту записку в справочник, а он уходит в каждый запрос на
    перевод: полкниги переводилось бы без имён и терминов.

    Работа при этом сделана и оплачена, а путь назван — если файл на месте,
    забираем его. Нет — ошибка, и кусок уходит следующей модели.

    Служебный вывод отдельно тут не ищется, как в конспекте: за маркерами он
    и так остаётся снаружи, а книга про инъекции приводит такие строки
    примером на каждой странице.
    """
    try:
        return unbox(out, "SCOUT"), ""
    except ValueError:
        for u in FILE_URL.findall(out):
            p = urllib.parse.unquote(u)
            if os.path.isfile(p):
                got = open(p, encoding="utf-8", errors="replace").read().strip()
                if _heads(got) >= SCOUT_HEADS:
                    return got, ""
        # Справочник без конверта не принимается, даже когда похож на
        # работу: закрывающий маркер — единственное доказательство, что
        # ответ дошёл до конца, а транспорт режет молча. Обрубок с парой
        # разделов сошёл бы за целый справочник. Такой кусок идёт запасной
        # модели цепочки.
        raise


def _heads(s):
    """Сколько в тексте разделов «## …». Мера для файла, который модель
    написала вместо ответа: маркеров в нём нет и быть не может."""
    return len(re.findall(r"(?m)^#{1,4}\s*\S", s))


def condense(state, upto, agent, retries, log, fallback=None):
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

    prompt_tpl, _ = lang.prompt("digest")
    prompt = boxed(prompt_tpl.format(budget=DIGEST_BUDGET, upto=upto,
                                     digest=digest, fresh="\n\n".join(fresh)),
                   "DIGEST", lang.prompt("box_digest")[0])
    log("  " + T("digest_go"), end="")
    try:
        # Система — пристёгивающая строка, не пустота: агентная обёртка без
        # системы зовёт инструмент, headless его отклоняет — и приходит
        # SUCCESS с пустым текстом, по три попытки на каждое сжатие.
        (new, _), meta, dt = _chain_run([agent] + _backups(fallback),
                                        _text_only(), prompt,
                                        retries,
                                        _parse_digest, log)
    except (Refused, RuntimeError, Fatal) as e:
        # Прежний конспект целее негодного нового: он уходит в каждый запрос
        # на перевод и ошибётся не однажды, а на всём остатке книги.
        log("")
        log("    " + T("digest_kept", e))
        return (digest + "\n\n" + "\n\n".join(fresh)).strip()
    state["digest"] = new
    state["digest_upto"] = upto - 1
    cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
    log(T("digest_done", len(new), f"{dt:.0f}", f"{meta['model']}{cost}"))
    return new


def verse_canon(state, blocks):
    """Канонические переводы стихотворных строк этого куска.

    Стихи повторяются: рефрены, песни, поэма, которую герой начинает в одной
    главе и дочитывает через сто страниц. Один и тот же оригинал обязан
    переводиться одинаково, а переводчик работает кусками и прошлых решений
    не видит. Реестр в `state.json` («verse»: отпечаток строки → перевод)
    помнит принятое; сюда попадают ТОЛЬКО блоки вида verse — та же строка,
    случайно встретившаяся в прозе, механику не трогает.
    """
    known = state.get("verse") or {}
    return {b["id"]: known[fingerprint(b["text"])]
            for b in blocks
            if b["kind"] == "verse" and fingerprint(b["text"]) in known}


def verse_learn(state, blocks, res):
    """Пополнить реестр переводами стихотворных строк куска."""
    known = state.setdefault("verse", {})
    for b in blocks:
        if b["kind"] == "verse" and b["id"] in res:
            known.setdefault(fingerprint(b["text"]), res[b["id"]])


# Разделы, где карточки «- **Имя (Name):** …» ключуются и едут с куском по
# упоминанию, как строки таблиц. Остальные разделы — костяк: он нужен
# каждому куску и живёт в системном промпте.
REF_KEYED = ("CHARACTERS", "NAMES", "TERMS", "GENDER", "ADDRESS", "WORLD",
             "FOOTNOTES")
_BULLET_KEY = re.compile(r"\s*[-*]\s+\*\*(.+?):?\*\*(?=[\s:—–-])")


def _unbracket(inner):
    """Скобочная альтернатива — в составной ключ: «Имя (Name)» → «Имя / Name»,
    чтобы отбор по куску ловил оба написания."""
    names = [re.sub(r"\s+", " ", re.sub(r"\([^()]*\)", " ", inner))
             .strip(" \t:—–-")]
    names += [p.strip() for p in re.findall(r"\(([^()]+)\)", inner)]
    return " / ".join(dict.fromkeys(n for n in names if n))


# Слова шапок таблиц: модель воспроизводит шапку из образца в промпте, и
# без фильтра та въезжает в реестр строкой.
_HEADER_KEYS = {"оригинал", "original", "имя", "name", "имя в оригинале",
                "персонаж", "character", "термин", "term", "слово", "word",
                "ключ", "key"}


def _line_key(line, sec=None):
    """Ключ строки реестра: первая ячейка таблицы или полужирное начало
    карточки «- **Имя (Name):** …». `sec` ограничивает карточки разделами
    из REF_KEYED; None — без ограничения."""
    t = line.strip()
    key = ""
    if t.startswith("|") and t.count("|") > 2:
        key = re.sub(r"[\s*_`~]+", " ", t.split("|")[1]).strip()
        if not key or set(key) <= set("- :"):
            return ""
        key = _unbracket(key) if "(" in key else key
    elif sec is None or sec in REF_KEYED:
        m = _BULLET_KEY.match(line)
        if m:
            key = _unbracket(m.group(1).strip())
    if key and all(p.strip().lower() in _HEADER_KEYS
                   for p in re.split(r"\s*/\s*", key)):
        return ""
    return key


def _norm_key(key):
    """Нормальный вид ключа реестра: регистр, выделение и артикль строку не
    рознят, составной ключ не зависит от порядка половин."""
    parts = []
    for p in re.split(r"\s*[/,;]\s*", key):
        p = re.sub(r"[\s*_`~]+", " ", p).strip().lower()
        p = re.sub(r"^(?:the|a|an)\s+", "", p)
        if p:
            parts.append(p)
    return " / ".join(sorted(parts))


def _ref_scan(text):
    """Разметка справочника построчно: (раздел, заголовок, ключ, строка, род).

    Род: `head` — заголовок, `row` — строка реестра, `junk` — шапка или
    линейка таблицы, `frame` — проза костяка. Раздел — латинский ключ
    ближайшего заголовка («CHARACTERS», «NAMES»…); заголовок того же или
    старшего уровня раздел закрывает, подразделы остаются внутри.
    """
    out, sec, depth, head = [], "", 0, ""
    for line in text.splitlines():
        t = line.strip()
        m = re.match(r"(#{1,4})\s*(.+)", t)
        if m:
            level = len(m.group(1))
            if sec and level <= depth:
                sec = ""
            km = re.match(r"([A-Z]{2,})\b", m.group(2))
            if km:
                sec, depth = km.group(1), level
            head = line
            out.append((sec, head, "", line, "head"))
            continue
        if t.startswith("|") and t.count("|") > 2:
            key = _line_key(line)
            out.append((sec, head, key, line, "row" if key else "junk"))
            continue
        key = _line_key(line, sec)
        out.append((sec, head, key, line, "row" if key else "frame"))
    return out


def split_ref(text):
    """Справочник надвое: костяк и строки реестра.

    Костяк — проза: повествование, опасные места, стихи. Он неизменен на
    всю книгу и уходит в системный промпт, который транспорт кеширует по
    совпадающему началу. Реестр — таблицы и карточки, а куску из них нужны
    считаные: те, чьи ключи в нём встречаются. Они уезжают из системы и
    приезжают с куском — см. ref_rows_for. Заголовок, под которым не
    осталось прозы, уходит вместе со своими строками: пустая шапка в
    системе только путает.
    """
    recs = _ref_scan(text)
    rows = [(k, l) for _, _, k, l, kind in recs if kind == "row"]
    live, cur, has = set(), None, False
    for i, (_, _, _, line, kind) in enumerate(recs):
        if kind == "head":
            if cur is not None and has:
                live.add(cur)
            cur, has = i, False
        elif kind == "frame" and line.strip():
            has = True
    if cur is not None and has:
        live.add(cur)
    frame = "\n".join(line for i, (_, _, _, line, kind) in enumerate(recs)
                      if kind == "frame" or (kind == "head" and i in live))
    return re.sub(r"\n{3,}", "\n\n", frame), rows


def ref_rows_for(rows, text, budget=0):
    """Строки справочника, чьи ключи встречаются в этом тексте.

    Ключ — первая ячейка; составной («duo / trio») ловится любой частью.
    Совпадение — полной фразой в границах слова, как у сведения имён цикла,
    но без оглядки на регистр: пропущенная строка стоит разнобоя в имени,
    лишняя — сотни знаков.

    `budget` — предел в знаках. У сиквела к середине книги совпадает
    полтысячи строк на сотню тысяч знаков, и запрос перерастает
    переносимость шлюза: модель молча возвращает пустоту. Первыми выживают
    строки, чаще упомянутые в тексте; порядок уцелевших — прежний.
    """
    got = []
    for key, line in rows:
        hits = 0
        for part in re.split(r"\s*[/,;]\s*", key):
            if len(part) > 1:
                hits += len(re.findall(
                    rf"(?<![^\W\d_]){re.escape(part)}(?![^\W\d_])", text, re.I))
        if hits:
            got.append((hits, line))
    if budget and sum(len(l) + 1 for _, l in got) > budget:
        keep, size = set(), 0
        for i in sorted(range(len(got)), key=lambda j: -got[j][0]):
            need = len(got[i][1]) + 1
            # Не break: после длинной строки короткая ещё может влезть.
            if size + need <= budget:
                keep.add(i)
                size += need
        got = [g for i, g in enumerate(got) if i in keep]
    return [l for _, l in got]


def refresh(work, log, to=""):
    """Пересчитать отпечатки готовности после ручной правки переводов.

    Точечная замена в tr (имя, термин), внесённая синхронно в пары правок,
    сделанности работы не меняет — но отпечатки расходятся, и редактура со
    сверкой честно перечитали бы всю книгу. Блок, чья правка по-прежнему
    чисто накладывается (или правки не было), признаётся сделанным; блок с
    осиротевшей правкой остаётся честно несделанным. Сверка меряется по
    тексту с наложенными правками, редактура — по черновику.
    """
    tr = {}
    for _, p in chunk_files(lpath(work, "tr", to)):
        tr.update(json.load(open(p, encoding="utf-8")).get("tr", {}))
    if not tr:
        log("  " + T("refresh_none"))
        return
    stale = orphan = 0
    for _, p in chunk_files(lpath(work, "ed", to)):
        d = json.load(open(p, encoding="utf-8"))
        src, edits = d.get("src") or {}, d.get("edits") or {}
        ch = False
        for k, h in list(src.items()):
            if k not in tr:
                continue
            now = fingerprint(tr[k])
            if h == now:
                continue
            e = edits.get(k)
            if e is None or e.get("old") == tr[k]:
                src[k] = now
                ch = True
                stale += 1
            else:
                orphan += 1
        if ch:
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    log("  " + T("refresh_done", "ed", stale, orphan))
    cur = dict(tr)
    for _, p in chunk_files(lpath(work, "ed", to)):
        for k, e in (json.load(open(p, encoding="utf-8")).get("edits") or {}).items():
            if k in cur and cur[k] == e.get("old", cur[k]):
                cur[k] = e["new"]
    stale = orphan = 0
    for _, p in chunk_files(lpath(work, "vf", to)):
        d = json.load(open(p, encoding="utf-8"))
        src, edits = d.get("src") or {}, d.get("edits") or {}
        ch = False
        for k, h in list(src.items()):
            if k not in cur:
                continue
            e = edits.get(k)
            if e is not None and e.get("old") != cur[k]:
                orphan += 1
                continue
            target = fingerprint(e["new"] if e else cur[k])
            if h != target:
                src[k] = target
                ch = True
                stale += 1
        if ch:
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    log("  " + T("refresh_done", "vf", stale, orphan))


def _chunk_head(index, label):
    """Первая строка запроса куска. Раздел упоминается, только когда он есть:
    файл-вариант вместо склейки строк, чтобы шов не жил в коде."""
    name = "chunk_head_label" if label else "chunk_head"
    return lang.prompt(name)[0].format(index=index, label=label)


def translate_prompt(chunk, nxt, summary, tail, terms, refs, task):
    # стихи помечаем отдельно: иначе модель их не отличит и переведёт прозой,
    # а редактор потом «выправит» ритм окончательно
    src = "\n".join(f"[[[{MARK.get(b['kind'], 'P')} {b['id']}]]]\n{b['text']}"
                    for b in translatable(chunk["blocks"]))
    # Задание — первым: оно неизменно на всю книгу, а поставщики кешируют
    # запрос по дословно совпадающему началу. Переменное — дальше.
    parts = [task, _chunk_head(chunk["index"], chunk["label"])]
    if summary:
        parts.append(lang.prompt("translate_prev")[0] + "\n\n" + summary)
    if tail:
        parts.append(lang.prompt("translate_tail")[0] + "\n\n" + tail)
    if terms:
        parts.append(lang.prompt("translate_terms")[0] + "\n\n" + "\n".join(terms))
    if refs:
        parts.append(lang.prompt("ref_rows")[0] + "\n\n" + "\n".join(refs))
    parts.append(lang.prompt("translate_fragment")[0] + "\n\n" + src)
    if nxt:
        ahead = " ".join(strip(b["text"]) for b in translatable(nxt["blocks"])[:4])
        ahead = " ".join(ahead.split()[:LOOKAHEAD_WORDS])
        if ahead:
            parts.append(lang.prompt("translate_ahead")[0] + "\n\n" + ahead)
    # Последней строкой — предупреждение об отвержении. Замерено на живой
    # отказной сцене: голый промпт Gemini рвал в 2 случаях из 3, с вестью об
    # отвергнутой попытке проходил 3 из 3, с этим честным предупреждением —
    # 2 из 3. Действующее вещество — весть об отвержении; повтор с настоящим
    # «прошлая попытка отвергнута» остаётся второй линией.
    parts.append(lang.prompt("translate_finish")[0])
    return "\n\n---\n\n".join(parts)




# Что в файле куска хранится по номерам блоков: при слиянии эти словари
# дополняются, а не заменяются целиком.
# ------------------------------------------------- языковые части папки

def lpath(work, name, to=""):
    """Путь к языковой части рабочей папки: `tr` → `work/ru/tr`.

    Одну книгу переводят на несколько языков, и делить между ними разбор
    оригинала, разметку и картинки — вся выгода: они стоят дороже всего после
    самого перевода. А перевод, редактура, сноски, справочник и конспект у
    каждого языка свои и лежат в его папке.

    Папка, а не суффикс в имени: язык удаляется одним движением, общий список
    файлов не растёт с каждым новым языком, а главное — работая с одним
    языком, в чужой не попадёшь. При плоской раскладке `scout_de.md` стоял бы
    вплотную к `scout_ru.md`, и правка ушла бы не туда молча.

    DEPRECATED: своего пути ещё нет, а рядом с общими файлами такое имя есть —
    берём его. Так читаются папки выпусков до 1.8.99, и переводить в них
    заново ничего не нужно. Когда таких папок в обиходе не останется, убрать
    `old` и обе строки под этим словом.
    """
    new = os.path.join(work, to, name) if to else ""
    old = os.path.join(work, name)
    if new and not os.path.exists(new) and os.path.exists(old):
        return old
    return new or old


def mkparent(path):
    """Завести папку под этот файл. Языковая папка появляется при первой
    записи в неё, и заводить её отдельной строкой в каждом проходе незачем."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    return path


BY_BLOCK = ("tr", "src", "edits")


def chunk_files(d):
    """Файлы кусков в порядке записи: старые раньше, свежие позже.

    Один и тот же блок лежит в двух файлах, когда нарезка сдвинулась: убрали
    из книги повторы — и блок из пятьдесят девятого куска стал блоком
    пятьдесят восьмого, а прежний файл остаётся лежать (стирать его нельзя,
    там работа по другим блокам). Читали такие файлы по имени, и побеждал не
    свежий перевод, а больший номер: кусок переводился заново, а в книгу шёл
    прежний текст — молча и за деньги.

    Порядок берём из отметки времени внутри файла; у файлов от прежних версий
    её нет, для них — время самого файла.
    """
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        p = f"{d}/{n}"
        try:
            at = json.load(open(p, encoding="utf-8")).get("saved")
        except Exception:                                 # noqa: BLE001
            at = None
        out.append((at if at is not None else os.path.getmtime(p), n, p))
    return [(n, p) for _, n, p in sorted(out, key=lambda x: x[0])]


def _save(path, obj, keep=True, stamp=True):
    """Записать файл куска целиком или никак.

    При `--jobs > 1` соседний поток читает хвост предыдущего куска ровно
    тогда, когда этот его пишет, и половина файла — это разбор json с
    ошибкой и падение всего прохода. Подмена готового файла атомарна.

    Работу прежнего файла не выбрасываем. Имя файла — номер куска, а нарезка
    между прогонами меняется: убрали из книги повторы — и восемьдесят первый
    кусок стал восьмидесятым. Тогда `--only translate --chunks 29` пишет
    `tr/0029.json` для нового двадцать девятого куска и стирает переводы,
    лежавшие там от старого. На живой книге так пропало 104 блока из 701, и
    увидеть это можно было только на сборке. Читают эти файлы по номерам
    блоков, а не по именам, поэтому лишняя запись безвредна: не подойдёт
    отпечаток — её просто не возьмут.
    """
    if keep and os.path.exists(path):
        try:
            was = json.load(open(path, encoding="utf-8"))
        except Exception:
            was = {}
        for k in BY_BLOCK:
            if isinstance(was.get(k), dict) and isinstance(obj.get(k), dict):
                obj[k] = {**was[k], **obj[k]}
    # Метка нужна там, где на один блок притязают два файла: спор решается по
    # времени записи. У сплошных карт «блок → работа» (`ocrfix.json`,
    # `code.json`) спорить не с кем, а метка легла бы к ним как блок с именем
    # `saved` — и проход, считающий работу по блокам, падал на ней.
    if stamp:
        obj["saved"] = time.time()
    tmp = mkparent(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _blockmap(path):
    """Карта «блок → сделанная работа». Метку времени, если она туда попала от
    прежних выпусков, снимаем: блока с именем `saved` не бывает."""
    try:
        out = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out.pop("saved", None)
    return out


def _backups(fallback):
    """Запасные модели списком.

    Проходы получают цепочку: первая модель делает книгу, до второй доходят
    единицы кусков, до третьей — считанные. Одиночную модель принимаем тоже:
    так подстраховка задавалась раньше.
    """
    if not fallback:
        return []
    return list(fallback) if isinstance(fallback, (list, tuple)) else [fallback]


def _edit_row(primary, spares, by, ban, self_edit="allow"):
    """Очередь редакторов для куска. `by` — модель, переведшая кусок.

    `self_edit` — судьба переведшей модели в очереди (ключ `--self-edit`):
    `allow` — очередь как задана; `last` — переведшая встаёт в конец,
    даже когда она главный редактор; `never` — вычёркивается совсем, и
    пустая очередь значит «кусок не правится»: самоправка слепа к своим
    калькам, и запрет дороже пропуска.

    Кусок, переведённый после отказа с записанными именами (`ban` —
    список), идёт цепочке ещё и за вычетом отказавшихся: отказ вызван
    содержанием, и на правке он повторится — причём тихо, обрывом в начале,
    который не всегда отличим от «править нечего». Обычный кусок —
    `ban is False`.

    В старых файлах имён отказавшихся нет (`ban is None`): кто именно
    отказался, уже не восстановить, а угадать редактора, который откажется
    тихо, дороже самоправки — такой кусок по-старому правит переведшая
    (кроме `never`: там запрет главнее).
    """
    if ban is None and by and self_edit != "never":
        translator = next((f for f in spares
                           if by == getattr(f, "model", None)), None)
        if translator is None:
            return [primary] + spares
        return [translator] + [a for a in spares if a is not translator]
    row = [a for a in [primary] + spares
           if getattr(a, "model", None) not in (ban or ())]
    mine = [a for a in row if by and getattr(a, "model", None) == by]
    if self_edit == "never":
        return [a for a in row if a not in mine]
    if self_edit == "last":
        row = [a for a in row if a not in mine] + mine
    # Отказали всем, а переведшей модели в цепочке нет: кто-то должен
    # попробовать, и пусть это будет заданный редактор.
    return row or [primary]


def _hold(who, waited, log):
    """Сколько ждать, когда под лимитом вся цепочка. 0 — кто-то свободен.

    Ждать раньше умела сама модель, и это было хуже: ожидание не видно
    снаружи, прогон спал по четыре часа, а запасная модель стояла рядом
    свободная. Ждём только когда ждать больше нечего, и не своей модели, а
    ближайшей освобождающейся.
    """
    pause = min((agent_mod.limit_left(a) for a in who), default=0)
    if not pause:
        return 0
    top = getattr(who[0], "max_wait", 86400)
    if waited >= top:
        return 0
    pause = int(min(pause, top - waited)) + 1
    log("")
    log("    " + T("lim_wait", max(pause // 60, 1), int(waited) // 60))
    time.sleep(pause)
    return pause


def _run_patient(agent, backups, system, prompt, retries, parse, log, lock=None):
    """`_run`, пережидающий на месте лимит и молчаливую смерть.

    Лимит, ударивший в момент вызова, — не приговор куску: обёртка агента уже
    пополнила общий реестр, соседние куски его пережидают, и этот должен ждать
    с ними, а не объявляться несделанным. Кусок уступается цепочке только
    когда запасная модель свободна, а своя — под лимитом: тогда отдать дешевле,
    чем ждать. Молчаливая смерть пережидается как в _chain_run, с потолком
    HUSH_MAX: неизвестной беде час, известному лимиту — до max_wait.
    """
    def say(*lines):
        if lock:
            with lock:
                for x in lines:
                    log(x)
        else:
            for x in lines:
                log(x)

    held = 0
    while True:
        while True:
            step = _hold([agent] + backups, held, log)
            if not step:
                break
            held += step
        try:
            return _run(agent, system, prompt, retries, parse, log)
        except agent_mod.RateLimited as e:
            free = any(not agent_mod.limit_left(fb)
                       for fb in backups if fb is not agent)
            if free or held >= getattr(agent, "max_wait", 86400) or STOP.is_set():
                raise
            say("", "    " + T("chunk_failed", e))
        except agent_mod.Hushed as e:
            if held >= HUSH_MAX or STOP.is_set():
                raise
            say("", "    " + T("hush_wait", HUSH_PAUSE // 60, int(held) // 60))
            time.sleep(HUSH_PAUSE)
            held += HUSH_PAUSE


def _chain_run(who, system, prompt, retries, parse, log):
    """`_run` по цепочке: следующая модель подхватывает и отказ, и сбой.

    Проход, работающий одним запросом на большой кусок книги, без этого падал
    целиком: поставщик отвечал «high traffic» пять попыток подряд, и прогон
    кончался, хотя вторая модель цепочки была задана и стояла рядом.

    Модель, упёршуюся в лимит, пропускаем не спрашивая: об этом знает общий
    реестр, и переспрашивать её на каждом куске значит платить запросом за
    уже известный ответ.
    """
    waited, last, refused = 0, None, []
    while True:
        for k, a in enumerate(who):
            if agent_mod.limit_left(a):
                continue
            if k:
                log("")
                log("    " + T("refused_retry", getattr(a, "model", "?")), end="")
            try:
                got, meta, dt = _run(a, system, prompt, retries, parse, log)
                # Пометка «после отказа» уходит в файл куска вместе с именами
                # отказавшихся: редактура обведёт их стороной, а цепочку
                # редакторов пройдёт как задана. Именно имена, не флаг: без
                # них ей остаётся только отдать кусок переведшей модели —
                # самоправка, слепая к своим калькам.
                if refused:
                    meta = dict(meta, after_refusal=True, refused_by=refused)
                return got, meta, dt
            except (Refused, RuntimeError, Fatal) as e:
                m = getattr(a, "model", None)
                if isinstance(e, (Refused, Blocked)) and m and m not in refused:
                    refused.append(m)
                last = e
        pause = _hold(who, waited, log)
        if not pause:
            # Молчаливая смерть — почти всегда внешняя беда (лимит, давка
            # сессий), пришедшая раньше, чем реестр лимитов о ней узнал:
            # свойство самого куска приходит со словами. Пережидаем штатный
            # интервал и спрашиваем снова; потолок нарочно ниже суточного —
            # неизвестной беде час, известному лимиту сутки.
            if isinstance(last, agent_mod.Hushed) and waited < HUSH_MAX:
                pause = HUSH_PAUSE
                log("")
                log("    " + T("hush_wait", pause // 60, int(waited) // 60))
                time.sleep(pause)
            else:
                raise last or AgentError(T("lim_gave_up", waited // 3600, ""))
        waited += pause


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


def _cool(who, refused, log):
    """Переждать сбой, прежде чем брать следующий кусок.

    Пауза только на сбое. Лимит выяснится сам: реестр знает, до какого часа
    модель занята, и ждёт её `_hold` — накладывать сверху ещё пять минут
    значит удваивать простой на ровном месте.
    """
    if all(agent_mod.limit_left(a) for a in who):
        return
    pause = FAIL_PAUSE[min(refused[0], len(FAIL_PAUSE)) - 1]
    log("    " + T("fail_wait", max(pause // 60, 1)))
    time.sleep(pause)


# Перевод короче этой доли оригинала или длиннее этой — потеря или чужой
# текст. Порог мягкий: на коротких строках отношение шумит, поэтому есть и
# нижняя граница длины.
LOSS_LOW, LOSS_HIGH, LOSS_MIN = 0.5, 2.5, 60


def _regrow(agent, system, task, blocks, res, retries, log, fallback=None):
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
    body = "\n\n".join(f"[[[P {i}]]]\n{src[i]}" for i in bad)
    prompt = (task + "\n\n---\n\nЭти абзацы уже переводились, и перевод вышел "
              "заметно короче или длиннее оригинала: часть текста потерялась "
              "или попала чужая. Переведи их заново, целиком, ничего не "
              "пропуская и не добавляя от себя.\n\n" + body)
    try:
        (again, _), _, _ = _chain_run([agent] + _backups(fallback), system, prompt,
                                      retries,
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
              fallback=None, to=""):
    os.makedirs(lpath(work, "tr", to), exist_ok=True)
    sp = lpath(work, "state.json", to)
    state = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {"sum": {}, "terms": {}}
    # готовность считаем по блокам, а не по именам файлов: если нарезка
    # изменилась, старый файл покроет не те блоки и оставит дыру
    have, old = {}, False
    for _, p_ in chunk_files(lpath(work, "tr", to)):
        x = json.load(open(p_, encoding="utf-8"))
        fp = x.get("src") or {}
        old = old or not fp
        have.update({k: fp.get(k) for k in x["tr"]})
    if old:
        log("  " + T("no_fingerprint"))

    # Строки таблиц справочника: в запрос идут не все, а встречающиеся в
    # куске — как термины из state.json. Костяк справочника уже в системном
    # промпте, см. split_ref.
    rp = lpath(work, "scout.md", to)
    ref_rows = split_ref(open(rp, encoding="utf-8").read())[1] \
        if os.path.exists(rp) else []

    done = skipped = 0
    refused, halted = [0], False
    for i, c in enumerate(chunks):
        idx = c["index"]
        if only and idx not in only:
            continue
        out_path = f'{lpath(work, "tr", to)}/{idx:04d}.json'
        # Готово — это когда и блок тот же, и текст в нём тот же. Отпечатка
        # нет только у файлов от прежних версий: там верим на слово.
        if not only and all(
                b["id"] in have
                and have[b["id"]] in (None, fingerprint(b["text"]))
                for b in translatable(c["blocks"])):
            skipped += 1
            continue
        summary = condense(state, idx, agent, retries, log, fallback)
        tail = ""
        prev = f'{lpath(work, "tr", to)}/{idx - 1:04d}.json'
        if os.path.exists(prev):
            vals = [v for v in json.load(open(prev, encoding="utf-8"))["tr"].values() if v.strip()]
            tail = "\n\n".join(vals[-TAIL_PARAS:])
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        here = " ".join(b["text"] for b in translatable(c["blocks"]))
        prompt = translate_prompt(c, nxt, summary, tail,
                                  accumulated_terms(state, idx, here),
                                  ref_rows_for(ref_rows, here), task)
        canon = verse_canon(state, c["blocks"])
        if canon:
            prompt += ("\n\n---\n\n" + lang.prompt("verse_canon")[0] + "\n\n"
                       + "\n\n".join(f"[[[P {k}]]]\n{v}" for k, v in canon.items()))
        open(mkparent(f'{lpath(work, "prompts", to)}/{idx:04d}.txt'), "w",
             encoding="utf-8").write(prompt)

        expected = [b["id"] for b in translatable(c["blocks"])]
        srcs = {b["id"]: b["text"] for b in translatable(c["blocks"])}
        log(f"[{idx:04d}/{len(chunks):04d}] {c['label'][:24]:24s} "
            + T("words_n", f"{c['words']:5d}") + " ... ", end="")
        # Вся цепочка под лимитом — переждать; иначе кусок объявили бы
        # непереведённым, а через три таких прогон бы встал. Лимит, ударивший
        # в момент самого вызова, пережидается на месте — см. _run_patient.
        try:
            (res, extra), meta, dt = _run_patient(
                agent, _backups(fallback), system, prompt, retries,
                lambda o: _parse_translate(o, expected, srcs), log)
        except (Refused, RuntimeError, Fatal) as e:
            # Отказ и сбой поставщика тут равны: кусок не переведён, а
            # следующая модель цепочки может и взяться. Прежде ловился один
            # отказ, и «исчерпаны попытки» валили прогон при живой запасной.
            log("")
            if isinstance(e, Refused):
                # Показываем сам текст: по нему сразу видно, почему модель
                # встала, и не нужно гадать про размеры кусков и бюджеты.
                src = next((b["text"] for b in c["blocks"] if b["id"] == e.first), "")
                src = re.sub(r"<[^>]+>", "", src)[:150]
                log("    " + T("refused", e.first, e.n, e.total))
                log(f"      {src}…")
            else:
                log("    " + (T("lim_switch", e) if isinstance(e, RateLimited)
                              else T("chunk_failed", e)))
            # Подстраховка: то же задание следующей модели цепочки. Отказ —
            # свойство модели, а не текста, и у следующей такого запрета может
            # не быть. Идём по цепочке до первой, которая возьмётся.
            backups = _backups(fallback)
            got, last = None, getattr(e, "first", "?")
            deniers = ([getattr(agent, "model", None)]
                       if isinstance(e, (Refused, Blocked)) else [])
            for fb in backups:
                if agent_mod.limit_left(fb):
                    continue
                log("    " + T("refused_retry", getattr(fb, "model", "?")))
                try:
                    got = _run(fb, system, prompt, retries,
                               lambda o: _parse_translate(o, expected, srcs), log)
                    # У перевода свой обход цепочки, мимо _chain_run, и
                    # пометка «после отказа» здесь терялась — вместе с
                    # именами отказавшихся, без которых редактура умеет
                    # только отдать кусок переведшей модели: самоправка.
                    if deniers:
                        got = (got[0], dict(got[1], after_refusal=True,
                                            refused_by=deniers), got[2])
                    break
                except (Refused, RuntimeError, Fatal) as e2:
                    m2 = getattr(fb, "model", None)
                    if isinstance(e2, (Refused, Blocked)) and m2 and m2 not in deniers:
                        deniers.append(m2)
                    last = getattr(e2, "first", last)
            if got is None:
                log("    " + (T("refused_both", last) if backups
                              else T("no_backup") + " " + T("refused_hint", idx)))
                if _stop_row(refused, log):
                    halted = True
                    break
                _cool([agent] + backups, refused, log)
                continue
            (res, extra), meta, dt = got
        refused[0] = 0                 # кусок взят — счётчик отказов сбрасываем
        extra, found = extra
        res, n_grew = _regrow(agent, system, task, translatable(c["blocks"]),
                              res, retries, log, fallback)
        if n_grew:
            log("\n    " + T("regrown", n_grew))
        # Канон сильнее ответа: повторённая строка подставляется из реестра,
        # как бы модель её ни перевела, а новые строки пополняют реестр.
        for k, v in canon.items():
            if k in res:
                res[k] = v
        verse_learn(state, c["blocks"], res)
        summ, terms = _split_meta(extra)
        _save(out_path, {"index": idx, "model": meta["model"],
                         "cost_usd": meta["cost_usd"], "footnotes": found,
                         **{k: meta[k] for k in ("after_refusal", "refused_by")
                            if meta.get(k)},
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
        json.dump(state, open(mkparent(sp), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        log(T("ready_in", f"{dt:.0f}", f"{meta['model']}{cost}"))
    return done, skipped, halted


def _near(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _twins(res, src, order):
    """Два соседних блока с одним и тем же переводом при разных оригиналах.

    Отвечая на длинный ряд похожих абзацев — описания рисунков, строки
    указателя, — модель теряет счёт и повторяет перевод соседа, а один
    оригинал остаётся непереведённым вовсе. Разбору это не видно: все
    идентификаторы на месте, пустых нет, отпечатки сходятся. На живой книге
    так пропало восемь описаний рисунков из шестидесяти восьми, и заметила
    это только проверка чисел на собранной книге.
    """
    def one(t):
        return " ".join(strip(t).split())

    for a, b in zip(order, order[1:]):
        ta, tb = one(res.get(a, "")), one(res.get(b, ""))
        if len(ta) < TWIN_LEN or _near(ta, tb) < TWIN_NEAR:
            continue
        sa, sb = one(src.get(a, "")), one(src.get(b, ""))
        if sa and sb and _near(sa, sb) < TWIN_NEAR:
            return a, b
    return None


def _shifted(res, src, order):
    """Перевод со сдвигом: под меткой блока — текст соседнего.

    Модель вернула все идентификаторы по разу, тексты все разные и каждый
    сам по себе правдоподобен — счёт блоков, близнецы и обрубки молчат.
    Но под меткой N стоит перевод блока N−1: содержание молча разъехалось,
    а последний блок куска остался непереведённым. На живой книге так
    съехали 26 блоков из 38, и заметила это только проверка длин на уже
    собранной книге.

    Семантику разбору сверить нечем — он не зовёт моделей и не знает
    языков. А длину есть чем: длина перевода следует за длиной оригинала.
    Одиночное совпадение с соседом — случайность, окно подряд, где каждый
    перевод ближе к чужому оригиналу, чем к своему, — строй.
    """
    ids = [i for i in order if i in res
           and len(src.get(i, "")) >= SHIFT_MIN and len(res[i]) > 5]

    def off(a, b):
        return abs(math.log(len(res[a]) / len(src[b])))

    for step in (-1, 1):
        for at in range(len(ids) - SHIFT_WIN + 1):
            w = ids[at:at + SHIFT_WIN]
            straight = sum(off(i, i) for i in w) / len(w)
            pairs = [(w[k], w[k + step]) for k in range(len(w))
                     if 0 <= k + step < len(w)]
            slid = sum(off(a, b) for a, b in pairs) / len(pairs)
            if slid + SHIFT_GAP < straight and straight > SHIFT_BAD:
                return w[0], step
    return None


def _parse_translate(out, expected, src=None):
    """Ответ переводчика: абзацы + сноски + служебный блок."""
    ids = {i for i in expected}
    found = parse_notes_blocks(out, ids)
    body = re.split(r"\[\[\[NOTE\s", out)[0]
    res, extra = parse_blocks(body, expected=expected, extra_tag="META")
    # Обрубок вместо перевода: модель оборвалась ПОСРЕДИ блока — «Пациент»
    # вместо сцены на две сотни знаков. Пропавшие и пустые блоки ловятся
    # выше, а полупустой сходил за переведённый, и отпечаток записывал кусок
    # готовым. Порог грубый нарочно: честный перевод короче того же абзаца
    # на порядок не бывает.
    if src:
        stubs = [i for i, t in res.items()
                 if len(src.get(i, "")) >= STUB_MIN
                 and len(t) < len(src[i]) * STUB_SHARE]
        if stubs:
            first = min(stubs, key=lambda i: expected.index(i)
                        if i in expected else 10 ** 9)
            raise Truncated(first, len(stubs), len(expected))
    twin = _twins(res, src or {}, expected)
    if twin:
        raise ValueError(f"один перевод на два блока: {twin[0]} и {twin[1]} "
                         f"— оригиналы у них разные")
    slip = _shifted(res, src or {}, expected)
    if slip:
        raise ValueError(f"перевод сдвинут: в окне от {slip[0]} тексты "
                         f"стоят под метками соседних блоков")
    m = re.search(r"\[\[\[META\]\]\]\s*(.*)$", out, re.S)
    if m:
        extra = _unscaffold(m.group(1))
    return res, (extra, found)


def _split_meta(extra):
    summ = re.search(r"SUMMARY:\s*(.*?)(?=TERMS:|$)", extra, re.S)
    terms = re.search(r"TERMS:\s*(.*)$", extra, re.S)
    return (summ.group(1).strip() if summ else extra.strip(),
            terms.group(1).strip() if terms else "")


# ---------------------------------------------------------------- редактура

def _swap_edit(res, draft):
    """Правка-подмена: под меткой блока — переписанный СОСЕДНИЙ абзац.

    На живой книге реплика героя была замещена вариацией предыдущей фразы:
    читатель видел абзац дважды, чуть по-разному, а настоящий текст пропадал.
    Правка, чей результат ближе к чужому черновику, чем к своему, — не правка.
    """
    for k, new in res.items():
        if len(new) < TWIN_LEN:
            continue
        own = _near(new, draft.get(k, ""))
        for j, d_ in draft.items():
            # Порог мягче близнецового: главную работу делает разрыв
            # «свой против чужого» — у честной правки own высок сам по себе.
            if j != k and len(d_) >= TWIN_LEN \
                    and _near(new, d_) > max(own + 0.3, 0.8):
                return k, j
    return None


def edit(work, chunks, agent, system, task, retries, log, only=None, jobs=1,
         fallback=None, force=False, to="", self_edit="allow"):
    os.makedirs(lpath(work, "ed", to), exist_ok=True)
    now = getattr(agent, "model", None) or ""

    # Строки таблиц справочника едут с куском, а не в системе — см. split_ref.
    # Ключи — оригиналы, поэтому отбор идёт по исходному тексту куска, хотя
    # сам оригинал редактору намеренно не показывается.
    rp = lpath(work, "scout.md", to)
    ref_rows = split_ref(open(rp, encoding="utf-8").read())[1] \
        if os.path.exists(rp) else []

    # Черновик собираем по всей книге, а не из файла с тем же номером.
    # Нарезка могла измениться — от новой версии конвейера или от другого
    # --chunk-words, — и тогда tr/0004.json покрывает уже не тот кусок.
    # Прежде это кончалось пустым запросом: редактор честно отвечал, что
    # править нечего, пустой файл правки ложился поверх старого, и сделанная
    # редактура пропадала кусок за куском.
    raw, whose, banned = {}, {}, {}
    for _, p_ in chunk_files(lpath(work, "tr", to)):
        x = json.load(open(p_, encoding="utf-8"))
        for k, v in x["tr"].items():
            raw[k] = v
            # Переводчик куска: правка никогда не начинается с него —
            # переведшая модель встаёт в конец очереди, даже когда она же
            # главный редактор (см. _edit_row).
            whose[k] = x.get("model") or ""
            if x.get("after_refusal"):
                banned[k] = x.get("refused_by")

    # Что уже отредактировано — тоже по блокам, а не по номерам файлов. Кусок,
    # на котором правка оборвалась, считаем сделанным только до места обрыва.
    ready, stuck = set(), {}
    d = lpath(work, "ed", to)
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            x = json.load(open(f"{d}/{n}", encoding="utf-8"))
            bl = x.get("blocks") or []
            at = x.get("stopped_at") or len(bl)
            fp = x.get("src") or {}
            # Считаем по `src`, а не по списку `blocks`. Имя файла — номер
            # куска, а нарезка между прогонами меняется, и файл с тем же
            # номером перезаписывается под другие блоки. Карты `src` и `edits`
            # при этом сливаются со старыми, список `blocks` — заменяется. По
            # нему выходило, что работа, сделанная при прежней нарезке, не
            # сделана: на живой книге так теряло запись 201 блоков, и каждый
            # запуск переделывал горстку кусков — а новая запись стирала
            # предыдущую, и конца этому не было.
            # Правка сделана по переводу; перевели заново — править надо снова.
            if fp:
                got = {i for i, f in fp.items() if f == fingerprint(raw.get(i, ""))}
                ready |= got - set(bl[at:])
            else:
                ready.update(bl[:at])        # файлы прежних выпусков
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
        out_path = f'{lpath(work, "ed", to)}/{idx:04d}.json'
        ids = [b["id"] for b in translatable(c["blocks"])]
        draft = {i: raw[i] for i in ids if i in raw}
        if not draft:
            return
        # Кусок, переведённый после отказа, идёт обычной цепочке редакторов
        # за вычетом отказавшихся: отказ вызван содержанием, и на правке он
        # повторится (замерено: 2 правки из 41, обе в первых двух абзацах).
        # Очередь и судьба старых файлов без имён — в _edit_row.
        row = _edit_row(agent, _backups(fallback), whose.get(ids[0], ""),
                        banned.get(ids[0], False), self_edit)
        if not row:
            # --self-edit never: в очереди остался один переводчик —
            # кусок остаётся без правки, о чём говорим вслух.
            with lock:
                log(f"[{idx:04d}/{n_all:04d}] {who:24s} " + T("ed_self_only"))
            return
        mine, spares = row[0], row[1:]
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
            ep = f'{lpath(work, "ed", to)}/{idx - 1:04d}.json'
            if os.path.exists(ep):
                for k, e in json.load(open(ep, encoding="utf-8"))["edits"].items():
                    if k in ptxt and ptxt[k] == e.get("old", ptxt[k]):
                        ptxt[k] = e["new"]
            tail = "\n\n".join([v for v in ptxt.values() if v.strip()][-TAIL_PARAS:])
        # Конспект сюжета, накопленный при переводе. Редактору он нужен не
        # меньше: правя местоимения, обращения и связки, легко исказить смысл,
        # если не знаешь, кто в сцене, что уже случилось и кем персонажи
        # приходятся друг другу.
        digest, terms = "", []
        src_txt = " ".join(b["text"] for b in translatable(c["blocks"]))
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
            terms = accumulated_terms(st, 1 << 30, src_txt)
        # только русский текст: оригинал намеренно не показываем, иначе
        # правка идёт в сторону чужого синтаксиса, а не хорошего русского
        pairs = [f"[[[{MARK.get(b['kind'], 'P')} {b['id']}]]]\n{draft[b['id']]}"
                 for b in translatable(c["blocks"]) if b["id"] in draft]
        parts = [task, _chunk_head(idx, c["label"])]
        if digest:
            hint_prev, _ = lang.prompt("translate_hint_prev")
            parts.append(hint_prev + "\n\n" + digest)
        if tail:
            hint_tail, _ = lang.prompt("translate_hint_tail")
            parts.append(hint_tail + "\n\n" + tail)
        if terms:
            parts.append(
                lang.prompt("translate_hint_terms")[0] + "\n\n" + "\n".join(terms))
        refs = ref_rows_for(ref_rows, src_txt)
        if refs:
            parts.append(lang.prompt("ref_rows")[0] + "\n\n" + "\n".join(refs))
        parts.append(lang.prompt("edit_fragment")[0] + "\n\n" + "\n\n".join(pairs))
        prompt = "\n\n---\n\n".join(parts)
        open(mkparent(f'{lpath(work, "prompts", to)}/{idx:04d}.edit.txt'), "w",
             encoding="utf-8").write(prompt)

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

        def parse(o):
            res, tail = parse_blocks(o, allowed=set(draft), extra_tag="NOTES")
            swap = _swap_edit(res, draft)
            if swap:
                raise ValueError(
                    f"правка {swap[0]} подменяет абзац {swap[1]}: результат "
                    f"ближе к чужому черновику, чем к своему")
            return res, tail

        # Заняты все — переждать. Прогон не встанет: три подряд «не взялись»
        # останавливают редактуру, а лимит — не отказ и пройдёт сам.
        try:
            (res, notes), meta, dt = _run_patient(mine, spares,
                                                  system, prompt, retries,
                                                  parse, log, lock)
        except (Refused, RuntimeError, Fatal) as e:
            # Сбой — не то же, что «править нечего»: пустой результат нельзя
            # записать как готовый кусок, иначе следующий запуск сочтёт его
            # сделанным. Идём по цепочке, как и при обрыве.
            with lock:
                log("    " + (T("lim_switch", e) if isinstance(e, RateLimited)
                              else T("chunk_failed", e)))
            (res, notes), dt = ({}, []), 0.0
            meta = {"model": getattr(mine, "model", "?"), "cost_usd": 0}
            failed = True
        else:
            failed = False
        stopped = _stopped(res, ids)
        for fb in spares:
            if agent_mod.limit_left(fb):
                continue
            # Оборвалась правка — передаём кусок следующей модели цепочки, как
            # и при отказе перевода. Иначе кусок остался бы наполовину
            # нетронутым, а при следующем запуске зачёлся бы готовым: файл-то
            # записан.
            if not stopped and not failed:
                break
            if fb is mine:
                continue          # этой моделью кусок только что и правился
            with lock:
                if stopped:
                    log("    " + T("edit_stopped", stopped, len(ids)))
                log("    " + T("refused_retry", getattr(fb, "model", "?")))
            try:
                (res2, notes2), meta2, dt2 = _run(fb, system, prompt, retries,
                                                  parse, log)
            except (Refused, RuntimeError, Fatal) as e:
                with lock:
                    log("    " + (T("lim_switch", e) if isinstance(e, RateLimited)
                              else T("chunk_failed", e)))
                continue
            if len(res2) > len(res) or failed:
                res, notes, meta, dt = res2, notes2, meta2, dt2
                failed = False
                stopped = _stopped(res, ids)
        if failed:
            # Никто не взялся. Файла не пишем вовсе: пустая правка легла бы
            # как готовая, и следующий запуск обошёл бы кусок стороной.
            # Непроредактированный кусок остаётся переведённым и читаемым.
            with lock:
                log("    " + T("edit_failed", idx))
                # Об остановке говорим один раз: при jobs>1 сюда приходят все
                # запущенные куски, и «ОСТАНОВКА: 7 отказа подряд» пять раз
                # кряду выглядит так, будто счётчик сломался.
                if not halt[0]:
                    halt[0] = _stop_row(refused, log, force, "edit")
            if not halt[0]:
                _cool([mine] + spares, refused, log)
            return
        # Отпечатки — только по блокам, до которых правка дошла: по ним и
        # считается сделанное. Хвост оборванного куска в `src` не пишем,
        # иначе он сойдёт за отредактированный.
        covered = ids[:stopped] if stopped else ids
        out = {"index": idx, "model": meta["model"], "cost_usd": meta["cost_usd"],
               "notes": notes, "blocks": ids,
               "src": {k: fingerprint(draft[k]) for k in covered},
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
                if not halt[0]:
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
    return done, skipped, total, halt[0]


def current(work, idx, to=""):
    tp = f'{lpath(work, "tr", to)}/{idx:04d}.json'
    if not os.path.exists(tp):
        return {}
    base = json.load(open(tp, encoding="utf-8"))["tr"]
    for sub in ("ed", "vf"):
        ep = f'{lpath(work, sub, to)}/{idx:04d}.json'
        if os.path.exists(ep):
            for k, e in json.load(open(ep, encoding="utf-8"))["edits"].items():
                if k in base and base[k] == e.get("old", base[k]):
                    base[k] = e["new"]
    return base


def all_translations(work, to=""):
    """Черновик с наложенной редактурой и сверкой + счётчик правок."""
    tr, edited = {}, 0
    for _, p_ in chunk_files(lpath(work, "tr", to)):
        tr.update(json.load(open(p_, encoding="utf-8"))["tr"])
    for sub in ("ed", "vf"):
        for _, p_ in chunk_files(lpath(work, sub, to)):
            for k, e in json.load(open(p_, encoding="utf-8"))["edits"].items():
                # Пара «было и стало» делает правку самопроверяемой: если
                # «было» не совпадает с текстом под рукой, правка — сирота
                # от прежнего перевода (блок перевели заново, а карты edits
                # при перезаписи сливаются) и ложиться поверх не должна.
                # На живой книге такая сирота вернула в текст сцену,
                # выброшенную вместе со старым переводом. Кусок при этом не
                # считается отредактированным — следующий проход правки
                # переделает его честно.
                if k in tr and tr[k] == e.get("old", tr[k]):
                    tr[k] = e["new"]
                    edited += 1
    return tr, edited


# ---------------------------------------------------------------- сверка

# «Замечаний нет» редактор пишет на языке перевода, и списком отрицаний
# всё не покрыть: до слов дело доходит только у коротких ответов.
NO_NOTES = ("нет", "none", "keine", "aucune", "无", "なし", "-", "—")


def has_notes(t):
    t = (t or "").strip()
    return bool(t) and len(t) > 12 and t.lower().rstrip(".") not in NO_NOTES


def _parse_verify(out, want, must=None):
    """Вердикты сверщика + сноски и исправления, каждое при своём вердикте.

    `must` — блоки, по которым вердикт обязателен (замечания редактора);
    остальные из `want` даны на сквозной прочёс, и молчание по ним — норма.
    """
    verdicts = {}
    for m in re.finditer(r"\[\[\[VERDICT\s+(\S+?)\s+"
                         r"(author|translation|dismiss|unsure)\]\]\]"
                         r"\s*(.*?)(?=\[\[\[|\Z)", out, re.S):
        bid, kind, why = m.groups()
        if bid in want:
            verdicts[bid] = (kind, " ".join(why.split()))
    missing = [i for i in (want if must is None else must) if i not in verdicts]
    if missing:
        raise ValueError(f"нет вердикта по блокам {missing[:4]} ({len(missing)})")
    notes = parse_notes_blocks(out, want)
    fixes = {}
    for m in re.finditer(r"\[\[\[P\s+(\S+?)\]\]\]\s*(.*?)(?=\[\[\[|\Z)", out, re.S):
        if m.group(1) in want and m.group(2).strip():
            fixes[m.group(1)] = m.group(2).strip()
    noted = {n["block"] for n in notes}
    bad = [i for i, (k, _) in verdicts.items() if k == "author" and i not in noted]
    if bad:
        raise ValueError(f"вердикт author без сноски: {bad[:4]}")
    bad = [i for i, (k, _) in verdicts.items()
           if k == "translation" and i not in fixes]
    if bad:
        raise ValueError(f"вердикт translation без исправления: {bad[:4]}")
    # Сноска или правка вопреки вердикту — рассинхрон ответа, а не довесок.
    notes = [n for n in notes if verdicts.get(n["block"], ("",))[0] == "author"]
    fixes = {i: v for i, v in fixes.items()
             if verdicts.get(i, ("",))[0] == "translation"}
    return verdicts, notes, fixes


def verify(work, chunks, agent, system, task, retries, log, only=None,
           fallback=None, to="", jobs=1, full=False):
    """Сверка замечаний редактора с оригиналом.

    С `full` кусок сверяется целиком: сверщику идут все пары «оригинал —
    перевод», и помимо замечаний он прочёсывает текст на грубые смысловые
    расхождения. Вердикты обязательны только по замечаниям; молчание по
    остальным абзацам — норма. Вход дорожает, но выход остаётся коротким,
    а гладкая неверная фраза перестаёт быть невидимой для конвейера.

    Редактор работает без оригинала: спорное по существу место он не правит,
    а выписывает замечанием. Сверщик — единственный, кто видит обе стороны.
    Ошибся автор — истина уходит в сноску, а текст остаётся авторским;
    ошибся перевод — блок исправляется; подозрение пустое — снимается.
    Нерешённое остаётся человеку в review.md.

    По умолчанию сверяет цепочка редактора: у переводчика здесь конфликт —
    вердикт «ошибся перевод» выносится его собственной работе, и ему выгодно
    винить автора. Редактор к спорным блокам непричастен: он их сознательно
    не правил.

    Куски сверяются независимо и потому параллелятся (`--jobs`): замечание
    живёт в границах своего куска, а общий словарь текущих переводов — под
    замком, со снимком спорных блоков на время запроса.
    """
    orig = {}
    for c in chunks:
        for b in c["blocks"]:
            orig[b["id"]] = b["text"]
    by_index = {c["index"]: c for c in chunks}
    cur, _ = all_translations(work, to)
    # Строки таблиц справочника — по оригиналам спорных блоков, см. split_ref.
    rp = lpath(work, "scout.md", to)
    ref_rows = split_ref(open(rp, encoding="utf-8").read())[1] \
        if os.path.exists(rp) else []
    os.makedirs(lpath(work, "vf", to), exist_ok=True)
    done = skipped = added = fixed = 0
    n_all = len(chunks)
    lock = threading.Lock()
    pair_tpl = lang.prompt("verify_pair")[0]

    todo = [(int(n.split(".")[0]), ep)
            for n, ep in chunk_files(lpath(work, "ed", to))
            if not only or int(n.split(".")[0]) in only]

    def one(item):
        nonlocal done, skipped, added, fixed
        if STOP.is_set():
            return
        idx, ep = item
        remark = (json.load(open(ep, encoding="utf-8")).get("notes") or "").strip()
        if not has_notes(remark):
            if not full:
                return
            remark = ""
        # Адреса блоков — прямо из текста замечания: редактор помечает их
        # идентификаторами. Замечание без адреса сверять не по чему — оно
        # остаётся человеку, как и раньше.
        out_path = f'{lpath(work, "vf", to)}/{idx:04d}.json'
        with lock:
            must = sorted((i for i in orig if i in remark and i in cur),
                          key=_id_key)
            ids = must
            if full:
                blocks = (by_index.get(idx) or {}).get("blocks") or []
                ids = sorted((b["id"] for b in blocks if b["id"] in cur),
                             key=_id_key)
            if not ids:
                return
            # Сверка сделана по замечанию и по тексту; изменилось любое — снова.
            if os.path.exists(out_path) and not only:
                old = json.load(open(out_path, encoding="utf-8"))
                if old.get("remark") == fingerprint(remark) and all(
                        old.get("src", {}).get(i) == fingerprint(cur[i])
                        for i in ids):
                    skipped += 1
                    return
            snap = {i: cur[i] for i in ids}
        who = ((by_index.get(idx) or {}).get("label") or "—")[:24]
        rows = [pair_tpl.format(id=i, orig=orig[i], tr=snap[i]) for i in ids]
        pieces = [task]
        if full:
            pieces.append(lang.prompt("verify_sweep")[0])
        if remark:
            pieces.append(lang.prompt("verify_remarks")[0] + "\n\n" + remark)
        refs = ref_rows_for(ref_rows, " ".join(orig[i] for i in ids))
        if refs:
            pieces.append(lang.prompt("ref_rows")[0] + "\n\n" + "\n".join(refs))
        pieces.append(lang.prompt("verify_pairs")[0] + "\n\n" + "\n\n".join(rows))
        prompt = "\n\n---\n\n".join(pieces)
        open(mkparent(f'{lpath(work, "prompts", to)}/{idx:04d}.verify.txt'), "w",
             encoding="utf-8").write(prompt)
        with lock:
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} " + T("vf_start", len(ids)))
        want, need = set(ids), set(must)
        res = None
        for m in [agent] + [f for f in _backups(fallback) if f is not agent]:
            try:
                res, meta, dt = _run(m, system, prompt, retries,
                                     lambda o: _parse_verify(o, want, need), log)
                break
            except (Refused, RuntimeError, Fatal, ValueError) as e:
                with lock:
                    log("    " + (T("lim_switch", e) if isinstance(e, RateLimited)
                              else T("chunk_failed", e)))
        if res is None:
            return
        verdicts, notes_, fixes = res
        kinds = {"author": T("vf_author"), "translation": T("vf_fixed"),
                 "dismiss": T("vf_dismissed"), "unsure": T("vf_unsure")}
        lines = [f"{i}: {kinds[verdicts[i][0]]} {verdicts[i][1]}".strip()
                 for i in ids if i in verdicts]
        out = {"index": idx, "model": meta["model"], "cost_usd": meta["cost_usd"],
               "remark": fingerprint(remark),
               # Отпечаток — от текста ПОСЛЕ исправления: следующий запуск
               # увидит его же и сочтёт сверенным. По досверочному сверка
               # зацикливалась бы на каждом исправленном блоке.
               "src": {i: fingerprint(fixes.get(i, snap[i])) for i in ids},
               "notes": "\n".join(lines),
               "footnotes": notes_,
               "edits": {i: {"old": snap[i], "new": v} for i, v in fixes.items()}}
        _save(out_path, out)
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        counts = collections.Counter(k for k, _ in verdicts.values())
        with lock:
            # Дальше по книге сверка идёт уже по исправленному тексту.
            cur.update(fixes)
            done += 1
            added += len(notes_)
            fixed += len(fixes)
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} "
                + T("vf_done", counts["author"], counts["translation"],
                    counts["dismiss"], counts["unsure"], f"{dt:.0f}",
                    f"{meta['model']}{cost}"))

    if jobs > 1 and len(todo) > 1:
        log("  " + T("in_threads", jobs))
        # Не `with`: см. редактуру — при выходе он ждал бы все запущенные
        # задачи, и Ctrl+C не доходил до обработчика.
        ex = cf.ThreadPoolExecutor(max_workers=jobs)
        try:
            list(ex.map(one, todo))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        for it in todo:
            one(it)
    return done, skipped, added, fixed


# ---------------------------------------------------------------- сноски

def notes(work, chunks, agent, system, task, retries, log, only=None, jobs=1,
          fallback=None, to=""):
    """Предложение сносок и проверка фактов.

    Идёт по кускам, накапливая уже предложенное: одно и то же понятие не должно
    получить сноску дважды. Результат — файлы `work/nt/NNNN.json`, которые
    сборщик накладывает на текст. Ставит сноски сборщик, а не модель: иначе длинная
    глава, переведённая несколькими запросами, получит пояснение по разу
    на каждый.
    """
    os.makedirs(lpath(work, "nt", to), exist_ok=True)
    chain = [agent] + _backups(fallback)
    todo, skipped = [], 0
    for c in chunks:
        idx = c["index"]
        if only and idx not in only:
            continue
        if os.path.exists(f'{lpath(work, "nt", to)}/{idx:04d}.json') and not only:
            skipped += 1
            continue
        todo.append(c)

    # Список «уже объяснено» снимается один раз, до начала: при работе в
    # несколько потоков куски не увидят предложений друг друга. Это не беда —
    # окончательная дедупликация по термину всё равно делается при сборке,
    # так что дублей в книге не будет, лишними окажутся лишь предложения.
    already = []
    for n in sorted(os.listdir(lpath(work, "nt", to))):
        if n.endswith(".json"):
            for it in json.load(open(f'{lpath(work, "nt", to)}/{n}',
                                     encoding="utf-8"))["notes"]:
                already.append(it["term"])

    # Строки реестра — по оригиналам куска, как у перевода: см. split_ref.
    # Кандидаты в сноски и пометки «автор объясняет сам» лежат в реестре и
    # приезжают только в куски, где их слово встречается.
    rp = lpath(work, "scout.md", to)
    ref_rows = split_ref(open(rp, encoding="utf-8").read())[1] \
        if os.path.exists(rp) else []

    done = total = 0
    lock = threading.Lock()
    n_all = len(chunks)

    def one(c):
        nonlocal done, total
        if STOP.is_set():
            return
        idx = c["index"]
        who = (c["label"] or "—")[:24]
        out_path = f'{lpath(work, "nt", to)}/{idx:04d}.json'
        tr = current(work, idx)
        if not tr:
            return
        with lock:
            log(f"[{idx:04d}/{n_all:04d}] {who:24s} " + T("nt_start"))

        pair_tpl = lang.prompt("notes_pair")[0]
        pairs = [pair_tpl.format(id=b["id"], orig=b["text"], tr=tr[b["id"]])
                 for b in translatable(c["blocks"]) if b["id"] in tr]
        parts = [task, _chunk_head(idx, c["label"])]
        if already:
            hint_already, _ = lang.prompt("edit_hint_already")
            parts.append(hint_already + "\n\n" + "\n".join(sorted(set(already))))
        refs = ref_rows_for(ref_rows, " ".join(
            b["text"] for b in translatable(c["blocks"])))
        if refs:
            parts.append(lang.prompt("ref_rows")[0] + "\n\n" + "\n".join(refs))
        parts.append(lang.prompt("edit_fragment")[0] + "\n\n" + "\n\n".join(pairs))
        prompt = "\n\n---\n\n".join(parts)
        open(mkparent(f'{lpath(work, "prompts", to)}/{idx:04d}.notes.txt'), "w",
             encoding="utf-8").write(prompt)

        def parse_notes(out):
            items = []
            for m in re.finditer(
                    r"\[\[\[NOTE\s+(\S+?)\s+(reference|fact|term|source)\]\]\]\s*TERM:\s*(.*?)\n"
                    r"TEXT:\s*(.*?)(?=\[\[\[NOTE|\Z)", out, re.S):
                bid, kind, term, text = m.groups()
                if bid in tr and text.strip():
                    items.append({"block": bid, "kind": kind,
                                  "term": term.strip(), "text": " ".join(text.split())})
            return items, ""

        (items, _), meta, dt = _chain_run(chain, system, prompt, retries,
                                          parse_notes, log)
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


def _lead(it):
    """Сноска начинается с термина: «Vesti — российский телеканал…».

    По ссылке читалка показывает одну сноску, без абзаца вокруг, а список в
    конце книги читают и подряд: без термина это ответы без вопросов. Текст,
    который с термина и так начинается, не трогаем — по основе слова, чтобы
    склонённое начало не считалось другим.
    """
    t = it["text"]
    term = re.sub(r"<[^>]+>", "", str(it.get("term") or "")).strip().strip('"«»“”')
    if not term or len(term) > 60:
        return t
    # В TERM бывает несколько слов разом («микроже, же»): текст, начатый с
    # любого из них, уже начат с термина — иначе вышло бы «же — Же — …».
    for part in re.split(r"[,;/]", term):
        w = (part.strip().split() or [""])[0]
        if not w:
            continue
        stem = w[:-2] if len(w) >= 6 else w[:-1] if len(w) >= 4 else w
        # Начало слова обязательно: «же» иначе находится внутри «тяжести».
        if re.search(rf"\b{re.escape(stem)}", t[:len(term) + 32], re.I):
            return t
    lead = re.split(r"[,;/]", term)[0].strip() or term
    return f"{lead} — {t}"


def all_notes(work, order, to=""):
    """Все сноски в порядке следования по книге, по одной на блок."""
    got, seen = {}, set()
    src = []
    for sub, key in (("tr", "footnotes"), ("ed", "footnotes"),
                     ("vf", "footnotes"), ("nt", "notes")):
        d = lpath(work, sub, to)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                src += [(it, sub) for it in
                        json.load(open(f"{d}/{n}", encoding="utf-8")).get(key) or []]
    for it, sub in src:
        key = it["term"].lower()
        if key in seen:
            continue                    # одно понятие объясняем один раз
        seen.add(key)
        got.setdefault(it["block"], []).append((it, sub))
    merged = {}
    for bid in sorted(got, key=lambda x: order.get(x, 10 ** 9)):
        # Метку «Прим. переводчика» получают все сноски этого прохода, включая
        # выходные данные цитат. Прежде их оставляли без метки как «не
        # примечание, а библиография», но у книги, где авторских сносок нет
        # вовсе, читатель принимал такую сноску за авторскую. Авторские сюда
        # не попадают: они приходят блоками из самой книги.
        merged[bid] = {"text": " ".join(_lead(i) for i, _ in got[bid]),
                       # Термины — для точной привязки знака сноски: он
                       # ставится в тексте сразу после объясняемого слова.
                       "terms": [i["term"] for i, _ in got[bid] if i.get("term")],
                       "source_only": False,
                       # Сноска сверки говорит от имени редактора, и сборка
                       # подписывает её «Прим. ред.» вместо переводческого
                       # префикса. Смесь с переводческой в одном блоке идёт
                       # под общим префиксом: подпись одна на сноску.
                       # Пометка в самой записи равна происхождению из vf:
                       # второй проход редактуры запекает сверочные сноски в
                       # tr, и голос обязан остаться редакторским.
                       "editor": all(s == "vf" or i.get("editor")
                                     for i, s in got[bid]),
                       # Цитата по чужому переводу: машина не может
                       # подтвердить, что текст взят из издания, а не
                       # восстановлен по памяти. Читателю говорят об этом
                       # прямо у цитаты, а не только в отчёте.
                       "source": any(i.get("kind") == "source"
                                     for i, _ in got[bid])}
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

    from . import format as fmt
    n = (len(paras) + fmt.WINDOW - 1) // fmt.WINDOW
    log("  " + T("marks_more", at, len(paras), n) if at
        else "  " + T("marks_start", len(paras), n), end="")
    t = time.time()
    cost = [0.0]

    who = [agent] + _backups(fallback)

    held = [0]

    def run(body, k=0):
        # Все под лимитом — переждать: окно, брошенное на полпути, встанет в
        # `marks.part.json` недоделанным, и разметка выйдет дырявой.
        while all(agent_mod.limit_left(a) for a in who):
            step = _hold(who, held[0], log)
            if not step:
                break
            held[0] += step
        a = next((x for x in who[k:] if not agent_mod.limit_left(x)), who[k])
        if a is not who[0]:
            log("")
            log("  " + T("refused_retry", getattr(a, "model", "?")), end="")
        out, meta = a.run("", task + "\n\n---\n\n" + body)
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




def _parse_code(out, allowed):
    """Записи [[[C номер строка]]] из ответа: {номер листинга: [(строка, ориг, пер)]}."""
    got = {}
    for m in re.finditer(r"\[\[\[C\s+(\S+?)\s+(\d+)\]\]\]\s*ORIG:\s*(.*?)\n\s*TR:\s*(.*?)(?=\[\[\[|\Z)",
                         out, re.S):
        bid, no, orig, tr = m.groups()
        if bid in allowed:
            got.setdefault(bid, []).append((int(no), orig.strip(), tr.strip()))
    return got


def code_comments(work, blocks, agent, system, task, retries, log,
                  fallback=None, to=""):
    """Перевод комментариев в листингах. Код остаётся байт в байт.

    Комментарии ищет модель — знаков комментария у языков сотни, разбором их
    не покрыть. Но подставляет перевод не она: программа берёт названный ею
    кусок, находит его в названной строке и меняет ровно его (см. code.py).
    Не сошлось — строка остаётся как была.

    Готовое лежит в `work/code.json`, поэтому повтор прогона переводит
    только новые листинги.
    """
    from . import code as C
    who = [agent] + _backups(fallback)
    p = lpath(work, "code.json", to)
    done = _blockmap(p)
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
            f"[[[CODE {b['id']}]]]\n" +
            "\n".join(f"{k}| {l}" for k, l in enumerate(b["text"].split("\n"), 1))
            for b in part)
        try:
            (got, _), meta, _ = _chain_run(who, system,
                                           task + "\n\n---\n\n" + body, retries,
                                           lambda o: (_parse_code(o, ids), ""), log)
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
    _save(p, done, stamp=False)
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
    """Записи [[[F номер]]] из ответа: {номер: [(было, стало), ...]}."""
    got = {}
    for m in re.finditer(r"\[\[\[F\s+(\S+?)\]\]\]\s*ORIG:\s*(.*?)\n\s*FIX:\s*(.*?)(?=\[\[\[|\Z)",
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
    done = _blockmap(p)
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
        body = "\n\n".join(f"[[[F {b['id']}]]]\n{strip(b['text'])}" for b in part)
        got = None
        try:
            (got, _), meta, _ = _chain_run(
                [agent] + _backups(fallback), system,
                task + "\n\n---\n\n" + body, retries,
                lambda o: (_parse_fix(o, ids), ""), log)
        except (Refused, RuntimeError, Fatal):
            # Правка распознавания — не та потеря, ради которой стоит валить
            # прогон: неисправленный дефект останется в тексте, и его видно.
            got = None
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
        _save(p, done, stamp=False)
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


def _code_print():
    """Отпечаток кода конвейера: sha256 по файлам пакета, 12 знаков.

    Номер выпуска называет версию, но не доказывает её: рабочая копия между
    тегами — это тоже какая-то версия. Отпечаток считается по содержимому
    самих файлов — кода, промптов, правил языков, — и двум одинаковым
    отпечаткам можно верить, что конвейер был одинаков.
    """
    h = hashlib.sha256()
    base = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(files):
            if n.endswith((".py", ".md", ".json", ".conf")):
                p = os.path.join(root, n)
                h.update(os.path.relpath(p, base).encode())
                h.update(open(p, "rb").read())
    return h.hexdigest()[:12]


def _release():
    """Номер выпуска — как в `build.release_version`: индекс, потом pyproject.
    В рабочей копии без установки индекса нет, и версия выходила «?»."""
    try:
        from importlib.metadata import version as _v
        return _v("booktrans")
    except Exception:                                 # noqa: BLE001
        pass
    p = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "pyproject.toml")
    try:
        m = re.search(r'^version\s*=\s*"([^"]+)"',
                      open(p, encoding="utf-8").read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def note_version(work, stamp=None):
    """Записать в рабочую папку, какая версия конвейера её трогала.

    Папки переживают архивы и годы: перевод сделан одной версией, правка —
    другой, пересборка — третьей. Когда архив вернётся, по этому файлу видно,
    какими версиями что делалось и какие миграции внутренних форматов нужны.
    Хранится каждая версия с датами первого и последнего касания; `first` и
    `last` сверху — для беглого взгляда.
    """
    p = os.path.join(work, "versions.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:                                 # noqa: BLE001
        d = {}
    import datetime
    key = stamp or f"{_release()} {_code_print()}"
    today = datetime.date.today().isoformat()
    seen = d.setdefault("seen", {})
    rec = seen.setdefault(key, {"first": today})
    rec["last"] = today
    d.setdefault("first", {"pipeline": key, "date": today})
    d["last"] = {"pipeline": key, "date": today}
    json.dump(d, open(mkparent(p), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def note_source(work, **kw):
    """Чем читали и чем размечали книгу — для раздела в её конце."""
    p = f"{work}/source.json"
    was = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    was.update({k: v for k, v in kw.items() if v})
    json.dump(was, open(p, "w", encoding="utf-8"), ensure_ascii=False)


# ---------------------------------------------------------------- разведка

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


NO_INJECT = {"нет", "none", "no", "false", "0", "-", "—", "не обнаружены",
             "не обнаружено", "not found"}


def injected(merged):
    """Внедрённые в книгу обращения к переводящей машине, если разведка их
    нашла. Возвращает строки с местами; пустой список — чисто.

    Различение делает разведка, и оно существенное: книга **про** искусственный
    разум приводит промпты примером на каждой странице, и останавливать перевод
    на любом упоминании значило бы не переводить как раз те книги, ради которых
    конвейер и заводят. Находкой считается лишь указание, обращённое к тому,
    кто книгу обрабатывает, и не относящееся к её содержанию.

    Выполнить такое указание модели всё равно нечем — инструменты отключены
    наглухо. Опасно другое: им можно увести перевод, а увидит это человек уже
    в готовой книге.
    """
    m = re.search(r"^\s*INJECTED:\s*(.*)$", merged or "", re.M)
    if not m or m.group(1).strip().strip(".").lower() in NO_INJECT:
        return []
    # Под строкой идёт перечень мест: пустые строки до него пропускаем, на
    # первой непустой не из перечня останавливаемся.
    out = []
    for line in merged[m.end():].splitlines():
        line = line.strip()
        if line.startswith(("-", "*", "•")):
            out.append(line.lstrip("-*• ").strip())
        elif line or out:
            break
    return out or [m.group(1).strip()]


def _headify(text):
    """Разделы, написанные голым словом: «META» вместо «## META».

    Модель порой опускает решётки, а заголовки ищут все потребители
    справочника: выходные данные, сведение имён цикла, двоящиеся термины,
    пересжатие. На живой книге безрешёточный файл молча оставил книгу без
    заглавия, а сведение имён — без единого раздела.
    """
    out = []
    for line in text.split("\n"):
        t = line.strip()
        if (t and not t.startswith("#") and len(t) < 60 and re.match(
                r"(?:META|CHARACTERS|VOICES|NAMES|TERMS|GENDER|ADDRESS|"
                r"WORLD|FOOTNOTES|VERSE|RISK)\b\s*($|—|-|:)", t)):
            line = "## " + t
        out.append(line)
    return "\n".join(out)


def scout_meta(work, to=""):
    """Выходные данные, вычитанные разведкой из самого текста.

    Нужны там, где формат их не хранит: у txt и pdf метаданных нет вовсе,
    и без этого в заголовок книги попадает имя файла. Берётся только как
    запасной вариант — порядок старшинства задан в booktrans.
    """
    p = lpath(work, "scout.md", to)
    if not os.path.exists(p):
        return {}
    txt = open(p, encoding="utf-8").read()
    # Уровень заголовка не задан жёстко: модель ставит то «##», то «#», и
    # книга из-за одной решётки выходила с заглавием оригинала. Заодно
    # принимаем перевод названия раздела — по-английски и по-немецки.
    # Заголовок раздела несёт ключ латиницей: «## META — Выходные данные».
    # Он затем и нужен, что справочник пишется на целевом языке вместе с
    # названиями разделов, а список из четырёх слов их не покрывал: на
    # «PUBLICATION DATA» разбор не находил ничего, и книга выходила под
    # именем файла — без автора и с непереведённым словом в заглавии.
    m = re.search(r"(?m)^#{1,4}\s*META\b(.*?)(?=\n#{1,4}\s|\Z)", txt, re.S)
    if not m:      # справочники прежних выпусков
        m = re.search(r"#{1,4}\s*(?:ВЫХОДНЫЕ ДАННЫЕ|IMPRINT|METADATA|IMPRESSUM)"
                      r"(.*?)(?=\n#{1,4}\s|\Z)", txt, re.S | re.I)
    # А если и этого нет — берём первый раздел, каким бы он ни назывался:
    # выходные данные стоят первыми всегда.
    head = m.group(1) if m else (re.split(r"(?m)^#{1,4}\s.*$", txt) + [""])[1]
    if not head.strip():
        return {}
    out = {}
    allowed = {"title", "author", "year", "publisher", "edition", "series", "series_no",
               "genre"}
    for line in head.splitlines():
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
        if key not in allowed | {"title_target", "author_target", "series_target"}:
            continue
        if key == "genre":
            # Сверяем со словарём: выдуманный код хуже умолчания, потому что
            # выглядит как настоящий и никем не проверяется.
            v = v.strip().strip("`").lower()
            if v not in GENRES:
                continue
        if v and not v.startswith("("):
            out[key] = v

    # Разделы-указатели: модель перечисляет их под ключом drop_sections:
    # Ищем блок вида «drop_sections:\n- Index\n- Name Index»
    ds_m = re.search(r"drop_sections\s*:\s*\n((?:\s*[-*]\s*.+\n?)+)", txt, re.I)
    if ds_m:
        sections = []
        for line in ds_m.group(1).splitlines():
            item = re.sub(r"^\s*[-*]\s*", "", line).strip().strip('"\'')
            if item:
                sections.append(item)
        if sections:
            out["drop_sections"] = sections

    return out


def _id_key(i):
    """Порядковый ключ номера блока: s10.b0002 → (10, 2)."""
    m = re.match(r"s(\d+)\.[a-z]*?(\d+)", i)
    return (int(m.group(1)), int(m.group(2))) if m else (9999, 0)


def reanchor(work, blocks, to, log):
    """Перепривязать сделанный перевод, если нумерация блоков сдвинулась.

    Номера позиционные, и одна перечитанная страница сдвигает их у всей
    книги: на живой книге лишний заголовок объявил непереведёнными 477
    блоков из 527, хотя перевод лежал рядом — под прежними номерами. Текст
    при этом тот же, и отпечатки оригинала в `tr/*.json` это доказывают:
    сопоставляем старую и новую последовательности отпечатков и переписываем
    ключи. Платить заново остаётся только за то, что правда изменилось.
    """
    raw = [b for b in blocks if b["kind"] in ("p", "verse", "note", "table")]
    cur = {b["id"]: fingerprint(b["text"]) for b in raw}
    stored = {}
    for _, p_ in chunk_files(lpath(work, "tr", to)):
        d = json.load(open(p_, encoding="utf-8"))
        stored.update(d.get("src") or {})
    moved = {i for i, f in stored.items() if cur.get(i) != f}
    if not moved:
        return 0
    old_ids = sorted(stored, key=_id_key)
    new_ids = [b["id"] for b in raw]
    sm = difflib.SequenceMatcher(None, [stored[i] for i in old_ids],
                                 [cur[i] for i in new_ids], autojunk=False)
    remap = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            if old_ids[a + k] != new_ids[b + k]:
                remap[old_ids[a + k]] = new_ids[b + k]
    if not remap:
        return 0

    def rename(d):
        return {remap.get(k, k): v for k, v in d.items()}

    for sub, keys in (("tr", ("tr", "src")), ("ed", ("src", "edits")),
                      ("nt", ())):
        for _, p_ in chunk_files(lpath(work, sub, to)):
            d = json.load(open(p_, encoding="utf-8"))
            for k in keys:
                if isinstance(d.get(k), dict):
                    d[k] = rename(d[k])
            if isinstance(d.get("blocks"), list):
                d["blocks"] = [remap.get(i, i) for i in d["blocks"]]
            for key in ("notes", "footnotes"):
                if isinstance(d.get(key), list):
                    for n_ in d[key]:
                        if isinstance(n_, dict) and n_.get("block") in remap:
                            n_["block"] = remap[n_["block"]]
            _save(p_, d, keep=False, stamp=False)
    log("  " + T("reanchored", len(remap)))
    return len(remap)


def _name_key(line):
    """Ключ строки раздела NAMES/TERMS: имя на языке оригинала."""
    mb = _BULLET_KEY.match(line)
    if mb:
        # Карточка-маркер «- **Skitter:** Рой — …»: после двухъярусного
        # справочника NAMES часто пишется так, и канон цикла молча пустел —
        # имя главной героини уехало в чужую транслитерацию.
        raw = _unbracket(mb.group(1).strip())
    else:
        m = re.match(r"\s*\|?\s*([^|=]+?)\s*[|=]", line)
        if not m:
            return ""
        raw = m.group(1)
    k = re.sub(r"[\s*_`]+", " ", raw).strip().lower()
    # Артикль не рознит ключи: «the Qax» прежней книги и «Qax» новой — одно
    # имя, а без этого канон не принуждался и раса уехала в другой перевод.
    k = re.sub(r"^(?:the|a|an)\s+", "", k)
    if not k or re.fullmatch(r"[-: ]+", k) or k in ("оригинал", "original"):
        return ""                       # разделитель или шапка таблицы
    return k


def _domains(parts):
    """Границы разделов NAMES/TERMS в нарезке по заголовкам.

    Раздел включает свои подразделы: `## NAMES` кончается не на `### Люди`,
    а на следующем заголовке того же или старшего уровня. Пока сведение
    видело только тело самого `## NAMES` — пустое, потому что имена лежали
    в подразделах, — свои строки оставались невидимы, и весь канон
    дописывался заново: 360 строк на живой книге.

    Возвращает список (kind, [индексы тел в parts]).
    """
    out, cur, kind, depth = [], None, None, 0
    for i_ in range(1, len(parts), 2):
        level = len(parts[i_]) - len(parts[i_].lstrip("#"))
        m = re.match(r"#{1,4}\s*(NAMES|ИМЕНА|TERMS|ТЕРМИН)", parts[i_], re.I)
        if cur is not None and level <= depth:
            out.append((kind, cur))
            cur = None
        if m:
            kind = ("NAMES" if m.group(1).upper() in ("NAMES", "ИМЕНА")
                    else "TERMS")
            cur, depth = [i_ + 1], level
        elif cur is not None:
            cur.append(i_ + 1)
    if cur is not None:
        out.append((kind, cur))
    return out


def _cycle_canon(paths, to, log=None):
    """Канон цикла: строки NAMES/TERMS и выходные данные прежних книг.

    Повтор имени остаётся за самой ранней книгой — по правилу старшинства:
    написание, которое читатель встретил в вышедшей раньше книге, менять
    нельзя. Ключ раздела латинский, поэтому разделы находятся на любом
    целевом языке.
    """
    rows, meta = {}, {}
    for p in paths:
        path = p
        if os.path.isdir(p):
            path = lpath(p, "scout.md", to)
        elif not p.endswith(".md"):
            path = lpath(os.path.splitext(p)[0] + ".work", "scout.md", to)
        if not os.path.isfile(path):
            if log:
                log("  " + T("like_none", p))
            continue
        parts = re.split(r"(?m)^(#{1,4}\s.*)$",
                         open(path, encoding="utf-8").read())
        for kind, bodies in _domains(parts):
            for at in bodies:
                for line in parts[at].split("\n"):
                    k = _name_key(line)
                    if k and k not in rows:
                        rows[k] = (line.rstrip(), kind)
        sm = scout_meta(os.path.dirname(path) or ".", "")
        for key in ("author_target", "series_target"):
            if sm.get(key) and key not in meta:
                meta[key] = sm[key]
    return rows, meta


def _no_shrink_path(out_path):
    """Путь кэша «дожать не вышло» рядом со справочником.

    Имя выводится из пути справочника: у него уже стоит суффикс языка,
    и разойтись эти два файла не должны.
    """
    root = os.path.splitext(out_path)[0]                  # …/scout_ru
    head, _, suffix = os.path.basename(root).partition("_")
    return os.path.join(
        os.path.dirname(root),
        f"{head}.no_shrink" + (f"_{suffix}" if suffix else "") + ".json")


def cycle_merge(work, likes, to, blocks, log=None):
    """Сведение имён цикла — после разведки, кодом и без моделей.

    Прежде канон уезжал в промпт разведки просьбой, и модель была вольна её
    игнорировать: на живой книге разведка молча переименовала заглавие при
    живом каноне, а бюджет промпта обрезал цикл на второй книге из четырёх.
    Теперь разведка идёт чистой, а канон принуждается здесь: строка
    справочника, чьё имя знают прежние книги, заменяется их строкой; имя,
    живущее в тексте книги, но пропущенное разведкой, дописывается. Бюджета
    нет — в справочник попадает только то, что есть в текущем тексте.

    Замена — только по точному ключу. Подмножество слов лишь гасит
    дописывание («Poole» уже есть — «Michael Poole» не дописывается), а
    заменять по нему нельзя: на живой книге оно подменило «Michael Poole
    Bazalget» (другого персонажа) Пулом и убило «drone = трутень» каноном
    «antibody drone» из чужой книги. Дописывается имя только по полной
    фразе ключа в тексте: правило «хватит любого слова» тащило канон
    целиком — у терминов слова обычные.
    """
    if not likes:
        return
    sp = lpath(work, "scout.md", to)
    if not os.path.isfile(sp):
        return
    rows, meta = _cycle_canon(likes, to, log)
    if not rows and not meta:
        return
    txt = open(sp, encoding="utf-8").read()
    parts = re.split(r"(?m)^(#{1,4}\s.*)$", txt)
    body_text = re.sub(r"\s+", " ", " ".join(b["text"] for b in blocks))

    def in_book(row):
        # Полная фраза ключа, в исходном регистре: «Sheen» с прописной — имя,
        # «sheen» строчными — блик на коже, и правило без регистра тащило
        # в справочник термины чужих книг по случайным словам прозы.
        m = re.match(r"\s*\|?\s*([^|=]+?)\s*[|=]", row)
        key = re.sub(r"[\s*_`]+", " ", m.group(1)).strip() if m else ""
        # Дописываются только имена собственные — в ключе есть заглавная.
        # Строчные общие слова («star», «bus») у каждой книги цикла свои:
        # правило без этого тащило «звезду в милю поперечником» из одного
        # мира в справочник другого.
        if not key or not any(ch.isupper() for ch in key):
            return False
        return re.search(
            rf"(?<![^\W\d_]){re.escape(key)}(?![^\W\d_])",
            body_text) is not None

    swapped = added = 0
    seen, tail = set(), {}
    for kind, bodies in _domains(parts):
        tail.setdefault(kind, bodies[-1])
        for at in bodies:
            out = []
            for line in parts[at].split("\n"):
                k = _name_key(line)
                if k in rows:
                    seen.add(k)
                    if line.strip() != rows[k][0].strip():
                        swapped += 1
                        line = rows[k][0]
                elif k:
                    for rk in rows:
                        aw, bw = set(k.split()), set(rk.split())
                        if aw <= bw or bw <= aw:
                            seen.add(rk)
                out.append(line)
            parts[at] = "\n".join(out)
    for kind, at in tail.items():
        extra = [rv[0] for rk, rv in rows.items()
                 if rk not in seen and rv[1] == kind and in_book(rv[0])]
        if extra:
            parts[at] = parts[at].rstrip("\n") + "\n" + "\n".join(extra) + "\n\n"
            added += len(extra)
    txt2 = parts[0] + "".join(parts[i_] + parts[i_ + 1]
                              for i_ in range(1, len(parts), 2))
    # Автор и цикл живут в META и в имена не входят, а разойтись им проще
    # всего: на третьей книге цикла автор вышел «Victoria» против
    # «Viktoria» в двух первых.
    for key in ("author_target", "series_target"):
        val = meta.get(key)
        if not val:
            continue
        pat = re.compile(rf"(?m)^({key}\s*=\s*)(.*)$")
        m2 = pat.search(txt2)
        if m2:
            if m2.group(2).strip() != val:
                txt2 = pat.sub(lambda mm: mm.group(1) + val, txt2, count=1)
                swapped += 1
        else:
            tm = re.search(r"(?m)^title_target\s*=.*$", txt2)
            if tm:
                txt2 = txt2[:tm.end()] + f"\n{key} = {val}" + txt2[tm.end():]
                added += 1
    if txt2 != txt:
        # Сведение переписывает справочник — единственный экземпляр ручной
        # и машинной работы разом. Копия обходится в ничто, а спасает всё.
        open(sp + ".bak", "w", encoding="utf-8").write(txt)
        open(sp, "w", encoding="utf-8").write(txt2)
        # Кэш «дожать не вышло» равняется на новый размер. Без этого
        # пересжатие и сведение играли в пинг-понг: пересжатие выбрасывает
        # строки имён цикла, сведение их возвращает, размер меняется — и
        # каждый запуск заново платил три запроса за то же сжатие.
        # Рост после сведения тем самым принимается насовсем — сознательно:
        # канон — это строки таблиц, их пересжатие и так бережёт, и новая
        # попытка стоила бы денег, не имея что резать.
        nsp = _no_shrink_path(sp)
        if os.path.exists(nsp):
            try:
                got = json.load(open(nsp, encoding="utf-8"))
                if not isinstance(got, dict):
                    got = {m: None for m in got}
                json.dump({m: len(txt2) for m in got},
                          open(nsp, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception:
                pass
    if log and (swapped or added):
        log("  " + T("cycle_merged", swapped, added))


def _merge_batches(findings, limit=MERGE_INPUT):
    """Пачки разборов для сведения, каждая в пределах входа транспорта.

    Жадно и с сохранением порядка: части книги идут по сюжету, и сводить
    соседей полезнее, чем случайных. Разбор, в одиночку превышающий предел,
    получает пачку из самого себя — сведение такую пропустит как есть.
    """
    batches, cur, cw = [], [], 0
    for f in findings:
        if cur and cw + len(f) > limit:
            batches.append(cur)
            cur, cw = [], 0
        cur.append(f)
        cw += len(f)
    if cur:
        batches.append(cur)
    return batches


def _registry(findings):
    """Реестр строк из разборов частей: сведение кодом, без потерь.

    Пирамида сведения пересжимала строки к бюджету на каждом уровне, и на
    большой книге второстепенные имена выпадали до того, как становилось
    известно, нужны ли они. Строки сводит код: потерь нет, а модель зовут
    только к разноголосице — см. _settle_rows.

    Возвращает (порядок, группы, заголовки): порядок — (раздел, нормальный
    ключ) по первому появлению; группы — там же варианты (номер части,
    строка) без дословных повторов; заголовки — первая шапка раздела.
    """
    order, groups, heads = [], {}, {}
    for i, f in enumerate(findings, 1):
        # Разделы без решёток — обычное дело у моделей; без поправки все
        # строки такого разбора остались бы без раздела.
        for sec, head, key, line, kind in _ref_scan(_headify(f)):
            if (kind == "head" and sec and sec not in heads
                    and re.match(r"#{1,4}\s*" + sec, head.strip())):
                heads[sec] = "## " + head.lstrip("# ").strip()
            if kind != "row":
                continue
            gk = (sec, _norm_key(key))
            if gk not in groups:
                groups[gk] = []
                order.append((gk, key, head))
            var = " ".join(line.split())
            if all(var != v for _, v in groups[gk]):
                groups[gk].append((i, var))
    # Двойники персонажей: один герой под двумя ключами — «Рой; Тейлор» и
    # «Рой; Тейлор Эберт», — и обе карточки ехали в каждый его кусок.
    # Совпавшая часть ключа — тот же псевдоним, то есть тот же герой:
    # группы сливаются в раннюю, разноголосицу сведёт _settle_rows.
    # Только персонажи: у терминов общая часть законна («duo / trio»).
    # И только по частям с буквами оригинала: переводное имя-одиночка
    # («Сара») совпадает у разных людей — на живой книге правило без этой
    # оговорки склеило героиню с тёзкой из другой семьи.
    owner = {}
    for gk, key, _ in order:
        if gk[0] != "CHARACTERS" or not groups[gk]:
            continue
        parts = [p for p in gk[1].split(" / ")
                 if len(p) >= 3 and re.search(r"[a-z]", p)]
        dst = next((owner[p] for p in parts if p in owner), None)
        if dst is not None and dst != gk:
            for var in groups[gk]:
                if all(var[1] != v for _, v in groups[dst]):
                    groups[dst].append(var)
            groups[gk] = []
            gk = dst
        for p in parts:
            owner[p] = gk
    return order, groups, heads


def _render_registry(order, groups, heads):
    """Реестр в хвост справочника: разделы в устойчивом порядке, строки —
    в порядке первого появления. Подразделы не переносятся: строка реестра
    самодостаточна, а одинаковых подразделов у разных частей не бывает."""
    out = []
    for sec in REF_KEYED:
        mine = [gk for gk, _, _ in order if gk[0] == sec]
        if mine:
            out += ["", heads.get(sec, f"## {sec}"), ""]
            for gk in mine:
                out += [v for _, v in groups[gk]]
    stray = [(gk, head) for gk, _, head in order
             if gk[0] not in REF_KEYED]
    last = None
    for gk, head in stray:
        if head != last:
            out += ["", head or "## REF", ""]
            last = head
        out += [v for _, v in groups[gk]]
    return "\n".join(out).strip()


def _settle_rows(conflicts, total, who, system, retries, log):
    """Свести разноголосицу реестра: моделью и только спорные ключи.

    conflicts: [(гк, ключ, [(часть, строка), …]), …]. Варианты подписаны
    своими разборами — по подписям модель датирует перемены. Не свелось —
    остаётся вариант самой ранней части, по тому же правилу старшинства,
    что у имён цикла.
    """
    task = lang.prompt("scout_rows")[0]
    box = lang.prompt("box_scout_rows")[0]
    label = lang.prompt("scout_label")[0]
    got = {}

    def flush(batch):
        log("  " + T("scout_rows", len(batch)), end="")
        body = "\n\n".join(
            "\n".join([f"### {key}"]
                      + [label.format(i=p, n=total) + " " + v
                         for p, v in vs])
            for _, key, vs in batch)
        prompt = boxed(task + "\n\n---\n\n" + body, "SCOUT", box)
        try:
            (res, _), meta, dt = _chain_run(who, system, prompt, retries,
                                            _parse_scout, log)
        except Exception:
            log(T("scout_rows_kept"))
            return
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))
        for line in res.splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            nk = _norm_key(_line_key(t))
            if not nk:
                continue
            for gk, _, _ in batch:
                if gk not in got and gk[1] == nk:
                    got[gk] = " ".join(t.split())
                    break

    batch, size = [], 0
    for gk, key, vs in conflicts:
        piece = sum(len(v) for _, v in vs) + len(key) + 40 * len(vs)
        if batch and size + piece > MERGE_INPUT:
            flush(batch)
            batch, size = [], 0
        batch.append((gk, key, vs))
        size += piece
    if batch:
        flush(batch)
    return got


def scout(work, blocks, agent, system, task, retries, log, to='ru',
          hints=None, fallback=None, likes=None, jobs=1):
    """Крупноблочный проход ДО перевода.

    Собирает голоса персонажей, имена собственные и повторяющиеся термины.
    Дёшево: на вход идут десятки тысяч слов, на выходе — короткий разбор.
    Результат — work/scout.md о двух ярусах: реестр строк, сведённый кодом
    без потерь (едет с куском по упоминанию), и костяк прозы, сведённый
    пирамидой (уходит в системный промпт каждого запроса). Решения по
    именам и интонациям принимаются один раз на всю книгу, а не заново в
    каждом куске.
    """
    out_path = lpath(work, "scout.md", to)
    who = [agent] + _backups(fallback)
    if os.path.exists(out_path):
        log("  " + T("scout_done_already"))
        # Готовый справочник всё равно проверяем на двоящиеся термины: он мог
        # быть собран прежней версией, а платить за разведку заново незачем.
        raw = open(out_path, encoding="utf-8").read()
        merged = _headify(raw)
        if merged != raw:
            open(out_path, "w", encoding="utf-8").write(merged)
        merged = _condense_scout(merged, who, "", retries, log, out_path)
        forked = _forked(merged, to)
        if forked:
            merged = _unfork(merged, forked, who, "", retries, log, out_path)
        return merged

    paras = [b for b in blocks if b["kind"] in ("p", "title")]
    parts, cur, cw = [], [], 0
    # Глава, внутри которой начинается часть. Часть, стартующая посреди
    # главы, своего заголовка не видит — а перемены в справочнике датируются
    # главами, и без этой подсказки началу части нечем их пометить.
    starts, last_title = [], ""
    for b in paras:
        if not cur:
            starts.append("" if b["kind"] == "title" else last_title)
        cur.append(b)
        if b["kind"] == "title":
            last_title = strip(b["text"])
        cw += words(b["text"])
        if cw >= SCOUT_WORDS:
            parts.append(cur)
            cur, cw = [], 0
    if cur:
        parts.append(cur)

    half = lpath(work, "scout.part.json", to)
    mfile = lpath(work, "scout.merge.json", to)
    if os.path.exists(half):
        findings = json.load(open(half, encoding="utf-8"))
    else:
        findings = []

    # Канон цикла для частей. Целиком канон в промпт уже пробовали — бюджет
    # резал его на второй книге цикла (см. cycle_merge, он принуждает таблицы
    # кодом). Здесь другое и дешёвое: в часть едут только строки, чьи имена
    # в ней встречаются, — чтобы проза разделов (голоса, род, обращения)
    # рождалась с каноническим написанием, а не со своей транслитерацией.
    canon_rows = []
    if likes:
        canon_rows = [(k, line) for k, (line, _) in
                      _cycle_canon(likes, to)[0].items()]

    # Канон своих же частей: имя, рождённое в части 1, приезжает в часть 5
    # тем же механизмом, что канон цикла. Первенство за ранней частью, а
    # строки цикла главнее: их написания читатель прежних книг уже видел.
    liked = {_norm_key(k) for k, _ in canon_rows}
    known = {}

    def _learn(res):
        for sec, _, key, line, kind in _ref_scan(_headify(res)):
            gk = (sec, _norm_key(key))
            if kind == "row" and gk[1] and gk[1] not in liked:
                known.setdefault(gk, (key, line))

    for f in findings:
        if f:                 # в кэше волн место неразобранной части — null
            _learn(f)

    # После рестарта разборы и сведение могут быть целиком в кэше — тогда
    # циклы ниже не делают ни одного запроса, и модели для scout.json нет.
    meta = {}
    if len(findings) < len(parts):
        findings += [None] * (len(parts) - len(findings))
    pend = [i for i in range(1, len(parts) + 1) if not findings[i - 1]]
    lock = threading.Lock()

    def _ask(i):
        nonlocal meta
        part = parts[i - 1]
        text = "\n\n".join(strip(b["text"]) for b in part)
        # Имя исходного файла и метаданные книги — мощнейшие подсказки для старта.
        hint = ""
        if hints and i == 1:
            if hints.get("filename"):
                hint_filename, _ = lang.prompt("scout_hint_filename")
                hint += "\n\n" + hint_filename.format(filename=hints["filename"])

            meta_lines = []
            m = hints.get("meta") or {}
            if m.get("title"): meta_lines.append(f"title: {m['title']}")
            if m.get("author"): meta_lines.append(f"author: {m['author']}")
            if m.get("description"): meta_lines.append(f"description: {m['description']}")
            if m.get("genre"): meta_lines.append(f"genre: {m['genre']}")
            if m.get("series"): meta_lines.append(f"series: {m['series']}")
            if m.get("series_no"): meta_lines.append(f"series_no: {m['series_no']}")

            if meta_lines:
                hint_meta, _ = lang.prompt("scout_hint_meta")
                hint += "\n\n" + hint_meta.format(meta="\n".join(meta_lines))

        pairs = canon_rows + list(known.values())
        crows = ref_rows_for(pairs, text)
        if sum(len(c) + 1 for c in crows) > SCOUT_CANON_BUDGET:
            cut = ref_rows_for(pairs, text, SCOUT_CANON_BUDGET)
            with lock:
                log("    " + T("canon_trim", len(cut), len(crows)))
            crows = cut
        canon = ("\n\n" + lang.prompt("scout_canon")[0] + "\n\n"
                 + "\n".join(crows)) if crows else ""
        at = (lang.prompt("scout_part_at")[0].format(chapter=starts[i - 1])
              if starts[i - 1] else "")
        prompt = boxed(f"{task}{hint}{canon}\n\n---\n\n"
                       + lang.prompt("scout_part")[0].format(i=i, n=len(parts),
                                                             at=at)
                       + f"\n\n{text}",
                       "SCOUT", lang.prompt("box_scout_part")[0])
        wc = f"{sum(words(b['text']) for b in part):6d}"
        if jobs <= 1:
            log("  " + T("scout_block", i, len(parts), wc), end="")
        (res, _), m, dt = _chain_run(who, system, prompt, retries,
                                     _parse_scout, log)
        # Подпись части остаётся в разборе до сведения: пачки пирамиды друг
        # друга не видят, и противоречие состояний («женщина» в части 1,
        # «мужчина» в части 5) сведение датирует именно этими подписями.
        if len(parts) > 1:
            res = (lang.prompt("scout_label")[0].format(i=i, n=len(parts))
                   + "\n\n" + res)
        cost = f", ${m['cost_usd']:.2f}" if m.get("cost_usd") else ""
        with lock:
            meta = m
            findings[i - 1] = res
            json.dump(findings, open(mkparent(half), "w", encoding="utf-8"),
                      ensure_ascii=False)
            took = T("took", f"{dt:.0f}", f"{m['model']}{cost}")
            log(took if jobs <= 1
                else "  " + T("scout_block", i, len(parts), wc) + took)

    # Части почти независимы, и разбор идёт волнами по --scout-jobs
    # (по умолчанию 1 — последовательно). Канон своих частей обновляется на
    # границе волн: внутри волны части друг друга не видят; разноголосицу с
    # совпавшим ключом сводит _settle_rows, а разошедшиеся формы ключа —
    # честная цена параллельности, потому она и выключена по умолчанию.
    step = max(jobs, 1)
    for base in range(0, len(pend), step):
        wave = pend[base:base + step]
        if len(wave) == 1:
            _ask(wave[0])
        else:
            with cf.ThreadPoolExecutor(max_workers=step) as ex:
                err = None
                for f in cf.as_completed([ex.submit(_ask, i) for i in wave]):
                    err = err or f.exception()
            if err:
                raise err
        for i in wave:
            if findings[i - 1]:
                _learn(findings[i - 1])

    # Реестр сводится кодом и без потерь — до пирамиды и по исходным
    # разборам. Моделью решается только разноголосица: одному ключу разные
    # части дали разные строки.
    order, groups, heads_map = _registry(findings)
    conflicts = [(gk, key, groups[gk]) for gk, key, _ in order
                 if len(groups[gk]) > 1]
    if conflicts:
        got = _settle_rows(conflicts, len(parts), who, system, retries, log)
        for gk, line in got.items():
            groups[gk] = [(0, line)]
        for gk, key, vs in conflicts:
            if gk not in got:           # не свелось — вариант ранней части
                groups[gk] = vs[:1]

    if len(findings) > 1:
        merge_prompt, _ = lang.prompt("scout_merge")
        # Пирамида сводит только прозу: строки реестра уже сведены кодом, и
        # возить их через модель — терять. Все разборы одним запросом не
        # отправить: у транспорта есть предел входа, и режет он молча — agy
        # отдаёт модели ~50 тыс. токенов, модель получает обрубок и отвечает
        # пустотой. Поэтому проза сводится пачками в пределах MERGE_INPUT,
        # затем сводятся результаты пачек — и так до одного, сколько бы
        # книга ни весила.
        #
        # Каждая сведённая пачка тут же ложится на диск: пачка стоит минут и
        # денег, а прерванный прогон начинал пирамиду с нуля. После рестарта
        # уцелевшие результаты группируются заново — дерево сведения выйдет
        # другим, но сведёт то же самое.
        frames = [split_ref(_headify(f))[0] for f in findings]
        if os.path.exists(mfile):
            frames = [split_ref(_headify(x))[0] for x in
                      json.load(open(mfile, encoding="utf-8"))]
        while len(frames) > 1:
            batches = _merge_batches(frames)
            if len(batches) == len(frames):
                # каждый разбор — пачка сам по себе: группировать нечем,
                # сводим единым запросом, как раньше, а не крутимся вечно
                batches = [frames]
            nxt = []
            for j, batch in enumerate(batches, 1):
                if len(batch) == 1:
                    nxt.append(batch[0])
                    continue
                log("  " + (T("scout_merge_part", j, len(batches))
                            if len(batches) > 1 else T("scout_merge")), end="")
                merge = boxed(merge_prompt.format(budget=SCOUT_BUDGET,
                                                  parts=len(batch))
                              + "\n\n---\n\n" + "\n\n---\n\n".join(batch),
                              "SCOUT", lang.prompt("box_scout_merge")[0])
                (m, _), meta, dt = _chain_run(who, system, merge, retries,
                                              _parse_scout, log)
                cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
                log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))
                nxt.append(m)
                json.dump(nxt + [f for b in batches[j:] for f in b],
                          open(mkparent(mfile), "w", encoding="utf-8"),
                          ensure_ascii=False)
            frames = nxt
        merged = frames[0]
    else:
        merged = split_ref(_headify(findings[0]))[0] if findings else ""

    # Костяк — проза пирамиды; строки, которые модель всё же вернула,
    # вычёркиваются: их полный свод — реестр — дописывается следом.
    merged = split_ref(_headify(merged))[0]
    registry = _render_registry(order, groups, heads_map)
    if registry:
        merged = merged.rstrip() + "\n\n" + registry + "\n"
    open(mkparent(out_path), "w", encoding="utf-8").write(merged)
    # Модель разведки нигде больше не записана, а в книге её надо назвать.
    json.dump({"model": meta.get("model")},
              open(mkparent(lpath(work, "scout.json", to)), "w", encoding="utf-8"),
              ensure_ascii=False)
    log("  " + T("scout_ref", out_path, len(merged)))
    merged = _condense_scout(merged, who, "", retries, log, out_path)

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
        merged = _unfork(merged, forked, who, "", retries, log, out_path)
        forked = _forked(merged, to)
    if forked:
        log("  " + T("scout_forked", len(forked)))
        for _, line in forked[:8]:
            log(f"      {line}")
    for cache in (half, mfile):
        if os.path.exists(cache):
            os.unlink(cache)
    return merged


def _rows(text):
    """Строки таблиц справочника. По ним видно, не выпало ли при сжатии
    имя или термин: их строк должно стать не меньше.

    Ключ — первая ячейка, приведённая к общему виду: пробелы, выделение и
    регистр строку не меняют. Иначе модель, ужимая пояснение, заодно ставит
    имя полужирным — и сотня строк засчитывается потерянной, хотя ни одна
    никуда не делась.
    """
    return {re.sub(r"[\s*`_~]+", "", line.split("|")[1]).lower()
            for line in text.splitlines()
            if line.startswith("|") and line.count("|") > 2 and "---" not in line}




def _floor(part, over):
    """До скольких знаков позволено ужать за один заход.

    Мера берётся по прозе: строки таблиц стережёт отдельная проверка, и
    урезать их заданием мы не просим вовсе, а проза уходит на две трети.
    Прежде мера бралась от всего текста разом — и справочник, где две трети
    занимают биографии, а треть таблицы имён, запрещалось ужимать больше чем
    на треть. Тогда не сходилась сама арифметика: даже послушайся модель
    дословно, он остался бы в полтора предела.
    """
    tbl = sum(len(l) + 1 for l in part.splitlines() if l.startswith("|"))
    prose = max(len(part) - tbl, 0)
    return max(len(part) - over, tbl + prose // 3)


def _text_only():
    """Система для проходов, которым обычный системный промпт не положен.

    Пересжатию и расфуркам он не достаётся нарочно — в нём лежит сам
    справочник (см. _condense_scout). Но совсем пустая система ломает
    агентные обёртки: flash через agy на «ужми это» пять раз подряд вернул
    пустой ответ, а с одной пристёгивающей строкой отдал сжатие с первого
    захода. Строка задаёт только форму ответа и о содержании не говорит
    ничего, так что довод «модель перепишет всё» ею не задет.
    """
    return lang.prompt("text_only")[0]


def _condense_scout(merged, who, system, retries, log, out_path):
    """Пересжать костяк справочника, если он перерос предел. Реестр не
    трогается: его строки едут с куском по упоминанию и бюджета не тратят.

    Системный промпт сюда идёт пустой, и это важно. В обычном промпте лежит
    сам справочник целиком — он уходит в каждый запрос на перевод, — и,
    получив его вместе с заданием «ужми это», модель считает себя вправе
    переписать всё. На живой книге она так и сделала: раздел на 27 887 знаков
    вернулся четырьмя новыми разделами — содержание уцелело, но следующему
    проходу досталось вдвое больше разделов, а значит и запросов.
    «Пустой» — это без справочника; форму ответа держит промпт `text_only`,
    без него агентная обёртка отвечает пустотой.

    Просьба «уложиться в столько-то знаков» стоит и в промпте сведения, но
    исполняется там плохо: на одной книге вышло полтора предела. Отдельный
    запрос делает ровно одно дело и потому справляется.

    Справочник идёт целиком, одним запросом на заход. По разделам было хуже
    сразу с двух сторон: запрос на каждый раздел — это десяток запросов вместо
    трёх, а главное, раздел не видит соседей и потому не может убрать то, что
    повторяется в двух местах. Прежний довод за деление — «неудача на одном
    разделе не отменяет успеха на других» — держался на том, что запрос был
    дорог: справочник уезжал на вход по разу на каждый. Теперь промпт пуст,
    отвергнутый заход стоит одного запроса, и «всё или ничего» по карману.

    Заходов до трёх: модель за раз отдаёт меньше, чем просят, и уступает
    постепенно. Останавливаемся, как только уложились или очередной заход не
    дал ничего.
    """
    # Бюджет меряется по костяку: в каждый запрос идёт только он, а реестр
    # едет с куском по упоминанию, и ужимать его — терять строки.
    frame, _ = split_ref(merged)
    if len(frame) <= SCOUT_MAX:
        return merged

    no_shrink_path = _no_shrink_path(out_path)
    # Кэш «дожать не вышло» хранит и достигнутый размер: модель, остановившаяся
    # на 32 602 знаках, при следующем прогоне видела те же 32 602 и честно
    # начинала заново — с тем же исходом и за те же деньги. Пока справочник не
    # изменился (размер тот же), ту же модель не переспрашиваем; правка руками
    # или новая разведка меняют размер — и попытка снова разрешена.
    failed_models = {}
    if os.path.exists(no_shrink_path):
        try:
            got = json.load(open(no_shrink_path, encoding="utf-8"))
            failed_models = got if isinstance(got, dict) else {m: None for m in got}
        except Exception:
            pass
    primary_model = getattr(who[0], "model", "?")
    reached = failed_models.get(primary_model, -1)
    if primary_model in failed_models and reached in (None, len(frame)):
        log("  " + T("scout_big", out_path))
        return merged

    log("  " + T("scout_condense", len(frame), SCOUT_BUDGET), end="")
    t0, cost, model, now = time.time(), 0.0, "?", frame
    for _ in range(SCOUT_ROUNDS):
        if len(now) <= SCOUT_BUDGET:
            break
        short, meta, why = _shrink(now, _floor(now, len(now) - SCOUT_BUDGET),
                                   who, system, retries, log)
        cost += meta.get("cost_usd") or 0
        model = meta.get("model") or model
        if not short:
            log("\n    " + T("shr_no", why), end="")
            break
        log("\n    " + T("shr_ok", len(now), len(short),
                         meta.get("model") or "?"), end="")
        now = short
    log("\n  " + T("took", f"{time.time() - t0:.0f}",
                  model + (f", ${cost:.2f}" if cost else "")))
    if now == frame:
        log("  " + T("scout_condense_no", len(_rows(merged)), len(_rows(merged))))
        log("  " + T("scout_big", out_path))
        if model != "?":
            failed_models[model] = len(frame)
            json.dump(failed_models, open(mkparent(no_shrink_path), "w",
                                          encoding="utf-8"), ensure_ascii=False)
        return merged
    registry = _render_registry(*_registry([merged]))
    now = (now.rstrip() + "\n\n" + registry + "\n") if registry else now
    # Продвинулись, но до предела не дожали — это тоже «не вышло»: без записи
    # следующий прогон начинал то же сжатие заново. Записывается мера костяка
    # уже собранного файла — её и увидит повторный вход.
    reached_now = len(split_ref(now)[0])
    if reached_now > SCOUT_BUDGET and model != "?":
        failed_models[model] = reached_now
        json.dump(failed_models, open(mkparent(no_shrink_path), "w",
                                      encoding="utf-8"), ensure_ascii=False)
    open(mkparent(out_path), "w", encoding="utf-8").write(now)
    log("  " + T("scout_condense_ok", len(merged), len(now),
                 len(_rows(merged)), len(_rows(now))))
    if len(split_ref(now)[0]) > SCOUT_MAX:
        log("  " + T("scout_big", out_path))
    return now


def _shrink(part, want, who, system, retries, log):
    """Ужать справочник. Вернуть (сжатое, meta, причина отказа).

    Строки таблиц прежде сверялись с исходными: пропало больше трети — ответ
    отвергался. Проверка снята, потому что спорила с самим заданием. Первой
    ступенью лестницы стоит «выбросить очевидное», а очевидное — это как раз
    строки таблиц: «Aldous Huxley = Олдос Хаксли» переводчик передаст и без
    подсказки. На живой книге модель выбросила 45 строк из 107, и это была
    работа по заданию, а не порча, — но ответ отвергался целиком.

    Остаётся мера по длине: ответ короче половины запрошенного — это не
    сжатие, а выброшенный справочник.
    """
    ask_prompt, _ = lang.prompt("scout_condense")
    ask = boxed(ask_prompt.format(len_part=len(part), want=want)
                + "\n\n---\n\n" + part,
                "SHRINK", lang.prompt("box_shrink")[0])
    (short, _), meta, _dt = _chain_run(who, system or _text_only(), ask, retries,
                                       lambda o: (unbox(o, "SHRINK"), ""),
                                       log)

    # Приписанное перед разделом отрезаем по его заголовку. Замерено: один
    # прогон вернул справочник, а перед ним — весь системный промпт целиком,
    # девять тысяч знаков. Снаружи конверта такое теперь и так отброшено, а
    # эта строка снимает то, что модель припишет внутри него.
    head = next((l for l in part.splitlines() if l.startswith("#")), "")
    if head and head in short:
        short = short[short.index(head):]

    # Сжатие принимаем не на слово, и стеречь надо с двух сторон. Вычеркнуть
    # таблицу целиком — самый простой способ уложиться в предел, и обнаружилось
    # бы это уже в переводе. А в разделе без таблиц стеречь вовсе нечего: там
    # мерой служит сама длина, потому что ответ короче половины запрошенного —
    # это не сжатие, а выброшенный раздел.
    if len(short) >= len(part):
        return None, meta, T("shr_long", len(short))
    if len(short) < want / 2:
        return None, meta, T("shr_short", len(short), want)
    return short, meta, ""


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
    ask_prompt, _ = lang.prompt("scout_oneterm")
    ask = ask_prompt + "\n\n" + "\n".join(line for _, line in forked)
    (res, _), meta, dt = _chain_run(who, system or _text_only(), ask, retries,
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
    out.append(lang.prompt("scout_oneterm_apply")[0])
    out.append("")
    out += [f"- {x}" for x in done]
    merged = "\n".join(out)
    open(mkparent(out_path), "w", encoding="utf-8").write(_headify(merged))
    for x in done:
        log(f"      {x}")
    return merged


# ---------------------------------------------------------------- заголовки

def headings(work, blocks, agent, system, retries, log, fallback=None, to=""):
    """Заголовки и подзаголовки — одним запросом на всю книгу.

    Их немного (десятки), и они обязаны совпадать дословно между собой:
    десять вхождений одного имени рассказчика не должны выглядеть по-разному.
    Отдельно от прозы ещё и потому, что короткая строка в начале запроса
    принимается моделью за шапку промпта и молча теряется.
    """
    path = lpath(work, "headings.json", to)
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
    
    if len(uniq) > HEAD_CHUNK:
        log("  " + T("heads_todo", len(uniq)))
    else:
        log("  " + T("heads_todo", len(uniq)), end="")

    prompt_tpl, _ = lang.prompt("headings")
    who = [agent] + _backups(fallback)
    total_dt = 0
    
    for start in range(0, len(uniq), HEAD_CHUNK):
        chunk = uniq[start:start + HEAD_CHUNK]
        if len(uniq) > HEAD_CHUNK:
            log(f"    {start+1}-{start+len(chunk)} ... ", end="")
            
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(chunk, 1))
        prompt = prompt_tpl + "\n\n## Заголовки\n\n" + listing

        def parse_heads(out):
            got = {}
            for m in re.finditer(r"^\s*(\d+)[.)]\s*(.+)$", out, re.M):
                i = int(m.group(1))
                if 1 <= i <= len(chunk):
                    got[chunk[i - 1]] = m.group(2).strip()
            missing = [t for t in chunk if t not in got]
            if missing:
                short = [re.sub(r"\s+", " ", t)[:70] + ("…" if len(t) > 70 else "")
                         for t in missing[:3]]
                raise ValueError(f"не переведено {len(missing)} заголовков: {short}")
            return got, ""

        (got, _), meta, dt = _chain_run(who, system, prompt, retries, parse_heads, log)
        have.update(got)
        json.dump(have, open(mkparent(path), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        total_dt += dt
        
        cost = f", ${meta['cost_usd']:.2f}" if meta.get("cost_usd") else ""
        if len(uniq) > HEAD_CHUNK:
            log(T("took", f"{dt:.0f}", f"{meta['model']}{cost}"))
            
    if len(uniq) <= HEAD_CHUNK:
        log(T("ready_in", f"{total_dt:.0f}", f"{meta['model']}{cost}"))
    return have


# ---------------------------------------------------------------- структура

def detect_structure(work, styles, agent, task, retries, log, fallback=None):
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
    styles_prompt, _ = lang.prompt("structure_styles")
    prompt = task + "\n\n---\n\n" + styles_prompt + "\n\n" + "\n".join(listing)
    # Запрос ложится к своему результату, в общую часть папки: стили книги
    # языка перевода не знают. Папку заводит эта же запись — общего
    # `prompts/` до неё нет ни у кого.
    open(mkparent(f"{work}/prompts/structure.txt"), "w",
         encoding="utf-8").write(prompt)

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

    (got, _), meta, dt = _chain_run([agent] + _backups(fallback),
                                    _text_only(), prompt,
                                    retries, parse_map, log)

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
