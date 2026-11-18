"""Кто какой проход делает: цепочки моделей из ключей командной строки.

Каждому проходу — свой ключ (`--translator`, `--editor`, …), в ключе через
запятую — цепочка: первая модель делает работу, следующие подхватывают её
отказ. Что не названо, берётся из `--model`, а затем из набора агента.
"""
import re
import shutil
import sys

from . import lang
from .agent import (OPENAI_ENV, OPENAI_URL_ENV, OPENROUTER_ENV, make_agent, openai_key,
                    openai_key_file, openai_url, openai_url_file, openrouter_key,
                    openrouter_key_file)

# Семейства моделей: у родственниц общие слепые пятна — кальки, которые не
# видит переводчик, не увидит и старшая модель той же линии. Порядок значим:
# «gpt-oss» проверяется раньше «gpt».
FAMILIES = (("claude", "claude"), ("gemini", "gemini"), ("gemma", "gemini"),
            ("gpt-oss", "gpt-oss"), ("gpt", "gpt"), ("codex", "gpt"), ("o1", "gpt"),
            ("o3", "gpt"), ("o4", "gpt"), ("grok", "grok"), ("deepseek", "deepseek"),
            ("qwen", "qwen"), ("llama", "llama"), ("mistral", "mistral"),
            ("mixtral", "mistral"), ("glm", "glm"), ("kimi", "kimi"))


def family(model):
    """Семейство модели по имени; незнакомое имя — само себе семейство.
    Агент перед двоеточием («codex:gpt-…»), поставщик перед косой
    («google/gemini-…») и усилие в хвосте — части имени, смотрим все."""
    name = (model or "").lower()
    for part in re.split(r"[/:]", name):
        for prefix, fam in FAMILIES:
            if part.startswith(prefix):
                return fam
    return name


AGENTS = ("claude", "agy", "codex", "openrouter", "openai", "cmd")
PROVIDER_ALIASES = {"chatgpt": "codex", "api": "openai"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Проходы опознавательные, а не сочинительные: разобрать вёрстку, увидеть
# порчу распознавания и прочитать страницу умеет и самая дешёвая модель
# поставщика.
CHEAP_ROLES = ("formatter", "ocrfixer", "ocrmodel")
# У claude и openrouter усилие — отдельный ключ, у agy оно вшито в имя модели.
CHEAP_EFFORT = {"claude": "low", "openrouter": "low", "openai": "low"}
# Ключ `--agent` — это, по сути, имя набора умолчаний: какими моделями делать
# проходы у этого поставщика. Названная явно модель сильнее набора, набор
# сильнее умолчания самого агента. Затем он и нужен: `--agent agy` работает
# без обёрток и без десятка ключей в командной строке.
PRESETS = {
    "agy": {
        "model": "gemini-3.8-flash-medium,claude-opus-5-5-medium,claude-sonnet-5-5-medium",
        "formatter": "gemini-3.8-flash-low,claude-sonnet-5-5-low",
        "ocrfixer": "gemini-3.8-flash-low,claude-sonnet-5-5-low",
        "ocrmodel": "gemini-3.8-flash-low,claude-sonnet-5-5-low",
    },
    "claude": {
        "formatter": "claude-sonnet-5",
        "ocrfixer": "claude-sonnet-5",
        "ocrmodel": "claude-sonnet-5",
    },
    # Платится по токенам, потому и в смысловых проходах не Opus: Sonnet
    # впятеро дешевле, а запасным — другой поставщик.
    "openrouter": {
        "model": "anthropic/claude-sonnet-5,google/gemini-3.8-flash",
        "formatter": "google/gemini-3.8-flash",
        "ocrfixer": "google/gemini-3.8-flash",
        "ocrmodel": "google/gemini-3.8-flash",
    },
}
# Ключи, из которых собираются цепочки: проверяются все разом при старте.
CHAIN_KEYS = ("model", "translator", "scout", "editor", "verifier",
              "formatter", "ocrfixer", "ocrmodel", "judge")


def parse_chain(s, agent=None):
    """Цепочка прохода: [(агент, модель, усилие), …].

    Первая модель делает работу, следующие подхватывают её отказ. Пишутся в
    один ключ через запятую, потому что проходов много и на каждый заводить
    второй ключ — это `--translator-fallback`, `--editor-fallback` и так далее
    без конца:

        --editor gemini-3.8-flash-high,claude-opus-5-5-medium

    Через двоеточие перед моделью называется агент, если запасная модель не у
    того поставщика, что основная. Без двоеточия берётся агент из `--agent`:

        --editor gemini-3.8-flash-high,claude:claude-opus-5-5

    Третьей частью через двоеточие можно указать глубину размышлений
    (low/medium/high, а где агент умеет — xhigh и max):

        --editor gemini-pro:high,claude:claude-opus-5:low

    У моделей OpenRouter в имени косая черта, а у варианта — своё двоеточие,
    `deepseek/deepseek-v4:free`. Косая черта и отличает модель от агента:

        --editor openrouter:deepseek/deepseek-v4:free:high
    """
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        parts = [p.strip() for p in part.split(":")]
        name, effort = agent, None
        if parts[-1] in EFFORTS:
            effort = parts.pop()
        if len(parts) > 1 and "/" not in parts[0]:
            name = parts[0] or agent
            parts = parts[1:]
        out.append((PROVIDER_ALIASES.get(name, name), ":".join(parts) if parts else None, effort))
    return out


class Models:
    """Цепочки моделей по ролям прохода.

    Порядок старшинства: ключ роли, затем `--model`, затем набор агента.
    Разметке и корректуре дорогая `--model` не достаётся — им хватает дешёвой
    модели из набора, а иную можно назвать только их собственным ключом.
    """

    def __init__(self, args, log=print, make=make_agent):
        self.args = args
        self.log = log
        self.make = make

    def check(self):
        """Чужой агент и двойное усилие — отвергнуть сейчас, а не на середине
        книги невнятной ошибкой из чужой программы.

        У agy усилие задаётся либо суффиксом в имени модели, либо ключом
        `--effort`, и вместе они не работают. Ключ OpenRouter — здесь же:
        иначе о нём сказал бы только 401 первого запроса.
        """
        a = self.args
        copyedit = getattr(a, "copyedit", False)
        used = {self.specs(role)[0][0] for role in ("scout", "editor")} if copyedit else {a.agent}
        for key in (("scout", "editor", "model") if copyedit else CHAIN_KEYS):
            # Цепочку чтения страниц замыкает `local:pdftotext` — текстовый
            # слой без модели, когда все модели отказали.
            allowed = AGENTS + ("local",) if key == "ocrmodel" else AGENTS
            for name, m, _ in parse_chain(getattr(a, key, None), a.agent):
                if name not in allowed:
                    sys.exit(lang.T("bad_agent", name, ", ".join(AGENTS)))
                if name == "agy" and a.effort \
                        and m and re.search(r"-(low|medium|high|xhigh|max)$", m):
                    sys.exit(lang.T("effort_clash", m, a.effort))
                if not copyedit:
                    used.add(name)
        if "openrouter" in used and not openrouter_key():
            sys.exit(lang.T("openrouter_key", OPENROUTER_ENV, openrouter_key_file()))
        if "openai" in used and not (openai_key() and openai_url()):
            sys.exit(lang.T("openai_key", OPENAI_URL_ENV, OPENAI_ENV,
                            openai_url_file(), openai_key_file()))

    def _agent(self, name, model, effort=None):
        a = self.args
        return self.make(name, model, a.agent_cmd, wait=a.wait,
                         max_wait=a.max_wait, log=self.log,
                         effort=a.effort if effort is None else effort)

    def specs(self, role=None):
        """Все модели прохода: первая работает, следующие подхватывают отказ.

        Отказ — свойство модели, а не текста, и у следующей такого запрета
        может не быть. Порядок значим: первая модель делает всю книгу, до
        второй доходят единицы кусков.
        """
        a = self.args
        named = getattr(a, role, None) if role else None
        # Сверщику по умолчанию — цепочка редактора, а не переводчика: вердикт
        # «ошибся перевод» выносится работе переводчика, и судьёй в собственном
        # деле ему быть нельзя. Редактор к спорным блокам непричастен: он их
        # сознательно не правил, а вынес замечанием.
        if role == "verifier" and not named:
            named = a.editor
        preset = PRESETS.get(a.agent, {})
        cheap = role in CHEAP_ROLES and not named
        s = named or (preset.get(role) if cheap else None) \
            or a.model or preset.get("model")
        return [(n, m, eff or (CHEAP_EFFORT.get(n) if cheap else None))
                for n, m, eff in parse_chain(s, a.agent)] or [(a.agent, None, None)]

    def check_copyedit(self):
        for role in ("scout", "editor"):
            name, model, _ = self.specs(role)[0]
            if name in ("claude", "codex", "agy") and not shutil.which(name):
                sys.exit(lang.T("copyedit_missing_cli", role, name))
            if name == "openai" and not model:
                sys.exit(lang.T("openai_model"))
            if name == "cmd" and not self.args.agent_cmd:
                sys.exit("--provider cmd requires --agent-cmd")

    def chain(self, role=None):
        return [self._agent(n, m, eff) for n, m, eff in self.specs(role)]

    def first(self, role=None):
        """Основная модель прохода — первая в цепочке."""
        return self._agent(*self.specs(role)[0])

    def rest(self, role=None):
        """Кому отдавать кусок, от которого основная модель отказалась."""
        return self.chain(role)[1:]
