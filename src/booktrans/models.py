"""Кто какой проход делает: цепочки моделей из ключей командной строки.

Каждому проходу — свой ключ (`--translator`, `--editor`, …), в ключе через
запятую — цепочка: первая модель делает работу, следующие подхватывают её
отказ. Что не названо, берётся из `--model`, а затем из набора агента.
"""
import re
import sys

from . import lang
from .agent import make_agent

AGENTS = ("claude", "agy", "codex", "cmd")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Проходы опознавательные, а не сочинительные: разобрать вёрстку, увидеть
# порчу распознавания и прочитать страницу умеет и самая дешёвая модель
# поставщика.
CHEAP_ROLES = ("formatter", "ocrfixer", "ocrmodel")
# У claude усилие — отдельный ключ, у agy оно вшито в имя модели.
CHEAP_EFFORT = {"claude": "low"}
# Ключ `--agent` — это, по сути, имя набора умолчаний: какими моделями делать
# проходы у этого поставщика. Названная явно модель сильнее набора, набор
# сильнее умолчания самого агента. Затем он и нужен: `--agent agy` работает
# без обёрток и без десятка ключей в командной строке.
PRESETS = {
    "agy": {
        "model": "gemini-3.1-pro-high,claude-opus-4-6-thinking,claude-opus-5",
        "formatter": "gemini-3.6-flash-low,claude-sonnet-4-6",
        "ocrfixer": "gemini-3.6-flash-low,claude-sonnet-4-6",
        "ocrmodel": "gemini-3.8-flash-low,claude-sonnet-4-6",
    },
    "claude": {
        "formatter": "claude-sonnet-5",
        "ocrfixer": "claude-sonnet-5",
        "ocrmodel": "claude-sonnet-5",
    },
}
# Ключи, из которых собираются цепочки: проверяются все разом при старте.
CHAIN_KEYS = ("model", "translator", "scout", "editor", "verifier",
              "formatter", "ocrfixer", "ocrmodel")


def parse_chain(s, agent=None):
    """Цепочка прохода: [(агент, модель, усилие), …].

    Первая модель делает работу, следующие подхватывают её отказ. Пишутся в
    один ключ через запятую, потому что проходов много и на каждый заводить
    второй ключ — это `--translator-fallback`, `--editor-fallback` и так далее
    без конца:

        --editor gemini-3.1-pro-high,claude-opus-4-6-thinking

    Через двоеточие перед моделью называется агент, если запасная модель не у
    того поставщика, что основная. Без двоеточия берётся агент из `--agent`:

        --editor gemini-3.1-pro-high,claude:claude-opus-5

    Третьей частью через двоеточие можно указать глубину размышлений
    (low/medium/high, а где агент умеет — xhigh и max):

        --editor gemini-pro:high,claude:claude-opus-5:low
    """
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        parts = [p.strip() for p in part.split(":")]
        name, model, effort = agent, None, None
        if parts[-1] in EFFORTS:
            effort = parts.pop()
        if len(parts) == 2:
            name = parts[0] or agent
            model = parts[1]
        elif len(parts) == 1:
            model = parts[0]
        out.append((name, model, effort))
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
        `--effort`, и вместе они не работают.
        """
        a = self.args
        for key in CHAIN_KEYS:
            # Цепочку чтения страниц замыкает `local:pdftotext` — текстовый
            # слой без модели, когда все модели отказали.
            allowed = AGENTS + ("local",) if key == "ocrmodel" else AGENTS
            for name, m, _ in parse_chain(getattr(a, key, None), a.agent):
                if name not in allowed:
                    sys.exit(lang.T("bad_agent", name, ", ".join(AGENTS)))
                if name == "agy" and a.effort \
                        and m and re.search(r"-(low|medium|high|xhigh|max)$", m):
                    sys.exit(lang.T("effort_clash", m, a.effort))

    def _agent(self, name, model, effort=None):
        a = self.args
        return self.make(name, model, a.agent_cmd, wait=a.wait,
                         max_wait=a.max_wait, log=self.log,
                         effort=a.effort if effort is None else effort)

    def chain(self, role=None):
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
        return [self._agent(n, m, eff or (CHEAP_EFFORT.get(n) if cheap else None))
                for n, m, eff in parse_chain(s, a.agent)] \
            or [self._agent(a.agent, None)]

    def first(self, role=None):
        """Основная модель прохода — первая в цепочке."""
        return self.chain(role)[0]

    def rest(self, role=None):
        """Кому отдавать кусок, от которого основная модель отказалась."""
        return self.chain(role)[1:]
