#!/usr/bin/env python3
"""Проверка вывода в markdown.

Markdown берут ради текста, который правят руками и кладут в git, поэтому
здесь важно обратное обычному: не потерять разметку книги и не наделать её
там, где в книге просто текст. Абзац, начатый с решётки или дефиса, читается
как заголовок или список; звёздочка посреди фразы — как курсив.

Картинки идут папкой рядом: `data:` в markdown показывают немногие, а файл
на несколько мегабайт перестаёт быть тем, ради чего формат и выбирают.

    python3 tests/md_check.py
"""
import base64
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as O_extract                   # noqa: E402
from booktrans import lang as O_lang                        # noqa: E402
from booktrans import output as O                           # noqa: E402

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")

ITEMS = [
    ("title", "Глава первая", "s01.b0001", None, None, 1),
    ("p", "Текст с <b>жирным</b> и <i>курсивом</i>, <a1>ссылкой</a1>.",
     "s01.b0002", ["https://example.org/a"], None, None),
    ("p", "— Да, — сказал он, — 5 * 3 и file_name.txt.", "s01.b0003",
     None, None, None),
    ("p", "- знак минус в начале строки", "s01.b0005", None, None, None),
    ("p", "# 12 по шкале", "s01.b0006", None, None, None),
    ("p", "Абзац со сноской.", "s01.b0004", None, None, None),
    ("p", "Сюда ведёт ссылка.", "s02.b0001", None, None, None),
    ("p", "Отсюда <a1>внутрь</a1>.", "s02.b0002", ["#s02.b0001"], None, None),
    ("verse", "Строка первая", "s02.b0003", None, None, None),
    ("verse", "Строка вторая", "s02.b0004", None, None, None),
    ("code", "x = 1  # счёт", "s02.b0005", None, None, None),
    ("table", "a | b\n1 | 2", "s02.b0006", None, [[[2, 1], [1, 1]]], None),
    ("p", "Формула $P_{\\mathrm{doom}}$ и блок $$\\gamma = 1$$ целы.",
     "s02.b0009", None, None, None),
    ("p", "Цена выросла с $5 до $10 за штуку.", "s02.b0010", None, None, None),
    ("p", "В таблице стоит $0,3\\text{ года}@70\\%$ — это тоже формула.",
     "s02.b0012", None, None, None),
    ("p", "Готовая ссылка [сайт](https://example.org/b) в тексте.",
     "s02.b0011", None, None, None),
    ("p", "Внутри кода <code>[промо]</code> косых быть не должно.",
     "s02.b0013", None, None, None),
    ("image", "photo.png", "s02.b0007", None, None, None),
    ("break", "", "s02.b0008", None, None, None),
]
NOTES = {"s01.b0004": "Пояснение с *курсивом*."}


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    p = os.path.join(d, "книга.md")
    said = []
    O.write_md(p, {"title": "Книга", "author": "Автор", "target_lang": "ru"},
               ITEMS, NOTES, {"photo.png": PIXEL}, "Прим.:", {}, cover=PIXEL,
               log=said.append, lang=O_lang)
    t = open(p, encoding="utf-8").read()

    ok("заголовок уровнем", "\n# Глава первая" in t or t.startswith("# "),
       t[:60])
    # Титульный лист сборщик книги кладёт в блоки сам, и печатать своё
    # заглавие поверх него значит выдать его дважды подряд.
    with_title = ITEMS[:1] + [("title", "Книга", "_meta_title", None, None, 1),
                              ("p", "Автор", "_meta_author", None, None, None)]
    p3 = os.path.join(d, "титул.md")
    O.write_md(p3, {"title": "Книга", "author": "Автор", "target_lang": "ru"},
               with_title, {}, {}, "Прим.:", {})
    t3 = open(p3, encoding="utf-8").read()
    ok("заглавие не задвоено", t3.count("# Книга") == 1, t3[:80])
    ok("жирное и курсив знаками",
       "**жирным**" in t and "*курсивом*" in t,
       re.findall(r"\*+\w+\*+", t))
    ok("ссылка скобками", "[ссылкой](https://example.org/a)" in t,
       re.findall(r"\[[^\]]*\]\([^)]*\)", t))
    # Внутренняя ссылка ведёт к якорю, а якорь ставится только там, куда
    # ссылаются: метка у каждого абзаца засоряла бы текст без нужды.
    ok("внутренняя ссылка и якорь",
       "[внутрь](#s02.b0001)" in t and '<a id="s02.b0001"></a>' in t,
       re.findall(r'<a id="[^"]*"></a>', t))
    ok("лишних якорей нет", t.count("<a id=") == 1,
       re.findall(r'<a id="[^"]*"', t))

    # Дефис в начале строки markdown принимает за список, решётка — за
    # заголовок. В книге такие абзацы попадаются, и портить их нельзя.
    # Знак списка, наоборот, не трогаем: из markdown он пришёл списком и
    # списком уйдёт. Экранировать его значило бы разваливать всякий список,
    # прошедший через конвейер.
    ok("знак списка сохранён", "\n- знак минус" in t,
       [l for l in t.splitlines() if "минус" in l])
    ok("решётка не стала заголовком", "\\# 12 по шкале" in t,
       [l for l in t.splitlines() if "шкале" in l])
    # А длинное тире списка не делает: экранировать его значило бы засорить
    # обратными косыми каждую реплику в книге.
    ok("реплику не тронули", "\n— Да, — сказал он" in t,
       [l for l in t.splitlines() if "Да," in l])
    ok("звёздочка и подчёркивание в тексте экранированы",
       "5 \\* 3" in t and "file\\_name.txt" in t,
       [l for l in t.splitlines() if "file" in l])

    # Формулы markdown пишет долларами, и экранировать внутри них нельзя:
    # `$P_{\\mathrm{doom}}$` от этого превращается в кашу.
    ok("формула цела", "$P_{\\mathrm{doom}}$" in t and "$$\\gamma = 1$$" in t,
       [l for l in t.splitlines() if "doom" in l])
    # А доллар сам по себе — чаще деньги, чем формула.
    ok("деньги не приняли за формулу", "с $5 до $10 за штуку" in t,
       [l for l in t.splitlines() if "штуку" in l])
    # Кириллица внутри `\\text{}` законна: мера «это формула» тут по знаку
    # TeX, а не по письму, иначе такие ячейки таблиц выходили кашей.
    ok("формула с русским словом цела", "$0,3\\text{ года}@70\\%$" in t,
       [l for l in t.splitlines() if "года}" in l])
    # Внутри кода экранировать нечего: markdown там ничего не разбирает, и
    # косая осталась бы видна читателю.
    ok("в коде нет косых", "`[промо]`" in t,
       [l for l in t.splitlines() if "промо" in l])
    ok("готовая ссылка цела", "[сайт](https://example.org/b)" in t,
       [l for l in t.splitlines() if "сайт" in l])

    ok("сноска ссылкой и определением",
       "Абзац со сноской.[^1]" in t and "[^1]: Прим.:" in t,
       re.findall(r"\[\^\d+\]:?[^\n]{0,40}", t))
    ok("курсив в сноске развёрнут", "*курсивом*" in t.split("[^1]:")[-1],
       t.split("[^1]:")[-1][:60])

    # Строфа держится на двух пробелах в конце строки: без них markdown
    # склеит стихи в один абзац.
    ok("строфа не склеилась", "Строка первая  \nСтрока вторая" in t,
       [l for l in t.splitlines() if "Строка" in l])
    ok("листинг в заборе", "```\nx = 1  # счёт\n```" in t,
       re.findall(r"```[^`]*```", t))
    ok("таблица столбиками",
       "| a | b |" in t and re.search(r"\|( --- \|)+", t) is not None,
       [l for l in t.splitlines() if l.startswith("|")])
    ok("разделитель звёздочками", "\n* * *" in t)

    # Картинки лежат папкой рядом, и ссылки ведут в неё.
    where = os.path.join(d, "книга.images")
    ok("картинка легла в папку", os.path.exists(f"{where}/photo.png"),
       os.listdir(d))
    ok("обложка легла туда же",
       any(x.startswith("cover.") for x in os.listdir(where)),
       os.listdir(where))
    ok("ссылки ведут в папку", "![](книга.images/photo.png)" in t,
       re.findall(r"!\[\]\([^)]*\)", t))
    ok("сборщик сказал, сколько вложил", any("2" in x for x in said), said)

    # Книга без картинок не должна оставлять пустую папку рядом.
    p2 = os.path.join(d, "простая.md")
    O.write_md(p2, {"title": "Книга", "target_lang": "ru"},
               [("p", "Только текст.", "s01.b0001", None, None, None)],
               {}, {}, "Прим.:", {})
    ok("без картинок папки нет", not os.path.exists(f"{d}/простая.images"),
       os.listdir(d))

    # --- чтение markdown
    #
    # Прежде `.md` читался как голый текст: разметка ехала в книгу буквально
    # («## Вступление» строкой заголовка, звёздочки посреди фразы), а ради
    # заголовков спрашивали модель — на статье это стоило четыре минуты и
    # шестьдесят центов, хотя в файле они названы прямо.
    src = os.path.join(d, "статья.md")
    open(src, "w", encoding="utf-8").write(
        "# Заглавие\n\nПервый абзац со **словом** и *курсивом*.\n\n"
        "## Раздел\n\nАбзац со [ссылкой](https://example.org/c) и `кодом`.\n\n"
        "Формула $x_1$ цела.\n\n"
        "- пункт списка\n\n"
        "```\nx = 1\n```\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "![](рис.png)\n\n* * *\n")
    ok("файл опознан как разметка", O_extract.is_markdown(src))
    meta, blocks, _, _ = O_extract._markdown(src)
    kinds = [b["kind"] for b in blocks]
    by = {b["id"]: b for b in blocks}
    text = {b["id"]: b["text"] for b in blocks}
    joined = "\n".join(text.values())
    ok("заглавие взято из первого заголовка", meta["title"] == "Заглавие",
       meta["title"])
    ok("решётка не осталась в тексте", "#" not in joined, joined[:60])
    ok("уровень заголовка сохранён",
       [b.get("level") for b in blocks if b["kind"] == "title"] == [1, 2],
       [b.get("level") for b in blocks if b["kind"] == "title"])
    ok("выделение стало тегами",
       "<b>словом</b>" in joined and "<i>курсивом</i>" in joined,
       [t for t in text.values() if "слов" in t])
    ok("ссылка стала номером и списком",
       any("<a1>ссылкой</a1>" in t for t in text.values())
       and any(b.get("links") == ["https://example.org/c"] for b in blocks),
       [b.get("links") for b in blocks if b.get("links")])
    ok("код стал тегом", "<code>кодом</code>" in joined)
    ok("формула цела", "$x_1$" in joined,
       [t for t in text.values() if "x_1" in t or "x\\_1" in t])
    ok("листинг, таблица, картинка и разделитель узнаны",
       {"code", "table", "image", "break"} <= set(kinds), sorted(set(kinds)))
    # Маркер списка остаётся в тексте: своего вида блока для списков у
    # конвейера нет, а markdown прочтёт знак обратно.
    ok("пункт списка сохранил знак",
       any(t.startswith("- пункт") for t in text.values()),
       [t for t in text.values() if "пункт" in t])
    # Точная привязка сноски: метка после термина — знак у термина; термин
    # склонён — находится по основе; не нашёлся — знак в конце, как раньше.
    for text, terms, want in (
            ("канал <i>Vesti</i> вечером", ["Vesti"], "после Vesti"),
            ("поминал зелёного змия в сердцах", ["зелёный змий"], "после змия"),
            # Составной TERM «микроже, же»: части ищутся порознь, а куски
            # короче трёх знаков не ищутся вовсе — «же» нашлось бы везде.
            ("вода в микроже шла нехотя", ["микроже, же"], "после микроже"),
            # знак не встаёт внутрь слова с дефисом: «микро[1]-g» было бы хуже
            ("вода при микро-g шла нехотя", ["микро-g"], "после микро-g"),
            ("но же само по себе не термин", ["же"], "нет"),
            ("вовсе другой текст", ["эсхатология"], "нет")):
        got = O_extract and __import__("booktrans.output", fromlist=["x"]).anchor_note(text, "b1", terms)
        has = "\ue000" in got
        ok(f"привязка: {terms[0][:18]}", has == (want != "нет"),
           got)

    # Эпиграф зрячего чтения: цитата или строки с жёсткими переносами —
    # стихи; строка разметки (заголовок, картинка, таблица) — нет.
    for text, want in (("> строка раз  \n> строка два  \n> Хайям", True),
                       ("строка раз,  \nстрока два,  \nХайям", True),
                       ("# Заголовок  \n## или  \n# ещё", False),
                       ("обычная проза\nв две строки", False)):
        got = O_extract._verse_lines(text)
        ok(f"эпиграф: {text[:22]!r}", bool(got) == want, got)

    # Экранирование markdown. Разбор в зрячем чтении pdf его не понимал:
    # разделитель сцен `\* \* \*` превращался в `\<i> \</i> \*`, и читатель
    # видел в epub `\ \ \*`. Модель переводила это дословно.
    for src, want in ((r"\* \* \*", "* * *"),
                      (r"текст со \*звёздочкой\*", "текст со *звёздочкой*"),
                      ("**жирно** и *косо*", "<b>жирно</b> и <i>косо</i>"),
                      # Выделение не начинается с пробела и не кончается им —
                      # иначе строка из звёздочек читается как курсив.
                      ("* * *", "* * *"),
                      ("5 * 3 = 15", "5 * 3 = 15")):
        got = O_extract._md_spans(src, [])
        ok(f"разметка строки: {src[:26]}", got == want, got)

    # Ряд подчёркиваний — место под запись от руки («Экземпляр №________»),
    # а не разметка. Прежде он разбирался как вложенные теги, и fb2 выходил
    # невалидным: сборка кончалась на «mismatched tag».
    for src, want in (("Экземпляр №________", "Экземпляр №________"),
                      ("****", "****"), ("_ _ _", "_ _ _"),
                      ("__жирно__", "<b>жирно</b>"),
                      ("**2**", "<b>2</b>")):
        got = O_extract._md_spans(src, [])
        ok(f"подчёркивания и звёздочки: {src[:20]}", got == want, got)

    # А если непарный тег всё же случился — от модели, из чужой разметки, —
    # сборщик сводит теги сам. Прежде один такой тег ронял весь прогон, и
    # книга не выходила ни в одном формате.
    for src, want in (("<strong><emphasis><emphasis></emphasis></strong></emphasis>",
                       "<strong><emphasis><emphasis></emphasis></emphasis></strong>"),
                      ("<i>не закрыт", "<i>не закрыт</i>"),
                      ("лишнее</i> закрытие", "лишнее закрытие"),
                      ("<b>цел</b>", "<b>цел</b>")):
        got = O._balance(src)
        ok(f"теги сведены: {src[:26]}", got == want, got)

    # Разделитель сцен: звёздочек или тире не меньше трёх, между ними пробелы
    # и табуляции в любом числе. Две звёздочки — это выделение, не разделитель.
    for src, want in (("* * *", True), ("*  *  *", True), ("*\t*\t*", True),
                      ("  *   *   *  ", True), ("***", True), ("- - -", True),
                      ("___", True), ("\\*\t\\*  \\*", True),
                      ("* *", False), ("*текст*", False)):
        got = bool(O_extract.MD_RULE.match(src.replace("\\", "").strip()))
        ok(f"разделитель: {src[:22]!r}", got == want, got)

    # А голый текст с расширением .md разметкой не считается: заголовки в нём
    # придётся спрашивать у модели, как и прежде.
    plain = os.path.join(d, "голый.md")
    open(plain, "w", encoding="utf-8").write("Просто текст.\n\nБез разметки.\n")
    ok("голый текст не принят за разметку", not O_extract.is_markdown(plain))

    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
