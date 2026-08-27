"""Языки: правила целевого языка, локализация интерфейса, определение языка.

Добавить язык — положить файл в langs/ (правила перевода) и ui/ (сообщения).
Больше ничего менять не нужно: система читает их сама.
"""
import json
import os
import re
from .tune import MIN_SHARE

HERE = os.path.dirname(os.path.abspath(__file__))

# Частотные короткие слова: по ним язык определяется надёжнее, чем по алфавиту,
# и работает для языков с общей письменностью.
MARKERS = {
    "ru": r"\b(и|в|не|на|что|он|она|как|это|то|же|бы|был|была|для|его|её|их|мы|вы)\b",
    "uk": r"\b(і|та|не|на|що|він|вона|як|це|для|його|її|їх|ми|ви|був|була)\b",
    "en": r"\b(the|and|of|to|in|that|it|is|was|for|with|as|his|her|they|you)\b",
    "de": r"\b(der|die|das|und|ist|nicht|ein|eine|zu|mit|sich|auf|für|von)\b",
    "fr": r"\b(le|la|les|et|de|des|que|il|elle|dans|pour|est|pas|une|un)\b",
    "es": r"\b(el|la|los|las|y|de|que|en|un|una|por|con|para|es|no)\b",
    "it": r"\b(il|la|le|e|di|che|in|un|una|per|con|non|è|sono)\b",
    "pl": r"\b(i|w|nie|na|że|to|się|jest|do|z|za|jak|ale|był)\b",
}


def available_langs():
    d = os.path.join(HERE, "langs")
    return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md")) if os.path.isdir(d) else []


def available_uis():
    d = os.path.join(HERE, "ui")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def rules(code):
    """Правила целевого языка. Нет файла — работаем без них, но предупреждаем."""
    p = os.path.join(HERE, "langs", f"{code}.md")
    if not os.path.exists(p):
        return None
    return open(p, encoding="utf-8").read()


def lang_name(code, in_ru=True):
    txt = rules(code) or ""
    m = re.search(r"^name{}:\s*(.+)$".format("" if in_ru else "_en"), txt, re.M)
    return m.group(1).strip() if m else code


# Какой письменностью пишет язык. Нужно, чтобы искать в переводе остатки
# оригинала: при переводе с русского на немецкий латиница — это норма,
# а при переводе на русский — недоработка.
SCRIPTS = {
    "latin": r"A-Za-z\u00c0-\u024f",
    "cyrillic": r"\u0400-\u04ff",
    "cjk": r"\u3040-\u30ff\u4e00-\u9fff",
    "hangul": r"\uac00-\ud7af",
    "devanagari": r"\u0900-\u097f",
}
LANG_SCRIPT = {
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic",
    "en": "latin", "de": "latin", "fr": "latin", "es": "latin",
    "it": "latin", "pl": "latin",
    "ja": "cjk", "zh": "cjk", "ko": "hangul",
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
}


def script_of(code):
    return LANG_SCRIPT.get((code or "")[:2])


# Числительные словом. «Turn the picture 45 degrees» по-русски пишется «на
# сорок пять градусов», и цифра честно исчезает: это не потеря, а норма
# языка. Проверка чисел без этого краснела на здоровой книге двумя десятками
# строк, и раздел переставали читать.
#
# Разряды перечислены по отдельности: число раскладывается на них и ищется по
# частям — «сорок» и «пять». Формы даны падежами, потому что в русском
# числительное склоняется, а обрубок вроде «дв» нашёлся бы и в «движении».
WORDNUM = {
    "ru": {
        # Второй вид — существительное: «вывел на доске семёрку». Так
        # по-русски называют сам знак, и цифра тут тоже не потеряна.
        1: r"од(?:ин|на|но|ну|ного|ним|ной)\b|единиц",
        2: r"дв(?:а|е|ух|ум|умя)\b|двойк",
        3: r"тр(?:и|ёх|ех|ём|ем|емя|ёмя)\b|тройк",
        4: r"четыр(?:е|ёх|ех|ём|ьмя)\b|четв(?:ё|е)рк",
        5: r"пят(?:ь|и|ью)\b|пят(?:ё|е)рк",
        6: r"шест(?:ь|и|ью)\b|шест(?:ё|е)рк",
        7: r"сем(?:ь|и|ью)\b|сем(?:ё|е)рк",
        8: r"вос(?:емь|ьми|емью)\b|восьм(?:ё|е)рк",
        9: r"девят(?:ь|и|ью)\b|девятк",
        10: r"десят(?:ь|и|ью)\b|десятк", 11: r"одиннадцат", 12: r"двенадцат",
        13: r"тринадцат", 14: r"четырнадцат", 15: r"пятнадцат",
        16: r"шестнадцат", 17: r"семнадцат", 18: r"восемнадцат",
        19: r"девятнадцат", 20: r"двадцат", 30: r"тридцат",
        40: r"сорок(?:а)?\b", 50: r"пят(?:ь|и)десят", 60: r"шест(?:ь|и)десят",
        70: r"сем(?:ь|и)десят", 80: r"вос(?:емь|ьми)десят",
        90: r"девяност(?:о|а)\b", 100: r"ст(?:о|а|у|ом)\b",
        200: r"двест|двухсот|двумст|двухст|двумяст",
        300: r"трист|тр(?:ё|е)хсот", 400: r"четырест|четыр(?:ё|е)хсот",
        500: r"пят(?:ь|и)сот", 600: r"шест(?:ь|и)сот", 700: r"сем(?:ь|и)сот",
        800: r"вос(?:емь|ьми)сот", 900: r"девят(?:ь|и)сот",
        1000: r"тысяч",
    },
    "en": {
        1: r"\bone\b", 2: r"\btwo\b", 3: r"\bthree\b", 4: r"\bfour\b",
        5: r"\bfive\b", 6: r"\bsix\b", 7: r"\bseven\b", 8: r"\beight\b",
        9: r"\bnine\b", 10: r"\bten\b", 11: r"\beleven\b", 12: r"\btwelve\b",
        13: r"\bthirteen\b", 14: r"\bfourteen\b", 15: r"\bfifteen\b",
        16: r"\bsixteen\b", 17: r"\bseventeen\b", 18: r"\beighteen\b",
        19: r"\bnineteen\b", 20: r"\btwenty\b", 30: r"\bthirty\b",
        40: r"\bforty\b", 50: r"\bfifty\b", 60: r"\bsixty\b",
        70: r"\bseventy\b", 80: r"\beighty\b", 90: r"\bninety\b",
        100: r"\bhundred\b", 1000: r"\bthousand\b",
    },
}
# Своя таблица есть не у всякого языка, и это не беда: без неё проверка
# просто скажет о числе, как говорила раньше.


def spelled_out(text, n, to):
    """Написано ли число `n` в тексте словом.

    Раскладываем на разряды и требуем все: «сорок пять» — это и «сорок», и
    «пять». Иначе одного «пять» в абзаце хватило бы, чтобы простить потерю
    сорока пяти.
    """
    table = WORDNUM.get((to or "")[:2])
    if not table:
        return False
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False
    if not 0 < n < 10000:
        return False

    def places(x):
        """Разряды числа: 45 → [40, 5], 160 → [100, 60]."""
        out = []
        for step in (100, 10, 1):
            cur = x // step * step
            if cur:
                out.append(cur)
                x -= cur
        return out

    parts = []
    if n >= 1000:
        if n % 1000:
            return False        # 1234 словом не пишут — не наш случай
        # «Одна тысяча» — не по-русски, тысяча и есть тысяча.
        parts += [p for p in places(n // 1000) if n // 1000 > 1] + [1000]
    else:
        parts = places(n)
    return all(p in table and re.search(table[p], text, re.I) for p in parts)


def book_strings(code):
    """Строки, которые читатель увидит внутри самой книги.

    Лежат в файле языка строками вида «str.ключ: значение». Русские берутся
    основой: если для нового языка перевели не всё, книга выйдет с русской
    вставкой, но не сломается.
    """
    def parse(txt):
        return dict(re.findall(r"^str\.(\w+):[ \t]*(.+)$", txt or "", re.M))
    out = parse(rules("ru"))
    if code != "ru":
        out.update(parse(rules(code)))
    return out


def fmt_date(ts, code):
    """Дата на целевом языке. Нет правил формата — пишем цифрами по ISO:
    цифры читаются одинаково везде."""
    import datetime
    d = datetime.datetime.fromtimestamp(ts)
    st = book_strings(code)
    fmt = st.get("date_fmt")
    if not fmt:
        return d.strftime("%Y-%m-%d")
    months = [m.strip() for m in st.get("months", "").split(",") if m.strip()]
    month = months[d.month - 1] if len(months) == 12 else str(d.month)
    return fmt.format(d=d.day, m=d.month, y=d.year, month=month)


class UI:
    """Сообщения интерфейса. Ключ, которого нет, возвращается как есть —
    так недоведённый перевод ломает вид, но не работу."""

    def __init__(self, code="ru"):
        p = os.path.join(HERE, "ui", f"{code}.json")
        if not os.path.exists(p):
            p = os.path.join(HERE, "ui", "ru.json")
        self.d = {k: v for k, v in json.load(open(p, encoding="utf-8")).items()
                  if not k.startswith("_")}

    def __call__(self, key, *args):
        s = self.d.get(key, key)
        try:
            return s.format(*args) if args else s
        except (IndexError, KeyError, ValueError):
            return s




# Языки, которые узнаются по письменности, а не по словам: японский и
# китайский пишутся без пробелов, и «частотное слово» там не выделить.
# Кана бывает только в японском; иероглифы без каны — китайский; хангыль — корейский.
KANA = r"[\u3040-\u30ff]"
HAN = r"[\u4e00-\u9fff]"
HANGUL = r"[\uac00-\ud7af]"


def _by_script(t):
    n = len(t) or 1
    hangul = len(re.findall(HANGUL, t)) / n
    if hangul > 0.15:
        return "ko", hangul
    kana = len(re.findall(KANA, t)) / n
    han = len(re.findall(HAN, t)) / n
    if kana > 0.04:                 # в связном японском кана — это частицы
        return "ja", kana + han
    if han > 0.15:
        return "zh", han
    return None, 0.0


def detect(text, sample=200000):
    """Какой язык у текста. Возвращает (код, доля совпадений).

    По частотным словам, а не по алфавиту: кириллицу делят русский, украинский
    и болгарский, латиницу — десяток языков. Когда уверенности нет, честно
    возвращаем None: неверно названный язык хуже, чем неназванный.
    """
    t = text[:sample].lower()
    code, share = _by_script(t)
    if code:
        return code, share
    words = len(re.findall(r"\w+", t)) or 1
    best, score = None, 0.0
    for code, pat in MARKERS.items():
        r = len(re.findall(pat, t)) / words
        if r > score:
            best, score = code, r
    return (best, score) if score >= MIN_SHARE else (None, score)


def target_share(blocks, code, sample=400):
    """Доля абзацев, уже написанных на целевом языке.

    Нужна, чтобы не переводить книгу саму в себя. Считается по абзацам, а не
    по всему тексту: в книге бывают иноязычные вставки, и важно, что
    преобладает.
    """
    texts = [re.sub(r"<[^>]+>", "", b["text"]) for b in blocks
             if b["kind"] in ("p", "verse") and len(b["text"]) > 60]
    if not texts:
        return 0.0
    step = max(1, len(texts) // sample)
    picked = texts[::step][:sample]
    hits = sum(1 for t in picked if detect(t)[0] == code)
    return hits / len(picked)


# Единый доступ к интерфейсным строкам. Язык задаётся один раз при запуске,
# дальше любой модуль зовёт T() и не тащит объект через свои сигнатуры.
_ui = None


def set_ui(code):
    global _ui
    _ui = UI(code)
    return _ui


def T(key, *args):
    global _ui
    if _ui is None:
        _ui = UI("ru")
    return _ui(key, *args)


from .tune import config_dir

PROMPTS = os.path.join(HERE, "prompts")
# Знаки протокола: по ним разбирается ответ модели. Перевести их — значит
# молча потерять сноски, конспект и список терминов, и увидеть это можно
# будет только на собранной книге. Поэтому перекрытый промпт сверяется с
# исходным: пропал знак — конвейер скажет об этом до первого запроса.
TOKENS = re.compile(r"\[\[\[[A-Z]+(?=[ \]])|^[A-Z]{2,10}:", re.M)


def prompt_roots():
    """Где искать промпты, от старшего к младшему.

    Папка пакета перезаписывается при обновлении, поэтому свои промпты кладут
    не в неё. `BOOKTRANS_PROMPTS` — для запуска из скрипта и «положил рядом с
    книгой», папка настроек — для того, что должно пережить обновление.
    """
    out = [os.environ.get("BOOKTRANS_PROMPTS"),
           os.path.join(config_dir(), "prompts"), PROMPTS]
    return [p for p in out if p]

# Прочитанное держится в памяти процесса. Не ради скорости: покусковые
# проходы читают файл при каждом куске, и правка рабочей копии — новая
# версия, другие поля шаблона — на живом прогоне меняла промпт под ногами
# у давно загруженного кода. Прогон работает с тем, что застал при первом
# чтении; правка промптов вступает в силу с перезапуска.
_prompts = {}


def prompt(name, to=None, roots=None):
    """Промпт прохода, с учётом перекрытий.

    Свои промпты кладут в папку по коду языка перевода:

        prompts/de/translate.md        вместо авторского
        prompts/de/translate.add.md    в дополнение к нему

    Файл `.add.md` приписывается к тому, что вышло, — им дополняют, не
    переписывая; такой же файл вне языковой папки действует на все языки.
    Замена берётся одна, старшая; дополнения собираются со всех папок, начиная
    с авторской, — иначе своё дополнение отменяло бы авторское.
    """
    roots = roots or prompt_roots()
    key = (name, to, tuple(roots))
    if key in _prompts:
        return _prompts[key]
    base = None
    for root in roots:
        for p in ([os.path.join(root, to, f"{name}.md")] if to else []) + \
                 [os.path.join(root, f"{name}.md")]:
            if os.path.exists(p):
                base = p
                break
        if base:
            break
    if base is None:
        raise SystemExit(f"нет промпта {name}.md ни в одной из папок: "
                         + ", ".join(roots))
    out = [open(base, encoding="utf-8").read()]
    for root in reversed(roots):
        for add in [os.path.join(root, f"{name}.add.md")] + \
                   ([os.path.join(root, to, f"{name}.add.md")] if to else []):
            if os.path.exists(add):
                out.append(open(add, encoding="utf-8").read())
    # «Своё» — всё, кроме авторского файла из последней папки: о нём человеку
    # сообщают, потому что дальше конвейер работает не по тому, что в пакете.
    own = base if base != os.path.join(roots[-1], f"{name}.md") else None
    _prompts[key] = ("\n\n".join(out), own)
    return _prompts[key]


def lost_tokens(name, over, root=PROMPTS):
    """Знаки протокола, пропавшие из перекрытого промпта."""
    was = set(TOKENS.findall(open(os.path.join(root, f"{name}.md"),
                                  encoding="utf-8").read()))
    return sorted(was - set(TOKENS.findall(over)))
