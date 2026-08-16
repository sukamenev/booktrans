#!/usr/bin/env python3
"""Проверка цепочки моделей: подхватывает ли запасная.

Запасная модель задаётся ради одного случая — когда первая не справилась.
Случаев «не справилась» два: отказ (модель ответила, но работу не сделала) и
сбой поставщика (502, «high traffic», оборванная связь). Первый переживали
все проходы, второй — только некоторые: он летел мимо цепочки и валил прогон,
хотя запасная модель стояла рядом и была свободна.

Здесь проверяется общая функция и то, что каждый проход умеет её кормить.

    python3 tests/chain_check.py
"""
import inspect
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402

P.RETRY_PAUSE = 0          # выдержка между попытками проверке только мешает
from booktrans import agent as A                            # noqa: E402
from booktrans.agent import AgentError, Fatal               # noqa: E402

# Каждый проход, который обращается к модели, обязан принимать цепочку.
PASSES = ("translate", "edit", "notes", "scout", "code_comments", "headings",
          "detect_structure", "format_marks", "fix_ocr", "condense")


def hush(m="", end="\n"):
    pass


BLOCKS = [{"id": "s01.b0001", "kind": "title", "text": "Chapter One"},
          {"id": "s01.b0002", "kind": "p", "text": "Пробный абзац с dеfектом."},
          {"id": "s01.b0003", "kind": "code",
           "text": "x = 1  # here we count the items"}]
CHUNKS = [{"index": 1, "label": "Проба", "words": 3, "blocks": BLOCKS}]
STYLES = [{"tag": "p", "cls": "tx", "count": 40, "samples": ["Проза."]}]

# Что должна ответить запасная модель, чтобы её ответ разобрался.
ANSWERS = {
    "scout": "## ИМЕНА\n\nПётр = Пётр\n",
    "notes": "[[[NOTE s01.b0002 term]]]\nTERM: дефект\nTEXT: Пояснение.\n",
    "fix_ocr": "[[[F s01.b0002]]]\nORIG: dеfектом\nFIX: дефектом\n",
    "headings": "1. Глава первая\n",
    "detect_structure": "p|tx = p\n",
    "code_comments": "[[[C s01.b0003 1]]]\n"
                     "ORIG: here we count the items\nTR: тут мы считаем предметы\n",
}

# Чем в рабочей папке отпечаталась работа запасной. Одного «её позвали» мало:
# метки протокола однажды поменяли, ответы в этой проверке остались прежними,
# и разбор молча возвращал пустоту — а проверка всё равно была зелёной.
LANDED = {
    # Разведка пишет справочник в папку языка прогона: у `scout` он `ru`.
    "scout": lambda d: "Пётр" in open(f"{d}/ru/scout.md", encoding="utf-8").read(),
    "notes": lambda d: json.load(open(f"{d}/nt/0001.json",
                                      encoding="utf-8"))["notes"],
    "fix_ocr": lambda d: any(json.load(open(f"{d}/ocrfix.json",
                                            encoding="utf-8")).values()),
    "headings": lambda d: "Глава первая" in
                          json.load(open(f"{d}/headings.json",
                                         encoding="utf-8")).values(),
    "detect_structure": lambda d: json.load(open(f"{d}/structure.json",
                                                 encoding="utf-8"))["p|tx"] == "p",
    "code_comments": lambda d: "считаем" in
                               json.load(open(f"{d}/code.json",
                                              encoding="utf-8"))["s01.b0003"],
}


def _tr(work):
    """Готовый перевод: сноскам и комментариям без него работать не над чем."""
    os.makedirs(f"{work}/tr", exist_ok=True)
    import json
    json.dump({"index": 1, "model": "стенд", "cost_usd": 0, "footnotes": [],
               "tr": {b["id"]: "перевод" for b in BLOCKS}},
              open(f"{work}/tr/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)


RUNS = {
    "scout": lambda d, a, b: P.scout(d, BLOCKS, a, "", "з", 1, hush,
                                     fallback=[b]),
    "notes": lambda d, a, b: (_tr(d), P.notes(d, CHUNKS, a, "", "з", 1, hush,
                                              fallback=[b])),
    "fix_ocr": lambda d, a, b: P.fix_ocr(d, BLOCKS, a, "", "з", 1, hush,
                                         fallback=[b]),
    "headings": lambda d, a, b: P.headings(d, BLOCKS, a, "", 1, hush,
                                           fallback=[b]),
    "detect_structure": lambda d, a, b: P.detect_structure(d, STYLES, a, "з", 1,
                                                           hush, fallback=[b]),
    "code_comments": lambda d, a, b: P.code_comments(d, BLOCKS, a, "", "з", 1,
                                                     hush, fallback=[b]),
}


class Says:
    """Отвечает, что велено, или падает тем, чем велено.

    `deaf` — молчит и на проверочный вопрос. Этим отличается модель, у которой
    кончился доступ, от модели, которой не понравился кусок: вторая на «скажи
    ok» отвечает.
    """

    def __init__(self, model, boom=None, answer="готово", deaf=False):
        self.model, self.boom, self.answer = model, boom, answer
        self.deaf, self.calls = deaf, 0

    def run(self, system, user, image=None):
        if user == A.PING_ASK:
            if self.deaf:
                raise self.boom or AgentError("молчит")
            return "ok", {"model": self.model, "cost_usd": 0}
        self.calls += 1
        if self.boom:
            raise self.boom
        return self.answer, {"model": self.model, "cost_usd": 0}


def main():
    bad, seen = 0, 0
    said = []

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    def log(m="", end="\n"):
        said.append(m)

    plain = lambda o: (o, "")                               # noqa: E731

    # Сбой поставщика — повод взять следующую модель, а не кончить прогон.
    first = Says("первая", boom=AgentError("agy вернул 1: high traffic"))
    second = Says("вторая")
    (res, _), meta, _ = P._chain_run([first, second], "", "п", 2, plain, log)
    ok("сбой поставщика уходит следующей модели",
       res == "готово" and meta["model"] == "вторая", meta)
    ok("о переходе сказано вслух",
       any("вторая" in m for m in said), said)

    # Отказ в доступе — тем более: у следующей модели доступ может быть.
    first = Says("первая", boom=Fatal("model not found"))
    second = Says("вторая")
    (res, _), _, _ = P._chain_run([first, second], "", "п", 1, plain, log)
    ok("отказ в доступе уходит следующей модели", res == "готово", res)

    # Первая справилась — до второй дело не доходит, деньги не тратятся.
    first, second = Says("первая"), Says("вторая")
    P._chain_run([first, second], "", "п", 1, plain, log)
    ok("справилась первая — вторую не зовут",
       first.calls == 1 and second.calls == 0, (first.calls, second.calls))

    # Упали все — ошибка наружу. Молча вернуть пустое нельзя: пустой ответ
    # запишется как сделанная работа.
    try:
        P._chain_run([Says("а", boom=AgentError("502")),
                      Says("б", boom=AgentError("502"))], "", "п", 1, plain, log)
        ok("упала вся цепочка — ошибка наружу", False, "молчание")
    except RuntimeError:      # `_run` отдаёт «исчерпаны попытки»
        ok("упала вся цепочка — ошибка наружу", True)

    # Порядок значим: первая делает работу, остальные подхватывают.
    who = [Says("а", boom=AgentError("502")), Says("б", boom=AgentError("502")),
           Says("в")]
    (res, _), meta, _ = P._chain_run(who, "", "п", 1, plain, log)
    ok("идут по цепочке до первой, которая возьмётся",
       meta["model"] == "в" and [w.calls for w in who] == [1, 1, 1],
       [w.calls for w in who])

    # Лимит одной модели не должен останавливать прогон, пока в цепочке есть
    # свободные, а узнав о нём однажды, спрашивать её снова незачем.
    A.LIMITS.forget()
    tired = Says("уставшая", boom=A.RateLimited("usage limit reached, resets in 4h"))
    tired.kind, fresh = "стенд", Says("свежая")
    fresh.kind = "стенд"
    tired = A.WaitingAgent(tired, log=hush)
    fresh = A.WaitingAgent(fresh, log=hush)
    (res, _), meta, _ = P._chain_run([tired, fresh], "", "п", 1, plain, log)
    ok("лимит уводит на запасную, а не в сон",
       res == "готово" and meta["model"] == "свежая", meta)
    ok("время до перезарядки взято из ответа",
       3.9 * 3600 < A.limit_left(tired) <= 4 * 3600 + 60,
       f"{A.limit_left(tired):.0f} с")
    was = tired.inner.calls
    P._chain_run([tired, fresh], "", "п", 1, plain, log)
    ok("занятую модель больше не спрашивают",
       tired.inner.calls == was, f"звали ещё {tired.inner.calls - was} раз")
    A.LIMITS.forget()
    ok("после перезарядки модель снова свободна", A.limit_left(tired) == 0)

    # Лимит — не осечка запроса, а состояние времени: до срока модель ответит
    # тем же самым. Повторы тут ничего не меняют, только печатают о лимите
    # столько раз, сколько задано попыток.
    A.LIMITS.forget()
    once = Says("одна", boom=A.RateLimited("quota reached, resets in 4h"))
    once.kind = "стенд"
    wrapped = A.WaitingAgent(once, log=hush)
    try:
        P._run(wrapped, "", "п", 5, plain, hush)
    except A.RateLimited:
        pass
    ok("лимит не повторяют попытками", once.calls == 1,
       f"обращений {once.calls} вместо одного")

    # Конспект уходит в каждый запрос на перевод, и негодный портит не один
    # кусок, а весь остаток книги. На живой книге агент вернул вместо него
    # собственное размышление с вызовом инструмента, и конвейер это принял.
    good = "Автор рассказывает о старении и о способах его измерить. " * 10
    for name, text, want in (
            ("вызов инструмента", 'Сейчас загляну.\n<invoke name="Read">\n'
                                  '</invoke>\nFile does not exist.', False),
            ("ответ оболочки", good + "\nFile does not exist.", False),
            ("обрывок", "Коротко.", False),
            ("настоящий конспект", good, True)):
        try:
            P._parse_digest(text)
            got = True
        except ValueError:
            got = False
        ok(f"конспект: {name}", got == want, "принят" if got else "отвергнут")

    # Объяснение лежит в json-конверте, а не в его первых знаках. По конверту
    # решалось, лимит это или сбой, и в лог уходило `{"is_error":true,…` —
    # человек не узнавал ни причины, ни того, ждать ему или менять модель.
    stdout = type("R", (), {"stderr": ""})
    env = json.dumps({"is_error": True, "duration_api_ms": 0,
                      "session_id": "b652aebe", "total_cost_usd": 0,
                      "usage": {"input_tokens": 0},
                      "result": "Claude AI usage limit reached"})
    stdout.stdout = env
    ok("из конверта берут объяснение, а не служебные поля",
       A.said(stdout) == "Claude AI usage limit reached", A.said(stdout))
    ok("лимит виден по объяснению", A.limited(A.said(stdout)))
    stdout.stdout = json.dumps({"is_error": True, "usage": {"input_tokens": 0},
                                "result": "Prompt is too long"})
    ok("обычный сбой за лимит не принимают",
       not A.limited(A.said(stdout)), A.said(stdout))

    # Поставщик пишет про лимит по-разному, а цена ошибки несимметрична:
    # неузнанный лимит идёт по ветке сбоя и валит прогон на три часа раньше
    # срока, вместо того чтобы дождаться снятия.
    for msg, want in (("You've hit your session limit · resets 12:50am", True),
                      ("5-hour limit reached ∙ resets 8pm", True),
                      ("usage limit reached, resets in 3h7m54s", True),
                      ("Claude AI usage limit reached", True),
                      ("You are out of credits", True),
                      ("Quota exceeded for this project", True),
                      ("Your balance is too low", True),
                      ("Please retry after 60 seconds", True),
                      ("The service is currently at capacity", True),
                      ("Prompt is too long", False),
                      ("input length exceeds the model's token limit", False),
                      ("API Error: Response stalled mid-stream.", False),
                      ("ответ не json", False)):
        ok(f"лимит опознан: {msg[:34]}", A.limited(msg) == want)

    # Названный час снятия лучше слепой четверти часа, но только если его
    # правильно прочли: «12:50am» — это ноль часов пятьдесят минут, а не
    # полдень, и «resets 8pm» — сегодня, если день ещё не кончился.
    now = A.time.localtime()
    for msg, want in (("resets in 1h30m", 5400),
                      ("resets in 45s", 45),
                      ("resets 12:50am", None),
                      ("resets at 3pm", None),
                      ("resets 23:15", None),
                      ("resets 12pm", None),
                      ("нет ни слова о сроке", 0),
                      ("resets 99:99", 0)):
        got = A.reset_after(msg)
        if want is None:
            h, mi = {"resets 12:50am": (0, 50), "resets at 3pm": (15, 0),
                     "resets 23:15": (23, 15), "resets 12pm": (12, 0)}[msg]
            want = (h - now.tm_hour) * 3600 + (mi - now.tm_min) * 60 - now.tm_sec
            want += 0 if want > 0 else 86400
        ok(f"срок снятия: {msg}", abs(got - want) <= 1, f"{got} вместо {want}")

    # Сбой у поставщика проходит сам, но не за секунду: три куска подряд без
    # выдержки сгорают за один миг и останавливают прогон на ровном месте.
    A.LIMITS.forget()
    slept, real_sleep = [], P.time.sleep
    P.time.sleep = slept.append
    dead = Says("мёртвая", boom=AgentError("claude вернул 1: high traffic"))
    dead.kind = "стенд"
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/prompts", exist_ok=True)
    many = [{"index": i, "label": "", "words": 3, "blocks": BLOCKS}
            for i in range(1, 6)]
    done, _, halted = P.translate(d, many, dead, "", "", 1, hush)
    P.time.sleep = real_sleep
    shutil.rmtree(d, ignore_errors=True)
    ok("после сбоя ждут, прежде чем взять следующий кусок",
       slept[:2] == [60, 180], slept[:3])
    ok("три сбоя подряд останавливают прогон, а не всю книгу",
       halted and done == 0 and dead.calls == 3, (done, halted, dead.calls))

    # Тот же сбой, но модель молчит и на «скажи ok». Список запретных фраз
    # всегда отстаёт от поставщика, и разбирать, какими словами он объяснил
    # запрет, ненадёжно: спросим у него что-нибудь заведомо безобидное.
    # Молчание значит, что дело не в куске, — такое ждут, а не считают отказом.
    A.LIMITS.forget()
    mute = Says("немая", boom=AgentError("claude вернул 1: не пойми что"),
                deaf=True)
    mute.kind, mute.max_wait = "стенд", 5
    ok("молчащая модель признана недоступной", not A.alive(mute))
    ok("недоступность занесена в реестр", A.limit_left(mute) > 0)

    A.LIMITS.forget()
    slept.clear()
    P.time.sleep = slept.append
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/prompts", exist_ok=True)
    P.translate(d, many, mute, "", "", 1, hush)
    P.time.sleep = real_sleep
    shutil.rmtree(d, ignore_errors=True)
    ok("незнакомый запрет уводит в ожидание, а не в выдержку после сбоя",
       slept and 60 not in slept, slept[:4])

    # Лимит сюда не относится: у него свои часы, и ждёт его `_hold`. Выдержка
    # сверху удвоила бы простой.
    A.LIMITS.note(A.key_of(dead), 900)
    slept.clear()
    P.time.sleep = slept.append
    P._cool([dead], [1], hush)
    P.time.sleep = real_sleep
    A.LIMITS.forget()
    ok("под лимитом лишней выдержки нет", slept == [], slept)

    for name in PASSES:
        fn = getattr(P, name, None)
        ok(f"проход {name} принимает цепочку",
           fn is not None and "fallback" in inspect.signature(fn).parameters,
           "нет такого прохода" if fn is None else "нет параметра fallback")

    # А теперь каждый проход прогоняется по-настоящему. Одной подписи мало:
    # цепочка может быть принята и не дойти до вызова — переименованная
    # переменная, затёртое имя. Такое видно только на живом прогоне, и оба
    # раза оно вылезало у человека, а не здесь.
    for name, run in RUNS.items():
        first = Says("первая", boom=AgentError("agy вернул 1: high traffic"))
        second = Says("вторая", answer=ANSWERS[name])
        d = tempfile.mkdtemp()
        os.makedirs(f"{d}/prompts", exist_ok=True)   # это делает cli
        try:
            run(d, first, second)
            done = second.calls > 0 and bool(LANDED[name](d))
            why = "запасную не позвали" if not second.calls else "ответ пропал"
        except Exception as e:                              # noqa: BLE001
            done, why = False, f"{type(e).__name__}: {e}"
        ok(f"работа запасной доехала: {name}", done, why)
        shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
