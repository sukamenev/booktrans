"""Обёртка над агентом. По умолчанию Claude Code, но подойдёт любой.

Агент — это команда, которая читает запрос со stdin и печатает ответ в stdout.
Свой подключается через --agent-cmd, например:
    --agent-cmd 'llm -s {system}'
    --agent-cmd 'my-agent --sys-file {system_file}'
"""
import base64
import http.client
import json
import mimetypes
import os
import re
import select
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .lang import T
from .tune import config_dir


class AgentError(RuntimeError):
    pass


class Fatal(AgentError):
    """Ошибка, которую повторять бессмысленно: нет такой модели, нет доступа,
    негодный ключ. Пять повторов тут только жгут время и деньги."""


# Сообщения, по которым видно, что дело не в случайности
FATAL_PAT = re.compile(
    r"may not exist|no access|not have access|invalid model|unknown model|"
    r"invalid[ _-]?api[ _-]?key|unauthorized|authentication|forbidden|"
    r"недоступн|не существует", re.I)


class RateLimited(AgentError):
    """Кончились лимиты подписки. Не ошибка запроса — надо подождать."""


# Фильтр на входе шлюза: промпт до модели не дошёл. Повтор того же промпта
# в ту же дверь детерминированно бьётся о тот же фильтр — пять попыток
# подряд печатали одно и то же слово в слово.
BLOCKED_PAT = re.compile(
    r"prompt could not be submitted|Prohibited Use Policy", re.I)


class Blocked(AgentError):
    """Промпт отвергнут фильтром поставщика до модели. Повторы бесполезны;
    кусок — модели за другим шлюзом, как при содержательном отказе."""


class Hushed(AgentError):
    """Агент умер, не объяснившись: ненулевой код возврата и ни слова по
    существу. Так гибнут внешние беды — лимит или давка сессий, пришедшие
    раньше, чем поставщик успел напечатать причину; свойство самого куска
    (отказ, кривой ответ) всегда приходит со словами. Такой сбой не приговор
    куску: подождать и переспросить."""


# Об исчерпанной подписке поставщики пишут кто во что горазд: «usage limit»,
# «session limit», «out of credits», «Quota exceeded», «resets 12:50am». Список
# фраз за ними не поспевает, и цена промаха несимметрична: неузнанный запрет
# уходит по ветке сбоя и валит прогон, тогда как принятый за запрет сбой стоит
# четверти часа. Поэтому ловим не фразы, а две вещи, без которых такого
# сообщения не бывает: слово о нехватке — и названный срок возвращения.
SCARCE = (r"\b(?:limits?|quotas?|allowance|credits?|balance|throttl\w*|"
          r"exhaust\w*|insufficient|congestion|429|529)\b|"
          r"too many requests|ran out|out of (?:credits|tokens|quota|capacity)|"
          r"overloaded|at capacity|upgrade your")
# Названный срок сам по себе признак: так пишут только о запрете на время.
COMEBACK = (r"resets?\b|try again|retry (?:after|in|later)|"
            r"available again|come back|wait (?:until|for)\b")
LIMIT_PAT = re.compile(SCARCE + "|" + COMEBACK, re.I)

# Слово «limit» есть и там, где дело не в подписке, а в размере запроса.
# Такое ожиданием не лечится: ждать сутки, чтобы отправить тот же длинный
# кусок, — чистый простой.
SIZE_PAT = re.compile(r"too long|too large|context (?:window|length|limit)|"
                      r"maximum (?:context|length|tokens)|token limit|"
                      r"input length|prompt is too", re.I)


def limited(msg):
    """Это запрет на время, который пройдёт сам?"""
    msg = msg or ""
    return not SIZE_PAT.search(msg) and bool(LIMIT_PAT.search(msg))


# «Resets in 3h7m54s» — поставщик сам говорит, когда снимет запрет. Ждать
# вслепую по четверти часа глупо, когда названо точное время.
RESET_IN = re.compile(r"resets? in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
                      re.I)

# «resets 12:50am», «try again at 9:36 AM» — то же самое, но по часам. Пояс
# поставщик пишет свой, а время показывает наше, местное.
RESET_AT = re.compile(r"(?:resets?(?:\s+at)?|try again at|available(?: again)? at)"
                      r"\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def reset_after(msg):
    """Через сколько секунд поставщик обещает снять запрет. 0 — не сказал."""
    m = RESET_IN.search(msg or "")
    if m and any(m.groups()):
        h, mi, sec = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + sec
    m = RESET_AT.search(msg or "")
    if not m:
        return 0
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    half = (m.group(3) or "").lower()
    if half:
        h = h % 12 + (12 if half == "pm" else 0)
    if h > 23 or mi > 59:
        return 0
    now = time.localtime()
    left = (h - now.tm_hour) * 3600 + (mi - now.tm_min) * 60 - now.tm_sec
    # Время без даты двусмысленно. Повтор, посланный в саму минуту обещанного
    # срока, получает то же «try again at 2:39 PM» — поставщик снимает запрет
    # с запозданием в секунды. Свежепрошедший срок — короткая пауза, а не
    # «завтра в то же время»: сутки ожидания при живой квоте.
    if -600 < left <= 0:
        return 120
    return left if left > 0 else left + 86400


def said(r):
    """Что агент сказал, а не в чём он это принёс.

    Объяснение лежит в json-конверте, в поле `result`, а первые триста знаков
    конверта — служебные поля. Человек видел в логе
    `{"is_error":true,"duration_api_ms":0,…` и не узнавал ни про лимит, ни про
    запрет; по этим же знакам решалось, лимит это или сбой.
    """
    out = (r.stdout or "").strip()
    try:
        env = json.loads(out)
        msg = env.get("result") or env.get("error") or env.get("message") or ""
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg, ensure_ascii=False)
    except Exception:
        msg = out
    # Склейка через перевод строки: пробел приклеивал последнюю строку stderr
    # к первой строке stdout — `user` из эха промпта, — и построчные срезки
    # (CLI_NOISE, bare_words) переставали узнавать своё.
    return "\n".join(x for x in ((r.stderr or "").strip(), str(msg).strip()) if x)


# Codex на каждом запуске печатает в stderr баннер, а в stdout — эхо промпта.
# При молчаливой смерти это всё его наследство, и в сообщении о сбое баннер
# выдаёт себя за объяснение: «сбой: Reading prompt from stdin… user Язы».
CLI_NOISE = re.compile(
    r"Reading prompt from stdin\.\.\.|OpenAI Codex v[\d.]+|^-{4,}\s*$|"
    r"^(?:workdir|model|provider|approval|sandbox|reasoning effort|"
    r"reasoning summaries|session id):.*$", re.M)


def bare_words(msg, sent=""):
    """Что агент сказал по существу: сбой без лесов CLI и эха промпта.

    Эхо режется только когда оно узнано — хвост после строки `user` совпадает
    с началом нашего же промпта: настоящий ответ модели так не начинается,
    а резать наугад значит прятать объяснения.

    Сперва ищется эхо целиком со словами после него, и только потом —
    оборванное. В обратном порядке двухсотзнаковая проба на длинном промпте
    признаёт оборванным любой хвост и съедает слова вместе с эхом.
    """
    msg = CLI_NOISE.sub("", msg or "")
    m = re.search(r"(?m)^user\s*$", msg)
    if m:
        tail = msg[m.end():].strip()
        s = sent.strip()
        if s and len(tail) > len(s) and tail.startswith(s[:200]):
            msg = msg[:m.start()] + tail[len(s):]   # эхо целиком, дальше слова
        elif not tail or (s and s.startswith(tail[:200])):
            msg = msg[:m.start()]          # эхо оборвано на полуслове
    return msg.strip()


class Limits:
    """Кто из моделей сейчас под лимитом и до какого времени.

    Общий на весь прогон. Без него каждый кусок заново упирался бы в ту же
    исчерпанную модель: узнать об этом стоит запроса, а на книге в двести
    кусков — двухсот.

    Ключ — «агент: модель», а не агент целиком. Лимит у поставщика обычно на
    счёт, и по-хорошему следовало бы гасить сразу все его модели, — но если
    квоты всё же раздельные (у Antigravity Gemini и Claude выглядят именно
    так), мы отключили бы работающее на часы. Пусть лучше каждая модель
    узнаёт про запрет сама, ценой одного запроса.

    Под замком: при `--jobs 5` в него пишут пять потоков.
    """

    def __init__(self):
        self._until = {}
        self._lock = threading.Lock()

    def note(self, key, seconds):
        """Запомнить, что эта модель занята столько-то секунд."""
        with self._lock:
            self._until[key] = max(self._until.get(key, 0),
                                   time.time() + max(seconds, 0))

    def left(self, key):
        """Сколько ей ещё быть занятой. 0 — свободна."""
        with self._lock:
            return max(0, self._until.get(key, 0) - time.time())

    def forget(self):
        with self._lock:
            self._until.clear()


LIMITS = Limits()


def key_of(a):
    """Ключ модели в реестре лимитов."""
    return f"{getattr(a, 'kind', '?')}:{getattr(a, 'model', None) or '—'}"


def label(a):
    """Модель с усилием для лога, как в ключе: `claude-opus-5:medium`.
    Принимает агента или мету его ответа; усилие, вшитое в имя модели
    (agy), не повторяется."""
    get = a.get if isinstance(a, dict) else lambda k, d=None: getattr(a, k, d)
    model, effort = get("model") or "?", get("effort")
    return f"{model}:{effort}" if effort else model


def limit_left(a):
    return LIMITS.left(key_of(a))


# Текст пробы живости — из prompts, как и весь текст для модели. Константы,
# а не функция: стенды сверяют присланный вопрос с PING_ASK на равенство.
from .lang import prompt as _prompt

PING_SYS = _prompt("ping_sys")[0]
PING_ASK = _prompt("ping_ask")[0]


def alive(a):
    """Отвечает ли модель хоть что-нибудь — на вопрос, в котором нечему не
    понравиться.

    Список запретных фраз всегда отстаёт: поставщик волен завтра написать про
    исчерпанную подписку словами, которых в нём нет, и тогда запрет пойдёт по
    ветке сбоя и остановит прогон на всю ночь. Поэтому там, где цена ошибки
    высока, мы не гадаем по сообщению, а спрашиваем: два слова без книги, без
    справочника и без правил. Ответила — дело было в куске. Не ответила и на
    это — дело в доступе, и ждать надо, а не считать отказом.

    Молчание сразу заносим в реестр: конвейер спрашивает не чаще раза на кусок,
    и без записи он пошёл бы к той же модели со следующим куском.
    """
    try:
        out, _ = a.run(PING_SYS, PING_ASK)
        if (out or "").strip():
            return True
    except Fatal:
        raise           # нет доступа или такой модели: ждать нечего
    except Exception:                                        # noqa: BLE001
        pass
    LIMITS.note(key_of(a), getattr(a, "interval", 900))
    return False


class WaitingAgent:
    """Оборачивает агента: помнит лимиты и не тратит запрос впустую.

    Ждать здесь нельзя. Ожидание внутри модели не видно снаружи, и цепочка
    не узнаёт, что первая встала: прогон послушно спал по четыре часа, а
    запасная модель стояла рядом свободная. Поэтому здесь только учёт —
    сколько ждать и ждать ли вообще, решает тот, кто держит всю цепочку.
    """

    def __init__(self, inner, interval=900, max_wait=86400, log=print):
        self.inner = inner
        self.interval = interval
        self.max_wait = max_wait
        self.log = log

    @property
    def model(self):
        """Имя модели наружу: по нему конвейер узнаёт, кто сделал кусок, и
        отдаёт его на редактуру той же модели."""
        return getattr(self.inner, "model", None)

    @property
    def kind(self):
        return getattr(self.inner, "kind", "?")

    @property
    def effort(self):
        return getattr(self.inner, "effort", None)

    def run(self, system, user, image=None):
        left = LIMITS.left(key_of(self))
        if left:
            raise RateLimited(T("lim_known", self.model, max(int(left) // 60, 1)))
        try:
            text, meta = self.inner.run(system, user, image=image)
        except RateLimited as e:
            # Названо точное время снятия — берём его, а не четверть часа
            # вслепую. Полминуты сверху: часы у нас и у поставщика расходятся,
            # а лишний отказ стоит целой попытки.
            pause = reset_after(str(e))
            LIMITS.note(key_of(self), pause + 30 if pause else self.interval)
            raise
        # Усилие — свойство запуска, а не ответа: в мету оно попадает отсюда,
        # чтобы лог назвал модель вместе с ним.
        return text, (dict(meta, effort=self.effort) if self.effort else meta)


LIVE = set()            # наши группы процессов: при Ctrl+C убить всех


def _killpg(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def kill_all():
    """Дети в своём сеансе, сигнала терминала не видят — убиваются отсюда."""
    for p in list(LIVE):
        _killpg(p)


def run_envelope(cmd, input, timeout, done):
    """Запуск агента, отвечающего json-конвертом: ждём конверт, а не выход.

    agy 1.1.26 после ответа на большой запрос не выходит — сбрасывает свою
    базу и виснет, конверт уже напечатав. Ждать выхода значило сжечь полчаса
    срока и выбросить готовый ответ. Здоровому даются три секунды выйти самому.
    Ребёнок в своём сеансе, и убивается вся группа: под обёрткой sudo
    одиночный kill до самой программы не доходит, повисшие жили дальше.
    """
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True)
    LIVE.add(p)
    errs = []

    def feed():
        try:
            p.stdin.write(input.encode())
            p.stdin.close()
        except OSError:                 # умер, не дочитав: причина придёт кодом
            pass
    threading.Thread(target=feed, daemon=True).start()
    hear = threading.Thread(target=lambda: errs.append(p.stderr.read()), daemon=True)
    hear.start()
    out, deadline, got = b"", time.time() + timeout, False
    try:
        while not got:
            if time.time() > deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            ready = select.select([p.stdout], [], [], 1)[0]
            if not ready and p.poll() is not None:
                # Вышел, а трубу держит его потомок — конца файла не будет.
                ready = select.select([p.stdout], [], [], 0)[0]
                if not ready:
                    break
            if not ready:
                continue
            chunk = os.read(p.stdout.fileno(), 1 << 16)
            if not chunk:
                break
            out += chunk
            got = done(out)
        if got:
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                pass
    finally:
        _killpg(p)
        p.wait()
        LIVE.discard(p)
    hear.join(2)
    rc = 0 if got and p.returncode < 0 else p.returncode
    return subprocess.CompletedProcess(
        cmd, rc, out.decode("utf-8", "replace"),
        b"".join(errs).decode("utf-8", "replace"))


class Agent:
    _default_cache = {}

    def default_model(self):
        return "unknown"

    def run(self, system, user, image=None):
        """-> (текст ответа, {'model':..., 'cost_usd':...})"""
        raise NotImplementedError


class ClaudeAgent(Agent):
    """claude -p с json-конвертом: оттуда видно, какая модель отработала."""

    kind = "claude"

    def default_model(self):
        if "claude" not in Agent._default_cache:
            try:
                r = subprocess.run(["claude", "-p", "hi"], capture_output=True, text=True, timeout=15)
                env = json.loads(r.stdout)
                usage = env.get("modelUsage") or {}
                main = {k: v for k, v in usage.items() if not k.startswith("claude-haiku")}
                Agent._default_cache["claude"] = list(main)[0] if main else "claude-3-5-sonnet-20240620"
            except Exception:
                Agent._default_cache["claude"] = "claude-3-5-sonnet-20240620"
        return Agent._default_cache["claude"]

    def __init__(self, model=None, timeout=1800, tools="", effort=None):
        self.model = model or self.default_model()
        self.timeout = timeout
        self.tools = tools
        self.effort = effort

    def run(self, system, user, image=None):
        # Системный промпт передаём файлом, а не аргументом: у одного
        # аргумента командной строки предел около 128 КБ, а справочник
        # по книге легко перерастает его — процесс падает с
        # «Argument list too long» уже после дорогой разведки.
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md",
                                          encoding="utf-8", delete=False)
        tmp.write(system)
        tmp.close()
        # Инструменты отключены наглухо. Конвейер скармливает агенту текст
        # из чужих файлов, в том числе пиратских сборок, а по умолчанию у
        # `claude -p` работает Bash — проверено, выполняет по-настоящему.
        # Значит внедрённая в книгу команда исполнилась бы. Переводу и
        # редактуре инструменты не нужны вовсе.
        # --strict-mcp-config без --mcp-config значит «никаких MCP-серверов».
        # Без него на каждый запрос поднимаются серверы из настроек
        # пользователя: секунды на запуск, сотни мегабайт памяти и лишние
        # инструменты в руках у агента, который переводит чужой текст.
        cmd = ["claude", "-p", "--output-format", "json",
               "--tools", "", "--strict-mcp-config",
               "--append-system-prompt-file", tmp.name]
        # If an image is provided, Claude must have the Read tool to view it
        actual_tools = self.tools
        if image:
            if "Read" not in (actual_tools or ""):
                actual_tools = (actual_tools + ",Read").strip(",")
            img_dir = os.path.dirname(os.path.abspath(image))
            cmd += ["--add-dir", img_dir]
            
        if actual_tools:                      # только для узких справочных задач
            cmd[cmd.index("--tools") + 1] = actual_tools
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        
        if image:
            from . import lang
            user += "\n\n" + lang.prompt("image_read_vision")[0].format(
                path=os.path.abspath(image))
            
        try:
            r = subprocess.run(cmd, input=user, capture_output=True,
                               text=True, timeout=self.timeout)
        finally:
            os.unlink(tmp.name)
        if r.returncode != 0:
            # Объяснение claude кладёт в stdout, внутрь json, а не в stderr.
            msg = said(r)
            if limited(msg):
                raise RateLimited(CLI_NOISE.sub("", msg).strip()[:300])
            if FATAL_PAT.search(msg):
                raise Fatal(msg[:300])
            raise AgentError(f"claude вернул {r.returncode}: {msg[:400]}")
        try:
            env = json.loads(r.stdout)
        except json.JSONDecodeError:
            if limited(r.stdout):
                raise RateLimited(r.stdout[:300])
            raise AgentError(f"ответ не json: {r.stdout[:300]}")
        if env.get("is_error"):
            msg = str(env.get("result"))
            if limited(msg):
                raise RateLimited(CLI_NOISE.sub("", msg).strip()[:300])
            if FATAL_PAT.search(msg):
                raise Fatal(msg[:300])
            raise AgentError(f"агент сообщил об ошибке: {msg[:300]}")
        usage = env.get("modelUsage") or {}
        main = {k: v for k, v in usage.items() if not k.startswith("claude-haiku")}
        return env.get("result") or "", {
            "model": max(main, key=lambda k: main[k].get("outputTokens", 0), default=None),
            "cost_usd": env.get("total_cost_usd"),
        }


class CommandAgent(Agent):
    """Произвольная команда. Плейсхолдеры в шаблоне:

    {system}       — системный промпт как аргумент
    {system_file}  — путь к временному файлу с системным промптом

    Если ни один не указан, системный промпт приклеивается к началу stdin.
    """

    kind = "cmd"

    def __init__(self, template, timeout=1800):
        self.template = template
        self.timeout = timeout

    def run(self, system, user, image=None):
        tmp = None
        tpl = self.template
        if "{system_file}" in tpl:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".md",
                                              encoding="utf-8", delete=False)
            tmp.write(system)
            tmp.close()
            tpl = tpl.replace("{system_file}", shlex.quote(tmp.name))
        if "{system}" in tpl:
            tpl = tpl.replace("{system}", shlex.quote(system))
            payload = user
        elif tmp:
            payload = user
        else:
            payload = system + "\n\n" + user
        try:
            r = subprocess.run(tpl, shell=True, input=payload,
                               capture_output=True, text=True, timeout=self.timeout)
            if r.returncode != 0:
                msg = said(r)
                if limited(msg):
                    raise RateLimited(msg[:300])
                raise AgentError(f"агент вернул {r.returncode}: {msg[:400]}")
            return r.stdout, {"model": "custom", "cost_usd": None}
        finally:
            if tmp:
                os.unlink(tmp.name)


def agy_done(out):
    """Удачный конверт напечатан целиком. Неудачный дожидается выхода:
    сбой объясняют код возврата и stderr."""
    try:
        env = json.loads(out)
    except ValueError:
        return False
    return isinstance(env, dict) and env.get("status") == "SUCCESS"


class AgyAgent(Agent):
    """Antigravity CLI (agy)."""

    kind = "agy"

    def default_model(self):
        if "agy" not in Agent._default_cache:
            try:
                r = subprocess.run(["agy", "--sandbox", "--output-format", "json"], input="hi", capture_output=True, text=True, timeout=15)
                env = json.loads(r.stdout)
                Agent._default_cache["agy"] = env.get("model", "gemini-3.1-pro-high")
            except Exception:
                Agent._default_cache["agy"] = "gemini-3.1-pro-high"
        return Agent._default_cache["agy"]

    def __init__(self, model=None, timeout=1800, effort=None):
        self.model = model or self.default_model()
        self.timeout = timeout
        self.effort = effort

    def run(self, system, user, image=None):
        payload = f"{system}\n\n---\n\n{user}" if system else user
        # Свой срок у agy — пять минут, и думающая модель на большом куске в
        # него не укладывалась: ответ обрывался на полуслове, а выглядело как
        # отказ. Срок должен быть один, наш.
        cmd = ["agy", "--sandbox", "--output-format", "json",
               "--print-timeout", f"{self.timeout}s"]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        if image:
            from . import lang
            payload += "\n\n" + lang.prompt("image_read_tools")[0].format(
                path=os.path.abspath(image))
        try:
            r = run_envelope(cmd, input=payload, timeout=self.timeout,
                             done=agy_done)
        except Exception as e:
            raise AgentError(f"ошибка запуска agy: {e}")
        if r.returncode != 0:
            msg = said(r)
            if limited(msg):
                plain = bare_words(msg, payload)
                raise RateLimited((plain or CLI_NOISE.sub("", msg).strip())[:300])
            if FATAL_PAT.search(msg):
                raise Fatal(msg[:300])
            if BLOCKED_PAT.search(msg):
                raise Blocked(CLI_NOISE.sub("", msg).strip()[:300])
            plain = bare_words(msg, payload)
            if not plain:
                raise Hushed(f"agy вернул {r.returncode} без объяснения")
            raise AgentError(f"agy вернул {r.returncode}: {plain[:400]}")
        try:
            env = json.loads(r.stdout)
            # SUCCESS с пустым текстом — пустой ответ, а не сбой: перевод
            # разберёт его как «оборван на первом блоке», и два таких подряд
            # честно станут отказом с именем модели. Ошибкой это считалось
            # раньше — и тихая цензура жгла повторы той же модели с паузами
            # сбоя связи, без пометки отказа и смены модели. Конверт целиком
            # не подставляется: разбор жаловался бы «ответ без маркеров» с
            # json вместо текста в сообщении.
            text = str(env.get("result") or env.get("response") or "")
        except json.JSONDecodeError:
            text = r.stdout
        # Фильтр шлюза приходит и с кодом 0 — сообщением вместо ответа.
        if BLOCKED_PAT.search(text[:400]):
            raise Blocked(text.strip()[:300])
        return text, {"model": self.model, "cost_usd": None}


class CodexAgent(Agent):
    """OpenAI / Codex CLI."""

    kind = "codex"

    def default_model(self):
        if "codex" not in Agent._default_cache:
            try:
                r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "-c", 'web_search="disabled"', "--sandbox", "read-only"], input="hi", capture_output=True, text=True, timeout=15)
                m = re.search(r"^model:\s+(\S+)", r.stderr, re.MULTILINE)
                Agent._default_cache["codex"] = m.group(1) if m else "gpt-5.6-sol"
            except Exception:
                Agent._default_cache["codex"] = "gpt-5.6-sol"
        return Agent._default_cache["codex"]

    def __init__(self, model=None, timeout=1800, effort=None):
        self.model = model or self.default_model()
        self.timeout = timeout
        self.effort = effort

    def run(self, system, user, image=None):
        payload = f"{system}\n\n---\n\n{user}" if system else user
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "-c", 'web_search="disabled"', "--sandbox", "read-only"]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--config", f"model_reasoning_effort={self.effort}"]
        if image:
            cmd += ["-i", image]
        try:
            r = subprocess.run(cmd, input=payload, capture_output=True,
                               text=True, timeout=self.timeout)
        except Exception as e:
            raise AgentError(f"ошибка запуска codex: {e}")
        if r.returncode != 0:
            msg = said(r)
            if limited(msg):
                # Леса и эхо срезаем и здесь: в трёхстах знаках лимита должен
                # быть виден срок возвращения, а не баннер с workdir и не
                # начало нашего же промпта.
                plain = bare_words(msg, payload)
                raise RateLimited((plain or CLI_NOISE.sub("", msg).strip())[:300])
            plain = bare_words(msg, payload)
            if not plain:
                # Так codex гибнет, когда о лимит бьются несколько сессий
                # разом: часть получает внятный отказ, часть обрывается до
                # печати причины.
                raise Hushed(f"codex вернул {r.returncode} без объяснения")
            raise AgentError(f"codex вернул {r.returncode}: {plain[:400]}")
        actual_model = self.model
        if r.stderr:
            m = re.search(r"^model:\s+(\S+)", r.stderr, re.MULTILINE)
            if m:
                actual_model = m.group(1)
        return r.stdout, {"model": actual_model, "cost_usd": None}


OPENROUTER_ENV = "OPENROUTER_API_KEY"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def openrouter_key_file():
    return os.path.join(config_dir(), "openrouter.key")


def openrouter_key():
    """Ключ — из переменной окружения или из файла в папке настроек. Не из
    командной строки: `running.pid` хранит её целиком, и `ps` показывает всем."""
    key = os.environ.get(OPENROUTER_ENV, "").strip()
    if key:
        return key
    try:
        with open(openrouter_key_file(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def openrouter_post(body, key, timeout):
    """Запрос к OpenRouter потоком. -> (код HTTP, [события json]).

    Потоком, а не одним ответом: думающая модель на большом куске молчит
    минутами, и посредники рвут тихое соединение — а со служебными строками
    `: OPENROUTER PROCESSING` оно живо. Срок один на весь ответ.
    """
    req = urllib.request.Request(
        OPENROUTER_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/sukamenev/booktrans",
                 "X-Title": "BookTrans"})
    events, deadline = [], time.time() + timeout
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                if time.time() > deadline:
                    raise AgentError(T("or_silent", timeout))
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue                # пустые строки и `: PROCESSING`
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
            return r.status, events
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            env = json.loads(raw)
        except ValueError:
            env = None
        if not isinstance(env, dict):
            env = {"error": {"message": raw[:300]}}
        return e.code, [env]
    except (OSError, http.client.HTTPException) as e:
        raise AgentError(f"openrouter: {e}")


def openrouter_error(code, err):
    """Каким сбоем считать ответ OpenRouter с кодом `code`."""
    err = err if isinstance(err, dict) else {"message": str(err)}
    try:
        code = int(code or 0)
    except ValueError:
        code = 0
    msg = f"openrouter {code}: {err.get('message') or '?'}"[:300]
    meta = err.get("metadata") or {}
    if code == 429:
        return RateLimited(msg)
    # 403 — это и модерация, и закрытый доступ; модерацию выдают причины.
    if code == 403 and (meta.get("reasons") or re.search(r"moderat|flagged", msg, re.I)):
        return Blocked(msg)
    if code in (400, 401, 402, 403, 404):
        return Fatal(msg)
    return AgentError(msg)


class OpenRouterAgent(Agent):
    """OpenRouter: один HTTP-вход к моделям сотни поставщиков, плата по
    токенам. Имена моделей — `поставщик/модель`, варианты через двоеточие:
    `deepseek/deepseek-v4:free`."""

    kind = "openrouter"

    def default_model(self):
        return "anthropic/claude-sonnet-5"

    def __init__(self, model=None, timeout=1800, effort=None):
        self.model = model or self.default_model()
        self.timeout = timeout
        self.effort = effort

    def run(self, system, user, image=None):
        key = openrouter_key()
        if not key:
            raise Fatal(T("openrouter_key", OPENROUTER_ENV, openrouter_key_file()))
        ask = [{"type": "text", "text": user}]
        if image:
            mime = mimetypes.guess_type(image)[0] or "image/png"
            with open(image, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ask.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}})
        messages = [{"role": "user", "content": ask}]
        if system:
            # Пометка для кэша: справочник по книге один на все куски, а без
            # пометки Anthropic берёт за него полную цену каждый раз. Кому
            # пометка не нужна, тот её не замечает.
            messages.insert(0, {"role": "system", "content": [
                {"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}]})
        body = {"model": self.model, "messages": messages, "stream": True,
                "usage": {"include": True}}
        if self.effort:
            body["reasoning"] = {"effort": self.effort, "exclude": True}
        status, events = openrouter_post(body, key, self.timeout)
        err = (events[0].get("error") or events[0]) if events else {}
        if status == 400 and "reasoning" in body \
                and "reasoning" in str(err.get("message")).lower():
            # Модель не думает вовсе — усилие ей ни к чему.
            del body["reasoning"]
            status, events = openrouter_post(body, key, self.timeout)
            err = (events[0].get("error") or events[0]) if events else {}
        if status != 200:
            raise openrouter_error(status, err)
        text, model, cost, finish = [], self.model, None, None
        for ev in events:
            if ev.get("error"):
                raise openrouter_error(ev["error"].get("code"), ev["error"])
            for ch in ev.get("choices") or ():
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    text.append(piece)
                finish = ch.get("finish_reason") or finish
            model = ev.get("model") or model
            if ev.get("usage"):
                cost = ev["usage"].get("cost")
        if finish == "content_filter":
            raise Blocked(T("or_filter", model))
        return "".join(text), {"model": model, "cost_usd": cost}


def make_agent(kind="claude", model=None, command=None, timeout=1800,
               wait=900, max_wait=86400, log=print, tools="", effort=None):
    if kind == "claude":
        inner = ClaudeAgent(model, timeout, tools, effort)
    elif kind == "agy":
        inner = AgyAgent(model, timeout, effort)
    elif kind == "codex":
        inner = CodexAgent(model, timeout, effort)
    elif kind == "openrouter":
        inner = OpenRouterAgent(model, timeout, effort)
    elif kind == "local":
        class LocalAgent(Agent):
            def __init__(self, model):
                self.kind = model or "unknown_local"
            def run(self, sys, user, image=None):
                raise AgentError(f"LocalAgent({self.kind}) is a pseudo-agent. It should be handled specially.")
        inner = LocalAgent(model)
    elif kind == "cmd":
        if not command:
            raise SystemExit("--agent cmd требует --agent-cmd")
        inner = CommandAgent(command, timeout)
    else:
        raise SystemExit(f"неизвестный агент: {kind}")
    return WaitingAgent(inner, wait, max_wait, log) if wait else inner
