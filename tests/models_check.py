#!/usr/bin/env python3
"""Проверка выбора моделей: кто какой проход делает.

Старшинство одно на все роли: ключ роли, затем `--model`, затем набор
агента. Исключений два, и оба стоили денег: разметке и корректуре дорогая
`--model` не достаётся (им хватает дешёвой модели из набора), а сверщик без
своего ключа берёт цепочку редактора, а не переводчика — судьёй в
собственном деле переводчику быть нельзя.

Модели здесь не создаются: вместо `make_agent` подставлена запись того, что
у него попросили.

    python3 tests/models_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import cli, lang                             # noqa: E402
from booktrans.models import Models, parse_chain            # noqa: E402

for k in list(os.environ):
    if k.startswith("BT_"):                 # умолчания из окружения проверке мешают
        del os.environ[k]


def fake(name, model, command, wait=None, max_wait=None, log=None, effort=None):
    return (name, model, effort)


def models(*argv):
    args = cli.parser("ru").parse_args(["книга.epub", *argv])
    return Models(args, make=fake)


def main():
    bad = seen = 0
    lang.set_ui("ru")

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # ---- разбор цепочки
    got = parse_chain("a,b", "agy")
    ok("цепочка через запятую, агент из --agent",
       got == [("agy", "a", None), ("agy", "b", None)], got)
    got = parse_chain("claude:m", "agy")
    ok("агент перед двоеточием", got == [("claude", "m", None)], got)
    got = parse_chain("m:high,claude:x:low", "agy")
    ok("усилие третьей частью",
       got == [("agy", "m", "high"), ("claude", "x", "low")], got)
    got = parse_chain("codex:,agy:", "claude")
    ok("пустая модель — умолчание агента",
       got == [("codex", "", None), ("agy", "", None)], got)
    ok("пустой ключ — пустая цепочка", parse_chain(None, "agy") == [])

    # ---- старшинство
    got = models().chain()
    ok("ничего не названо: агент со своей моделью", got == [("claude", None, None)], got)
    got = models().chain("formatter")
    ok("разметке — дешёвая модель набора с низким усилием",
       got == [("claude", "claude-sonnet-5", "low")], got)
    got = models("--model", "X").chain("translator")
    ok("--model достаётся переводчику", got == [("claude", "X", None)], got)
    got = models("--model", "X").chain("formatter")
    ok("--model разметке не достаётся",
       got == [("claude", "claude-sonnet-5", "low")], got)
    got = models("--model", "X").chain("ocrmodel")
    ok("страницы pdf у claude читает Sonnet, не --model",
       got == [("claude", "claude-sonnet-5", "low")], got)
    got = models("--agent", "agy", "--model", "X").chain("ocrmodel")
    ok("у agy страницы читает Flash, не --model",
       got == [("agy", "gemini-3.8-flash-low", None),
               ("agy", "claude-sonnet-4-6", None)], got)
    got = models("--formatter", "Y", "--effort", "high").chain("formatter")
    ok("названная явно модель разметки — с общим усилием",
       got == [("claude", "Y", "high")], got)
    got = models("--agent", "agy").chain("editor")
    ok("набор agy: три модели цепочкой",
       got == [("agy", "gemini-3.1-pro-high", None),
               ("agy", "claude-opus-4-6-thinking", None),
               ("agy", "claude-opus-5", None)], got)
    got = models("--agent", "agy", "--editor", "a,claude:b").chain("editor")
    ok("ключ роли сильнее набора, чужой агент в цепочке",
       got == [("agy", "a", None), ("claude", "b", None)], got)

    # ---- сверщик
    m = models("--editor", "E", "--translator", "T")
    ok("сверщик без ключа берёт цепочку редактора",
       m.chain("verifier") == m.chain("editor") == [("claude", "E", None)],
       m.chain("verifier"))
    got = models("--editor", "E", "--verifier", "V").chain("verifier")
    ok("свой ключ сверщика сильнее", got == [("claude", "V", None)], got)

    # ---- первая и остальные
    m = models("--editor", "a,b,c")
    ok("first — первая, rest — остальные",
       m.first("editor") == ("claude", "a", None)
       and m.rest("editor") == [("claude", "b", None), ("claude", "c", None)])

    # ---- проверка ключей при старте
    def stops(*argv):
        try:
            models(*argv).check()
        except SystemExit:
            return True
        return False

    ok("чужой агент в цепочке отвергается", stops("--editor", "foo:bar"))
    ok("чужой агент у сверщика тоже", stops("--verifier", "foo:bar"))
    ok("у agy усилие и суффиксом, и ключом — отказ",
       stops("--agent", "agy", "--effort", "high", "--editor", "m-low"))
    ok("усилие ключом без суффикса — можно",
       not stops("--agent", "agy", "--effort", "high", "--editor", "m"))
    ok("исправные ключи проходят",
       not stops("--editor", "a,agy:b:low", "--ocrmodel", "cmd:"))

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
