#!/usr/bin/env python3
"""Перенос строки внутри абзаца: `<br>` от чтения до каждого формата.

Чат, форум и письмо набраны одним абзацем со строками через <br/>; раньше
перенос становился пробелом ещё при чтении, и реплики слипались. Теперь
он — разметка: сохраняется в блоке, меняет отпечаток и выходит в каждом
формате по-своему.

    python3 tests/br_check.py
"""
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                          # noqa: E402
from booktrans import output as O                           # noqa: E402
from booktrans import pipeline as P                         # noqa: E402

META = {"title": "Книга", "author": "Автор", "target_lang": "ru"}
ITEMS = [("title", "Глава", "s01.b0000", None),
         ("p", "<b>Аня:</b> привет<br>Боря: и тебе<br/>Аня: пока", "s01.b0001", None),
         ("p", "Обычный абзац.", "s01.b0002", None)]


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    html = ("<html><body><h1>Глава</h1>"
            "<p>Аня: привет<br/>Боря: и тебе<br/><br/>Аня: пока<br/></p>"
            "<p>Обычный абзац достаточной длины, чтобы считаться прозой.</p>"
            "</body></html>")
    hp = os.path.join(d, "book.html")
    open(hp, "w", encoding="utf-8").write(html)
    _, blocks, _, _ = E.read_book(hp)
    got = next((b["text"] for b in blocks if "Аня" in b["text"]), "")
    ok("чтение html: перенос — разметка, сдвоенный и крайний сняты",
       got == "Аня: привет<br>Боря: и тебе<br>Аня: пока", got)

    ok("strip: перенос — пробел, а не склейка", P.strip("а<br>б") == "а б",
       P.strip("а<br>б"))
    ok("отпечаток видит перенос",
       P.fingerprint("а б") != P.fingerprint("а<br>б"), None)
    ok("абзац по переносам", O._br_parts("а<br>б<br/> <br>в") == ["а", "б", "в"],
       O._br_parts("а<br>б<br/> <br>в"))
    got = O._br_parts("<i>раз<br>два<br><b>три</b></i> конец")
    ok("разметка через перенос закрывается и открывается заново",
       got == ["<i>раз</i>", "<i>два</i>", "<i><b>три</b></i> конец"], got)

    p = os.path.join(d, "b.html")
    O.write_html(p, META, ITEMS, {}, {}, "Прим.:", {})
    t = open(p, encoding="utf-8").read()
    ok("html: <br/> внутри абзаца", "привет<br/>Боря: и тебе<br/>Аня" in t, t[-300:])
    ok("html: теги сведены, </br> не появился", "</br>" not in t, None)

    p = os.path.join(d, "b.epub")
    O.write_epub(p, META, ITEMS, {}, {}, "Прим.:", {})
    z = zipfile.ZipFile(p)
    body = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))
    ok("epub: <br/> внутри абзаца", "привет<br/>Боря" in body, None)

    p = os.path.join(d, "b.md")
    O.write_md(p, META, ITEMS, {}, {}, "Прим.:", {})
    t = open(p, encoding="utf-8").read()
    ok("markdown: жёсткий перенос", "привет  \nБоря: и тебе  \nАня" in t, t[-200:])

    p = os.path.join(d, "b.txt")
    O.write_txt(p, META, ITEMS, {}, {}, "Прим.:", {})
    t = open(p, encoding="utf-8").read()
    ok("txt: перевод строки", "привет\nБоря: и тебе\nАня" in t, t[-200:])

    p = os.path.join(d, "b.tex")
    O.write_tex(p, META, ITEMS, {}, {}, "Прим.:", {})
    t = open(p, encoding="utf-8").read()
    ok("tex: \\newline внутри абзаца", "привет\\newline{}" in t and "и тебе\\newline{}" in t,
       t[-300:])
    ok("tex: крайний перенос снят", "\\newline" not in O._tex("абзац<br>"), O._tex("абзац<br>"))

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
