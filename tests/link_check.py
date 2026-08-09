#!/usr/bin/env python3
"""Проверка ссылок внутри книги.

Страницу сохраняют целиком, и перекрёстные ссылки в ней записаны полным
адресом: «https://сайт/статья.html#Intro» вместо «#Intro». Разбор видел `http`
и считал такую ссылку внешней — в переведённой книге она уводила читателя на
подлинник, да ещё и в оглавлении, где таких ссылок большинство.

Ошибиться тут можно в обе стороны: не опознать внутреннюю — читатель уходит из
книги; принять внешнюю за внутреннюю — ссылка ведёт в пустоту. Плюс якорь
должен достаться тому блоку, которому принадлежит, а не соседнему.

    python3 tests/link_check.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E, output as O                # noqa: E402

SITE = "https://example.com/article.html"

PAGE = """<html><head><title>Со ссылками</title></head><body>
<h1>Оглавление</h1>
<p><a href="%(site)s#Intro">Введение</a></p>
<p><a href="#Later">Дальше</a></p>
<p><a href="https://other.example/page">Наружу</a></p>
<p><a href="%(site)s#Missing">В никуда</a></p>
<h2><a name="RTFToC1"> </a><a name="Intro">Введение</a></h2>
<p>Текст введения.</p>
<h2 id="Later">Дальше</h2>
<p>Текст второго раздела.</p>
</body></html>
""" % {"site": SITE}


def read(src):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "book.html")
    open(p, "w", encoding="utf-8").write(src)
    out = E.read_book(p)
    shutil.rmtree(d)
    return out


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:44} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    meta, blocks, cover, imgs = read(PAGE)
    by = {b["id"]: b for b in blocks}
    urls = [u for b in blocks for u in b.get("links", ())]
    inside = [u for u in urls if u.startswith("#")]

    ok("ссылок внутрь книги две", len(inside) == 2, urls)
    ok("внешняя осталась внешней",
       "https://other.example/page" in urls, urls)
    # Якоря нет в книге — значит ссылка и правда наружу, оставляем как была.
    ok("несуществующий якорь не тронут",
       f"{SITE}#Missing" in urls, urls)

    tgt = [by.get(u[1:]) for u in inside]
    ok("цели найдены", all(tgt), inside)
    ok("полный адрес привязан к своему разделу",
       tgt[0] and tgt[0]["text"] == "Введение", tgt[0]["text"] if tgt[0] else "")
    # Якорь <a name> стоит внутри заголовка, и цель — сам заголовок, а не
    # следующий за ним абзац.
    ok("якорь достался заголовку, а не соседу",
       tgt[0] and tgt[0]["kind"] == "title", tgt[0]["kind"] if tgt[0] else "")
    ok("id= работает наравне с <a name>",
       tgt[1] and tgt[1]["text"] == "Дальше", tgt[1]["text"] if tgt[1] else "")

    items = [(b["kind"], b["text"], b["id"], b.get("links")) for b in blocks]
    d = tempfile.mkdtemp()
    O.write_html(os.path.join(d, "a.html"), meta, items, {}, imgs, "Прим.:", {})
    h = open(os.path.join(d, "a.html"), encoding="utf-8").read()
    hl = set(re.findall(r'href="#([^"]+)"', h))
    hi = set(re.findall(r'id="([^"]+)"', h))
    ok("в html ссылки ведут в цели", hl and not (hl - hi), (sorted(hl), sorted(hi)))

    O.write_epub(os.path.join(d, "a.epub"), meta, items, {}, imgs, "Прим.:", {})
    import zipfile
    z = zipfile.ZipFile(os.path.join(d, "a.epub"))
    x = "".join(z.read(n).decode("utf-8", "ignore")
                for n in z.namelist() if n.endswith(".xhtml"))
    # В epub книга разложена по файлам, и адрес обязан нести имя файла.
    ok("в epub адрес несёт имя файла",
       bool(re.search(r'href="ch\d+\.xhtml#', x)),
       re.findall(r'href="[^"]*#[^"]*"', x)[:3])
    el = set(re.findall(r'href="(?:ch\d+\.xhtml)?#([^"]+)"', x))
    ei = set(re.findall(r'id="([^"]+)"', x))
    ok("в epub ссылки ведут в цели", el and not (el - ei), (sorted(el), sorted(ei)))
    shutil.rmtree(d)

    # Ссылка на сноску, потерянная в переводе. Проверка чисел ловила такое
    # вперемешку с десятками ложных тревог, и на живой книге три настоящие
    # потери из 1048 ссылок терялись в этом шуме.
    import json as _json
    import tempfile as _tmp
    from booktrans import build as _B, lang as _lang
    _lang.set_ui("ru")
    d = _tmp.mkdtemp()
    os.makedirs(f"{d}/tr")
    bl = [{"id": "s01.b0001", "kind": "p",
           "text": "Первый факт.<sup>1</sup> И второй.<sup>2</sup>"},
          {"id": "s01.b0002", "kind": "p", "text": "Третий факт.<sup>3</sup>"}]
    _json.dump({"index": 1, "model": "стенд", "cost_usd": 0,
                "tr": {"s01.b0001": "A fact.<sup>1</sup> And a second one.",
                       "s01.b0002": "A third fact.<sup>3</sup>"}},
               open(f"{d}/tr/0001.json", "w", encoding="utf-8"), ensure_ascii=False)
    said = []
    _B.qa(d, bl, lambda x="": said.append(x), _lang.T, "ru", "en", False)
    txt = "\n".join(said)
    ok("потерянная ссылка на сноску названа", "s01.b0001: 2" in txt, txt[-200:])
    ok("ссылки сосчитаны", "1 из 3" in txt, txt[-200:])
    shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: 12   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
