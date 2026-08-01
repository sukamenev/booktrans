"""Языки: правила целевого языка, локализация интерфейса, определение языка.

Добавить язык — положить файл в langs/ (правила перевода) и ui/ (сообщения).
Больше ничего менять не нужно: система читает их сама.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
}
LANG_SCRIPT = {
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic",
    "en": "latin", "de": "latin", "fr": "latin", "es": "latin",
    "it": "latin", "pl": "latin",
    "ja": "cjk", "zh": "cjk", "ko": "hangul",
}


def script_of(code):
    return LANG_SCRIPT.get((code or "")[:2])


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


# Ниже этой доли частотных слов ответ — совпадение случайное. В связном
# тексте служебные слова занимают 5-15%; один-два процента даёт шум
# в формулах, оглавлениях и в тексте, прочитанном в неверной кодировке.
MIN_SHARE = 0.025


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
