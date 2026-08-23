#!/usr/bin/env python3
"""Проверка реестра стихотворных строк.

Стихи повторяются: рефрены, поэма, которую герой дочитывает через сто
страниц. Один оригинал обязан переводиться одинаково, а переводчик работает
кусками. Реестр помнит принятое; проза с теми же словами его не трогает.

    python3 tests/verse_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                        # noqa: E402


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    line = "We are the root of falsehood and truth's pure spring"
    state = {}
    chunk1 = [{"id": "s02.b0001", "kind": "verse", "text": line},
              {"id": "s02.b0002", "kind": "p", "text": "Прозаический абзац."}]
    # первого раза в реестре нет — переводим как обычно
    ok("до перевода канона нет", P.verse_canon(state, chunk1) == {})
    P.verse_learn(state, chunk1, {"s02.b0001": "Мы — корень неправды и правды родник",
                                  "s02.b0002": "не стих"})
    ok("строка легла в реестр", len(state["verse"]) == 1, state.get("verse"))
    # та же строка через сто страниц — канон найден по отпечатку
    chunk2 = [{"id": "s30.b0007", "kind": "verse", "text": line}]
    got = P.verse_canon(state, chunk2)
    ok("повтор получает канон",
       got == {"s30.b0007": "Мы — корень неправды и правды родник"}, got)
    # та же фраза прозой — механика её не видит
    prose = [{"id": "s31.b0004", "kind": "p", "text": line}]
    ok("проза с теми же словами не трогается",
       P.verse_canon(state, prose) == {})
    P.verse_learn(state, prose, {"s31.b0004": "прозой переведено иначе"})
    ok("и в реестр прозой не попадает", len(state["verse"]) == 1)
    # разметка не меняет отпечатка строки
    chunk3 = [{"id": "s40.b0001", "kind": "verse", "text": f"<i>{line}</i>"}]
    ok("курсив не рвёт совпадение", bool(P.verse_canon(state, chunk3)))

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
