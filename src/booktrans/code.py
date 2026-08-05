"""Комментарии в листингах книг по программированию.

Код в переводе остаётся как есть: имена, отступы, строковые литералы. Строку
`print("Hello")` перевести нельзя — двумя абзацами ниже книга показывает, что
она печатает, и соответствие рассыплется. А комментарий — это проза,
написанная человеку, и в учебнике на ней держится половина объяснения.

Языков программирования сотни, и знак комментария у каждого свой: `#`, `//`,
`%`, `;`, `!`, `(* *)`. Разбором это не покрыть, поэтому комментарии ищет
модель — она их знает все. Но подставляет перевод не она, а этот модуль, и
только там, где сходится точно: названный моделью кусок обязан найтись в той
самой строке, и заменяется ровно он. Всё прочее в листинге не меняется
никогда — ни отступ, ни имя, ни литерал.

Там, где знак комментария нам всё же известен, названное моделью
дополнительно сверяется с ним: строковый литерал, выданный за комментарий,
так не пройдёт.
"""
import re

BLOCK = (("/*", "*/"), ("<!--", "-->"))
# Директивы препроцессора C и Си-подобных: `#include <stdio.h>` — не комментарий.
CPP = re.compile(r"#\s*(include|define|undef|ifdef|ifndef|endif|elif|else|if"
                 r"|pragma|error|line|import|warning)\b")
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
WORDS = re.compile(r"[^\W\d_]{2,}", re.U)


def _opens(t, i, a, b):
    """Правда ли, что в позиции i начинается блочный комментарий.

    Незакрытый — не комментарий: в строке `for f in /var/log/app/*.log` стоит
    маска файлов, и на живом прогоне она съела весь листинг до конца.
    Настоящему `/*` вдобавок не предшествует буква: `app/*` — это путь,
    `a/*b` — деление.
    """
    if t.find(b, i + len(a)) < 0:
        return False
    return not (a == "/*" and i and (t[i - 1].isalnum() or t[i - 1] in "/.*"))


def _line_mark(t, i):
    """Длина знака строчного комментария, начинающегося в позиции i, или 0."""
    if t.startswith("//", i):
        return 0 if i and t[i - 1] == ":" else 2       # не «https://»
    if t.startswith("--", i):
        # В SQL и Lua знак — именно «-- » с пробелом; без него это ключ
        # командной строки или уменьшение на единицу.
        before = t[i - 1] if i else "\n"
        return 3 if t[i + 2:i + 3] == " " and before in " \t\n(" else 0
    if t.startswith("#", i):
        if t.startswith("#!", i) and i == 0:
            return 0                                    # shebang
        if CPP.match(t, i) or COLOR.match(t, i):
            return 0
        return 1
    return 0


def _scan(t):
    """Границы комментариев и строковых литералов: (комментарии, литералы).

    Знает не все языки, а самые ходовые. Пустой ответ значит «не знаю», а не
    «ничего нет», — потому и служит только проверкой сказанного моделью, а не
    заменой ему.
    """
    out, lit, i, n, quote, at = [], [], 0, len(t), None, 0
    while i < n:
        c = t[i]
        if c == "\n":
            # Строковые литералы почти нигде не переносятся, а вот `'a` в
            # Rust или апостроф в тексте — сплошь и рядом. Обрывая счёт
            # кавычек на конце строки, ошибку запираем в одну строку.
            quote, i = None, i + 1
            continue
        if quote:
            i += 2 if c == "\\" else 1
            if c == quote:
                lit.append((at, i))
                quote = None
            continue
        if c in "\"'":
            quote, at, i = c, i, i + 1
            continue
        for a, b in BLOCK:
            if t.startswith(a, i) and _opens(t, i, a, b):
                j = t.find(b, i + len(a))
                out.append((i + len(a), j))
                i = j + len(b)
                break
        else:
            k = _line_mark(t, i)
            if k:
                j = t.find("\n", i)
                j = n if j < 0 else j
                out.append((i + k, j))
                i = j
            else:
                i += 1
    return out, lit


def worth(s):
    """Стоит ли переводить. Двух слов не набралось — это `# TODO`, ссылка или
    закомментированная строка кода, а не объяснение автора."""
    s = re.sub(r"\S+://\S+", " ", s).strip()
    return len(s) >= 12 and len(WORDS.findall(s)) >= 3


def fits(line, part):
    """Может ли `part` быть комментарием в этой строке.

    Внутри комментария — да. Внутри строкового литерала — нет: `print("add up
    the prices")` печатает эти слова, и через абзац книга показывает вывод.
    Ни того ни другого не нашли — язык, видимо, не из тех, что мы разбираем,
    и тут остаётся верить модели.
    """
    at = line.find(part)
    if at < 0:
        return False
    end = at + len(part)
    cm, lit = _scan(line)
    if any(a <= at and end <= b for a, b in cm):
        return True
    if any(a < end and at < b for a, b in lit):
        return False
    return not cm


def splice(text, items):
    """Подставить переводы: items — [(номер строки, оригинал, перевод)].

    Меняется ровно названный кусок и ровно в названной строке. Не сошлось —
    строка остаётся как была: комментарий на языке оригинала заметен и
    поправим, а испорченный код — нет.
    """
    lines, n = text.split("\n"), 0
    for no, part, tr in items:
        i = no - 1
        part, tr = part.strip(), " ".join(tr.split())
        if not (0 <= i < len(lines)) or not part or not tr:
            continue
        if not worth(part) or not fits(lines[i], part):
            continue
        lines[i] = lines[i].replace(part, tr, 1)
        n += 1
    return "\n".join(lines), n
