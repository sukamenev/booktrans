"""Обёртка над агентом. По умолчанию Claude Code, но подойдёт любой.

Агент — это команда, которая читает запрос со stdin и печатает ответ в stdout.
Свой подключается через --agent-cmd, например:
    --agent-cmd 'llm -s {system}'
    --agent-cmd 'my-agent --sys-file {system_file}'
"""
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time

from .lang import T


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


LIMIT_PAT = re.compile(
    r"usage limit|rate limit|limit reached|quota|too many requests|"
    r"resets? (at|in)|try again later|429|overloaded|capacity|"
    r"upgrade your subscription", re.I)

# «Resets in 3h7m54s» — поставщик сам говорит, когда снимет запрет. Ждать
# вслепую по четверти часа глупо, когда названо точное время.
RESET_IN = re.compile(r"resets? in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
                      re.I)


def reset_after(msg):
    """Через сколько секунд поставщик обещает снять запрет. 0 — не сказал."""
    m = RESET_IN.search(msg or "")
    if not m or not any(m.groups()):
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


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
    return " ".join(x for x in ((r.stderr or "").strip(), str(msg).strip()) if x)


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


def limit_left(a):
    return LIMITS.left(key_of(a))


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

    def run(self, system, user):
        left = LIMITS.left(key_of(self))
        if left:
            raise RateLimited(T("lim_known", self.model, max(int(left) // 60, 1)))
        try:
            return self.inner.run(system, user)
        except RateLimited as e:
            # Названо точное время снятия — берём его, а не четверть часа
            # вслепую. Полминуты сверху: часы у нас и у поставщика расходятся,
            # а лишний отказ стоит целой попытки.
            pause = reset_after(str(e))
            LIMITS.note(key_of(self), pause + 30 if pause else self.interval)
            raise


class Agent:
    def run(self, system, user):
        """-> (текст ответа, {'model':..., 'cost_usd':...})"""
        raise NotImplementedError


class ClaudeAgent(Agent):
    """claude -p с json-конвертом: оттуда видно, какая модель отработала."""

    kind = "claude"

    def __init__(self, model=None, timeout=1800, tools="", effort=None):
        self.model = model
        self.timeout = timeout
        self.tools = tools
        self.effort = effort

    def run(self, system, user):
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
        if self.tools:                      # только для узких справочных задач
            cmd[cmd.index("--tools") + 1] = self.tools
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        try:
            r = subprocess.run(cmd, input=user, capture_output=True,
                               text=True, timeout=self.timeout)
        finally:
            os.unlink(tmp.name)
        if r.returncode != 0:
            # Объяснение claude кладёт в stdout, внутрь json, а не в stderr.
            msg = said(r)
            if LIMIT_PAT.search(msg):
                raise RateLimited(msg[:300])
            if FATAL_PAT.search(msg):
                raise Fatal(msg[:300])
            raise AgentError(f"claude вернул {r.returncode}: {msg[:400]}")
        try:
            env = json.loads(r.stdout)
        except json.JSONDecodeError:
            if LIMIT_PAT.search(r.stdout):
                raise RateLimited(r.stdout[:300])
            raise AgentError(f"ответ не json: {r.stdout[:300]}")
        if env.get("is_error"):
            msg = str(env.get("result"))
            if LIMIT_PAT.search(msg):
                raise RateLimited(msg[:300])
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

    def run(self, system, user):
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
                if LIMIT_PAT.search(msg):
                    raise RateLimited(msg[:300])
                raise AgentError(f"агент вернул {r.returncode}: {msg[:400]}")
            return r.stdout, {"model": "custom", "cost_usd": None}
        finally:
            if tmp:
                os.unlink(tmp.name)


class AgyAgent(Agent):
    """Antigravity CLI (agy)."""

    kind = "agy"

    def __init__(self, model=None, timeout=1800, effort=None):
        self.model = model
        self.timeout = timeout
        self.effort = effort

    def run(self, system, user):
        payload = f"{system}\n\n---\n\n{user}" if system else user
        cmd = ["agy", "--dangerously-skip-permissions", "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        try:
            r = subprocess.run(cmd, input=payload, capture_output=True,
                               text=True, timeout=self.timeout)
        except Exception as e:
            raise AgentError(f"ошибка запуска agy: {e}")
        if r.returncode != 0:
            msg = said(r)
            if LIMIT_PAT.search(msg):
                raise RateLimited(msg[:300])
            if FATAL_PAT.search(msg):
                raise Fatal(msg[:300])
            raise AgentError(f"agy вернул {r.returncode}: {msg[:400]}")
        try:
            env = json.loads(r.stdout)
            text = env.get("result") or env.get("response") or r.stdout
        except json.JSONDecodeError:
            text = r.stdout
        return text, {"model": self.model or "agy-default", "cost_usd": None}


class CodexAgent(Agent):
    """OpenAI / Codex CLI."""

    kind = "codex"

    def __init__(self, model=None, timeout=1800, effort=None):
        self.model = model
        self.timeout = timeout
        self.effort = effort

    def run(self, system, user):
        payload = f"{system}\n\n---\n\n{user}" if system else user
        cmd = ["codex", "exec"]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--config", f"model_reasoning_effort={self.effort}"]
        try:
            r = subprocess.run(cmd, input=payload, capture_output=True,
                               text=True, timeout=self.timeout)
        except Exception as e:
            raise AgentError(f"ошибка запуска codex: {e}")
        if r.returncode != 0:
            msg = said(r)
            if LIMIT_PAT.search(msg):
                raise RateLimited(msg[:300])
            raise AgentError(f"codex вернул {r.returncode}: {msg[:400]}")
        return r.stdout, {"model": self.model or "codex-default", "cost_usd": None}


def make_agent(kind="claude", model=None, command=None, timeout=1800,
               wait=900, max_wait=86400, log=print, tools="", effort=None):
    if kind == "claude":
        inner = ClaudeAgent(model, timeout, tools, effort)
    elif kind == "agy":
        inner = AgyAgent(model, timeout, effort)
    elif kind == "codex":
        inner = CodexAgent(model, timeout, effort)
    elif kind == "cmd":
        if not command:
            raise SystemExit("--agent cmd требует --agent-cmd")
        inner = CommandAgent(command, timeout)
    else:
        raise SystemExit(f"неизвестный агент: {kind}")
    return WaitingAgent(inner, wait, max_wait, log) if wait else inner
