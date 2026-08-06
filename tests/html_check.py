#!/usr/bin/env python3
"""Проверка чтения отдельного html.

Epub — это zip из xhtml, и разбор документа у них общий. Разница в том, что
отдельный html пишут люди и редакторы: теги не закрыты, атрибуты без кавычек,
`<br>` без слэша. Строгий разбор на таком падает, а книга читается прекрасно.

Картинки — второе отличие: ссылка ведёт на файл рядом, которого может и не
быть, а бывает и наоборот, картинка вшита в саму разметку.

    python3 tests/html_check.py
"""
import base64
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                       # noqa: E402

# Однопиксельный png, чтобы проверить и вшитую картинку, и лежащую рядом.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")

PAGE = """<!doctype html>
<html lang=en>
<head><meta charset="utf-8"><title>Пробная книга</title>
<meta name="author" content="Иван Петров">
<style>p { color: red }</style></head>
<body>
<h1>Глава первая
<p>Первый абзац: заголовок над ним не закрыт, и это обычное дело.
<p>Второй абзац со <b>вставкой</b> и <a href="https://example.com">ссылкой</a>.
<img src="есть.png" alt="лежит рядом">
<img src="нет.png" alt="а этой нет">
<img src="data:image/png;base64,%s">
<pre>
def f(x):      # комментарий
    return x
</pre>
<hr>
<h2>Глава вторая</h2>
<p>Текст второй главы<a href="#fn1" role="doc-noteref">1</a>.</p>
<p role="doc-footnote" id="fn1">Сноска автора.</p>
<p id="fn2">Абзац, похожий на сноску, но на него никто не ссылается.</p>
<img src="https://example.com/pictures/whale.jpg" alt="по сети">
<ul><li>первый пункт<li>второй пункт</ul>
<p>Строка с<br>переносом.
<p>Слово self-
defense разорвано переносом, а short- or long-term нет.
<ul><li>пункт первый<li>пункт второй<li>пункт третий<li>пункт четвёртый</ul>
<script>var x = "этого в книге быть не должно";</script>
</body></html>
"""


def book(with_file=True):
    d = tempfile.mkdtemp()
    open(os.path.join(d, "book.html"), "w", encoding="utf-8").write(
        PAGE % base64.b64encode(PIXEL).decode())
    if with_file:
        open(os.path.join(d, "есть.png"), "wb").write(PIXEL)
    return d


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:38} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = book()
    meta, blocks, cover, imgs = E.read_book(os.path.join(d, "book.html"))
    kinds = [(b["kind"], b["text"]) for b in blocks]

    ok("заглавие из <title>", meta.get("title") == "Пробная книга", meta)
    ok("автор из <meta>", meta.get("author") == "Иван Петров", meta)
    ok("язык из <html lang>", meta.get("lang") == "en", meta)

    # Незакрытый <h1> закрывается следующим блоком, иначе в заголовок уедет
    # вся страница.
    ok("незакрытый заголовок", kinds[0] == ("title", "Глава первая"), kinds[0])
    ok("абзац после него отдельный",
       kinds[1][0] == "p" and kinds[1][1].startswith("Первый абзац"), kinds[1])

    ok("внешняя ссылка сохранена",
       blocks[2].get("links") == ["https://example.com"], blocks[2])
    ok("листинг прочитан", any(k == "code" and "return x" in t for k, t in kinds))
    ok("линейка стала разрывом", any(k == "break" for k, t in kinds))
    ok("скрипт и стиль выброшены",
       not any("этого в книге" in t or "color: red" in t for k, t in kinds))

    ok("картинка с диска взята", "есть.png" in imgs, list(imgs))
    ok("вшитая картинка взята", any(n.startswith("img") for n in imgs), list(imgs))
    ok("пропавшая пропущена", "нет.png" not in imgs, list(imgs))
    ok("картинок ровно две", len(imgs) == 2, list(imgs))
    # Скачивать нельзя, а терять незачем: в html ссылка доедет и покажется.
    ok("картинка по сети осталась ссылкой",
       any(k == "image" and t.startswith("https://") for k, t in kinds), kinds)

    ok("пункты списка не пропали",
       [t for k, t in kinds if t in ("первый пункт", "второй пункт")]
       == ["первый пункт", "второй пункт"], kinds)
    # Перенос строки внутри слова — след старых конвертеров. Настоящий
    # висячий дефис пишется через пробел, и его трогать нельзя.
    ok("слово, разорванное переносом, срослось",
       any("self-defense" in t for k, t in kinds),
       [t for k, t in kinds if "defense" in t])
    ok("висячий дефис не тронут",
       any("short- or long-term" in t for k, t in kinds),
       [t for k, t in kinds if "short-" in t])
    ok("<br> не склеивает слова",
       any("с переносом" in t for k, t in kinds),
       [t for k, t in kinds if "перенос" in t])

    ok("сноска со ссылкой — сноска",
       any(k == "note" and t == "Сноска автора." for k, t in kinds), kinds)
    # Правило то же, что и в epub: без ссылки из текста это просто абзац.
    ok("сноска без ссылки — абзац",
       any(k == "p" and t.startswith("Абзац, похожий") for k, t in kinds), kinds)

    ok("главы поделены по заголовкам",
       len({b["id"].split(".")[0] for b in blocks}) == 2,
       sorted({b["id"].split(".")[0] for b in blocks}))
    shutil.rmtree(d)

    # Картинок рядом не положили — читается всё равно.
    d = book(with_file=False)
    meta, blocks, cover, imgs = E.read_book(os.path.join(d, "book.html"))
    ok("без файлов картинок книга читается",
       len([b for b in blocks if b["kind"] == "p"]) >= 4 and "есть.png" not in imgs,
       list(imgs))
    shutil.rmtree(d)

    # Перепись стилей — по ней модель определяет вёрстку.
    d = book()
    rows = E.scan_styles(os.path.join(d, "book.html"))
    ok("перепись стилей собрана",
       {(r["tag"], r["cls"]) for r in rows} >= {("h1", ""), ("h2", ""), ("p", "")},
       [(r["tag"], r["cls"], r["count"]) for r in rows])
    # Примеры берутся из разных мест книги: у списка первые пункты — это
    # оглавление, и по ним модель отвечает «skip», унося с ним все списки.
    li = next((r for r in rows if r["tag"] == "li"), None)
    ok("примеры стиля не все из начала",
       li and li["samples"][-1] != li["samples"][0]
       and "четвёртый" in li["samples"][-1], li["samples"] if li else "нет li")
    shutil.rmtree(d)

    print(f"\nслучаев: 24   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
