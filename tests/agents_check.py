#!/usr/bin/env python3
"""Проверка строки запуска агента: что уходит в чужую программу.

Модель, усилие и срок ожидания задаются не нами, а ключами чужой командной
строки, и пропажу такого ключа видно только на живом прогоне. Здесь запуск
перехватывается, и строка сверяется по частям. Agy запускается не через
`subprocess.run`, а через `run_envelope` — перехват свой.

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

    real = A.subprocess.run, A.run_envelope

    def spied(kind, spy):
        """Запуск подменён: agy идёт через run_envelope, остальные — run."""
        if kind == "agy":
            A.run_envelope = spy
        else:
            A.subprocess.run = spy

    def restore():
        A.subprocess.run, A.run_envelope = real

    for kind in ("claude", "agy", "codex"):
        spy = Ran(ANSWERS[kind])
        spied(kind, spy)
        try:
            a = A.make_agent(kind, model="проба-модель", timeout=1234,
                             effort="low")
            a.run("система", "запрос")
        finally:
            restore()
        cmd = spy.cmd or []
        ok(f"{kind}: модель названа", "проба-модель" in cmd, cmd)
        ok(f"{kind}: усилие названо",
           any("low" in str(c) for c in cmd), cmd)

    # SUCCESS с пустым текстом — пустой ответ, а не сбой: перевод разберёт
    # его как обрыв на первом блоке, и два таких подряд станут отказом со
    # сменой модели. Ошибкой это считалось раньше — и тихая цензура жгла
    # повторы той же модели с паузами сбоя связи.
    spy = Ran(json.dumps({"status": "SUCCESS", "response": ""}))
    spied("agy", spy)
    try:
        a = A.make_agent("agy", model="проба-модель", timeout=60)
        got, meta = a.run("система", "запрос")
    finally:
        restore()
    ok("agy: пустой SUCCESS — пустой ответ, не ошибка", got == "", repr(got))

    # Фильтр на входе шлюза: промпт до модели не дошёл, повтор бьётся о тот
    # же фильтр. Приходит и с кодом 0 — сообщением вместо ответа.
    spy = Ran(json.dumps({"status": "SUCCESS", "response":
        "The prompt could not be submitted. The prompt contains sensitive "
        "words that violate Google's [Generative AI Prohibited Use Policy]"}))
    spied("agy", spy)
    try:
        a = A.make_agent("agy", model="проба-модель", timeout=60)
        try:
            a.run("система", "запрос")
            got = "приняли"
        except A.Blocked:
            got = ""
    finally:
        restore()
    ok("agy: фильтр шлюза — Blocked, а не ответ", not got, got)

    # Наш срок в 1234 секунды должен дойти до agy: свой у него короче, и
    # обрыв по нему приходит как отказ модели, а не как исчерпанное время.
    spy = Ran(ANSWERS["agy"])
    spied("agy", spy)
    try:
        A.make_agent("agy", model="проба", timeout=1234).run("", "запрос")
    finally:
        restore()
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

    # Эхо длиннее двухсотзнаковой пробы: раньше полный хвост признавался
    # оборванным эхом, и слова после него съедались вместе с ним.
    sent = ("правь кусок по стилевому руководству ниже " * 8).strip()
    ok("длинное эхо целиком: слова после выживают",
       A.bare_words(f"user\n{sent}\nERROR: quota exceeded", sent)
       == "ERROR: quota exceeded",
       repr(A.bare_words(f"user\n{sent}\nERROR: quota exceeded", sent)))

    # Склейка stderr и stdout пробелом приклеивала последнюю строку баннера
    # к строке `user`, и построчные срезки переставали узнавать эхо.
    glue = Ran("user\n" + sent)
    glue.stderr, glue.returncode = "--------", 1
    ok("said не приклеивает stderr к строке user",
       A.bare_words(A.said(glue), sent) == "",
       repr(A.bare_words(A.said(glue), sent)))

    # Лимит codex приходит после эха промпта. Срок возвращения реестр читает
    # из сообщения RateLimited, и эхо выталкивало его за предел в триста
    # знаков — лимит с названным сроком ждали вслепую по четверти часа.
    lim = Ran(f"user\n{sent}\n\n---\n\nзапрос\n"
              "You have hit your usage limit. Resets in 2h5m3s.")
    lim.returncode = 1
    A.subprocess.run = lim
    msg = ""
    try:
        A.make_agent("codex", model="проба").run(sent, "запрос")
    except A.RateLimited as e:
        msg = str(e)
    finally:
        restore()
    ok("лимит codex: в сообщении срок, а не эхо промпта",
       A.reset_after(msg) == 2 * 3600 + 5 * 60 + 3 and "правь кусок" not in msg,
       repr(msg[:80]))

    # Живой codex пишет срок и так: «try again at 9:36 AM» — без слова
    # resets. Непонятый срок — это слепые ожидания по четверти часа.
    got = A.reset_after("You've hit your usage limit. …or try again at 9:36 AM.")
    ok("срок «try again at 9:36 AM» понят",
       0 < got <= 86400, got)

    # Повтор в саму минуту обещанного срока получает то же сообщение со
    # свежепрошедшим временем. Это «окно открывается сейчас», а не «завтра»:
    # книга ждала сутки при живой квоте.
    just = A.time.localtime(A.time.time() - 180)
    h12 = just.tm_hour % 12 or 12
    half = "PM" if just.tm_hour >= 12 else "AM"
    got = A.reset_after(f"…or try again at {h12}:{just.tm_min:02d} {half}.")
    ok("свежепрошедший срок — пауза, а не сутки", 0 < got <= 600, got)

    # ---- OpenRouter: запрос по HTTP, перехвачен на отправке.
    class Posted:
        """Ответ вместо запроса; тело запроса запоминается."""

        def __init__(self, status, events):
            self.status, self.events, self.body = status, events, None

        def __call__(self, body, key, timeout):
            self.body = body
            return self.status, self.events

    real_post, real_key = A.openrouter_post, A.openrouter_key
    A.openrouter_key = lambda: "sk-or-проба"

    def openrouter(status, events, model="deepseek/x:free", effort="low",
                   system="система"):
        spy = Posted(status, events)
        A.openrouter_post = spy
        try:
            a = A.make_agent("openrouter", model=model, effort=effort,
                             wait=0)
            try:
                return spy, a.run(system, "запрос")
            except A.AgentError as e:
                return spy, e
        finally:
            A.openrouter_post = real_post

    stream = [{"model": "deepseek/x:free",
               "choices": [{"delta": {"content": "гото"}, "finish_reason": None}]},
              {"choices": [{"delta": {"content": "во"}, "finish_reason": "stop"}],
               "usage": {"cost": 0.0021}}]
    spy, got = openrouter(200, stream)
    b = spy.body
    ok("openrouter: модель с косой чертой и вариантом — как есть",
       b["model"] == "deepseek/x:free", b["model"])
    ok("openrouter: усилие ушло в reasoning.effort",
       b.get("reasoning", {}).get("effort") == "low", b.get("reasoning"))
    sysm = [m for m in b["messages"] if m["role"] == "system"]
    ok("openrouter: системный промпт помечен для кэша",
       sysm and sysm[0]["content"][0]["text"] == "система"
       and sysm[0]["content"][0].get("cache_control"), sysm)
    ok("openrouter: текст склеен из потока, модель и цена из ответа",
       got == ("готово", {"model": "deepseek/x:free", "cost_usd": 0.0021}), got)
    spy, got = openrouter(200, stream, effort=None, system="")
    ok("openrouter: без усилия и системы — без reasoning и system",
       "reasoning" not in spy.body
       and all(m["role"] != "system" for m in spy.body["messages"]), spy.body)

    # Коды ответа: лимит ждут, закрытый доступ и негодный ключ — Fatal,
    # модерация — Blocked, сбой поставщика — обычная ошибка с повтором.
    err = lambda code, msg, **meta: {"error": {"code": code, "message": msg,
                                               "metadata": meta}}
    _, got = openrouter(429, [err(429, "Rate limit exceeded")])
    ok("openrouter: 429 — RateLimited", isinstance(got, A.RateLimited), got)
    _, got = openrouter(401, [err(401, "User not found.")])
    ok("openrouter: 401 — Fatal, без повторов", isinstance(got, A.Fatal), got)
    _, got = openrouter(403, [err(403, "Input flagged", reasons=["violence"])])
    ok("openrouter: модерация — Blocked", isinstance(got, A.Blocked), got)
    _, got = openrouter(502, [err(502, "Provider returned error")])
    ok("openrouter: 502 — сбой, не Fatal",
       type(got) is A.AgentError, got)
    _, got = openrouter(200, [{"error": {"code": 429, "message": "busy"},
                              "choices": [{"finish_reason": "error"}]}])
    ok("openrouter: ошибка внутри потока — по её коду",
       isinstance(got, A.RateLimited), got)
    _, got = openrouter(200, [{"choices": [{"delta": {"content": ""},
                                            "finish_reason": "content_filter"}]}])
    ok("openrouter: фильтр поставщика — Blocked", isinstance(got, A.Blocked), got)

    # Модель без размышлений отвергает reasoning — повтор без него.
    calls = []

    def twice(body, key, timeout):
        calls.append(dict(body))
        if "reasoning" in body:
            return 400, [err(400, "Reasoning is not supported by this model")]
        return 200, stream
    A.openrouter_post = twice
    try:
        got = A.make_agent("openrouter", model="x/y", effort="low",
                           wait=0).run("с", "з")
    finally:
        A.openrouter_post = real_post
    ok("openrouter: 400 про reasoning — повтор без него",
       len(calls) == 2 and "reasoning" not in calls[1] and got[0] == "готово",
       (len(calls), got))

    A.openrouter_key = lambda: ""
    try:
        A.make_agent("openrouter", model="x/y", wait=0).run("с", "з")
        got = "прошло"
    except A.Fatal as e:
        got = str(e)
    finally:
        A.openrouter_key = real_key
    ok("openrouter: без ключа — Fatal с именем переменной",
       A.OPENROUTER_ENV in got, got)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
