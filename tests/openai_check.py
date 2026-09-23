#!/usr/bin/env python3
"""Агент `openai`: любая точка протокола OpenAI. Тело запроса — без добавок
OpenRouter, адрес и ключ — из окружения или файлов, без модели — остановка.

    python3 tests/openai_check.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import agent as A, lang                           # noqa: E402
from booktrans.models import AGENTS, family                      # noqa: E402

for k in list(os.environ):
    if k.startswith(("BT_", "OPENAI_")):
        del os.environ[k]


def main():
    bad = seen = 0
    lang.set_ui("ru")

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("openai в списке агентов", "openai" in AGENTS)
    a = A.make_agent("openai", "deepseek-v4.1-flash", wait=0, effort="high")
    ok("агент собирается, вид и модель на месте",
       a.kind == "openai" and a.model == "deepseek-v4.1-flash" and a.effort == "high")
    body = a.body("система", "вопрос")
    ok("системное сообщение — строкой, без пометки кэша",
       body["messages"][0] == {"role": "system", "content": "система"}, body["messages"][0])
    ok("пользовательское — строкой", body["messages"][1]["content"] == "вопрос")
    ok("усилие — стандартным reasoning_effort, без объекта reasoning",
       body.get("reasoning_effort") == "high" and "reasoning" not in body and "usage" not in body,
       body)
    ok("поток с учётом токенов", body["stream"] and body["stream_options"] == {"include_usage": True})
    ok("предел вывода назван явно", body["max_tokens"] == A.OPENAI_MAX_TOKENS >= 32000, body.get("max_tokens"))
    try:
        A.collect_stream([{"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}],
                           "usage": {"completion_tokens": 16384}}], "m")
        ok("обрыв по длине — ошибка, не обрывок", False)
    except A.AgentError as e:
        ok("обрыв по длине — ошибка, не обрывок", "16384" in str(e), str(e)[:60])
    ok("без усилия поля нет",
       "reasoning_effort" not in A.make_agent("openai", "m", wait=0).body("", "q"))
    ok("семья модели роутера — по имени", family("openai:glm-5.3-flash:high") == "glm")

    # ---- адрес и ключ: окружение сильнее файла, без них — остановка
    d = tempfile.mkdtemp()
    real = A.openai_key_file, A.openai_url_file
    A.openai_key_file = lambda: os.path.join(d, "openai.key")
    A.openai_url_file = lambda: os.path.join(d, "openai.url")
    try:
        ok("нет адреса и ключа — пусто", not A.openai_key() and not A.openai_url())
        try:
            a.run("s", "u")
            ok("без адреса и ключа — Fatal", False)
        except A.Fatal as e:
            ok("без адреса и ключа — Fatal с именами мест",
               "OPENAI_BASE_URL" in str(e) and "openai.key" in str(e), str(e)[:80])
        open(A.openai_key_file(), "w").write("sk-file\n")
        open(A.openai_url_file(), "w").write("https://x.example/v1\n")
        ok("файлы читаются одной строкой",
           A.openai_key() == "sk-file" and A.openai_url() == "https://x.example/v1")
        os.environ["OPENAI_API_KEY"] = "sk-env"
        ok("переменная окружения сильнее файла", A.openai_key() == "sk-env")
        del os.environ["OPENAI_API_KEY"]
        try:
            A.make_agent("openai", None, wait=0).run("s", "u")
            ok("без модели — Fatal", False)
        except A.Fatal as e:
            ok("без модели — Fatal", "openai" in str(e).lower(), str(e)[:60])
    finally:
        A.openai_key_file, A.openai_url_file = real

    # ---- сбор потока общий с OpenRouter
    events = [{"choices": [{"delta": {"content": "Привет, "}}], "model": "glm-5.3-flash"},
              {"choices": [{"delta": {"content": "мир"}, "finish_reason": "stop"}]},
              {"usage": {"prompt_tokens": 10, "completion_tokens": 4,
                         "completion_tokens_details": {"reasoning_tokens": 2}}}]
    text, meta = A.collect_stream(events, "m")
    ok("поток: текст, модель, токены",
       text == "Привет, мир" and meta["model"] == "glm-5.3-flash"
       and meta["tokens"] == {"in": 10, "cached": 0, "out": 4, "reasoning": 2}
       and meta["cost_usd"] is None, meta)

    print(f"\n{'ВСЁ СОВПАДАЕТ' if not bad else f'РАСХОЖДЕНИЙ: {bad}'} ({seen} проверок)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
