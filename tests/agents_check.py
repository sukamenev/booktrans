#!/usr/bin/env python3
"""Проверка строки запуска агента: что уходит в чужую программу.

Модель, усилие и срок ожидания задаются не нами, а ключами чужой командной
строки, и пропажу такого ключа видно только на живом прогоне. Здесь запуск
перехватывается, и строка сверяется по частям.

Отдельно — срок. У agy свой срок ожидания, пять минут, и думающая модель на
большом куске в него не укладывалась: ответ обрывался, а выглядело как отказ
модели. Срок должен быть один, наш.

    python3 tests/agents_check.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import agent as A                            # noqa: E402


class Ran:
    """Ответ вместо запуска: сама строка запоминается."""

    def __init__(self, out):
        self.out, self.cmd, self.returncode = out, None, 0
        self.stdout, self.stderr = out, ""

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        return self


ANSWERS = {
    "claude": json.dumps({"result": "готово", "total_cost_usd": 0}),
    "agy": json.dumps({"status": "SUCCESS", "response": "готово"}),
    "codex": "готово",
}


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    real = A.subprocess.run
    for kind in ("claude", "agy", "codex"):
        spy = Ran(ANSWERS[kind])
        A.subprocess.run = spy
        try:
            a = A.make_agent(kind, model="проба-модель", timeout=1234,
                             effort="low")
            a.run("система", "запрос")
        finally:
            A.subprocess.run = real
        cmd = spy.cmd or []
        ok(f"{kind}: модель названа", "проба-модель" in cmd, cmd)
        ok(f"{kind}: усилие названо",
           any("low" in str(c) for c in cmd), cmd)

    # Наш срок в 1234 секунды должен дойти до agy: свой у него короче, и
    # обрыв по нему приходит как отказ модели, а не как исчерпанное время.
    spy = Ran(ANSWERS["agy"])
    A.subprocess.run = spy
    try:
        A.make_agent("agy", model="проба", timeout=1234).run("", "запрос")
    finally:
        A.subprocess.run = real
    cmd = spy.cmd or []
    ok("agy: срок ожидания наш",
       "--print-timeout" in cmd and cmd[cmd.index("--print-timeout") + 1]
       == "1234s", cmd)

    # Молчаливая смерть: баннер и эхо промпта — не объяснение. Леса
    # срезаются, эхо режется только узнанным, настоящие слова выживают.
    banner = ("Reading prompt from stdin...\nOpenAI Codex v0.147.0\n"
              "--------\nworkdir: /tmp\nmodel: sol\nprovider: openai\n"
              "--------\nuser\nЯзыковая правка куска")
    ok("леса и эхо срезаны в пустоту",
       A.bare_words(banner, "Языковая правка куска целиком") == "",
       repr(A.bare_words(banner, "Языковая правка куска целиком")))
    ok("настоящая причина выживает",
       "quota" in A.bare_words(banner + "\nERROR: quota exceeded",
                               "Языковая правка куска целиком"),
       repr(A.bare_words(banner + "\nERROR: quota exceeded", "")))
    ok("чужое эхо не режется",
       A.bare_words("user\nсовсем другой текст", "наш промпт") != "",
       repr(A.bare_words("user\nсовсем другой текст", "наш промпт")))
    ok("Hushed — подвид сбоя агента",
       issubclass(A.Hushed, A.AgentError) and not issubclass(A.Hushed, A.Fatal),
       A.Hushed.__mro__)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
