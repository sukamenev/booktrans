"""Per-user model routing shared by translation, copyediting and CLI profiles."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

from . import agent
from .models import AGENTS, PROVIDER_ALIASES, parse_chain
from .tune import config_dir


def path():
    return Path(config_dir()) / "providers.json"


def load():
    try:
        settings = json.loads(path().read_text(encoding="utf-8"))
        if not isinstance(settings, dict) or not all(isinstance(v, str) for v in settings.values()):
            raise ValueError("expected a JSON object with string values")
        return settings
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path()}: {exc}")


def save(dest, text):
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent, delete=False)
    tmp = Path(f.name)
    try:
        with f:
            f.write(text + "\n")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def configure(argv=()):
    ap = argparse.ArgumentParser(prog="booktrans configure",
                                 description="Set the default connection and models for all books.")
    ap.add_argument("--show", action="store_true", help="Show saved routing without API keys")
    args = ap.parse_args(argv)
    old = load()
    if args.show:
        print(path(), json.dumps(old, indent=2, ensure_ascii=False), sep="\n")
        return

    def ask(label, default=""):
        try:
            value = input(f"{label} [{default or '—'}]: ").strip()
        except EOFError:
            raise SystemExit("Ввод прерван. Настройки не изменены.") from None
        return "" if value == "-" else value or default

    print("BookTrans · настройки для всех книг. Enter — оставить значение; «-» — очистить.")
    print("Подключение: opencode (OpenCode V2 CLI), chatgpt (Codex CLI), claude, api (OpenAI-совместимый HTTP), "
          "openrouter, agy или cmd (своя команда).")
    provider = ask("Провайдер по умолчанию", old.get("provider", "chatgpt"))
    backend = PROVIDER_ALIASES.get(provider, provider)
    if backend not in AGENTS:
        raise SystemExit(f"Неизвестный провайдер: {provider}")
    same = PROVIDER_ALIASES.get(old.get("provider"), old.get("provider")) == backend
    settings = {"provider": provider}
    print("Для CLI можно оставить модель пустой. Для другой службы в роли укажите "
          "провайдер:модель, например api:gpt-4o-mini или chatgpt: (модель Codex по умолчанию).")
    used = {backend}
    if backend == "opencode":
        print("OpenCode: введите ID из `opencode models` (provider/model) или оставьте общую модель пустой.")
    for role, label in (("model", "Общая модель"), ("translator", "Переводчик"),
                        ("editor", "Редактор"), ("scout", "Справочник имён и терминов")):
        value = ask(label, old.get(role, "") if same else "")
        if value:
            for name, model, _ in parse_chain(value, backend):
                if name not in AGENTS or (name == "openai" and not model):
                    raise SystemExit(f"Неизвестный провайдер или не указана модель API: {value}")
                used.add(name)
            settings[role] = value
    if backend == "openai" and not settings.get("model") and not os.environ.get("BT_MODEL"):
        raise SystemExit("Для API укажите общую модель (--model) или задайте BT_MODEL.")
    files = {}
    if "cmd" in used:
        command = ask("Команда адаптера (ключи передавайте через окружение)", old.get("agent_cmd", ""))
        if not command:
            raise SystemExit("Для cmd требуется команда.")
        settings["agent_cmd"] = command
    if "openai" in used:
        url = ask("Base URL для Chat Completions", agent.openai_url() or "https://api.openai.com/v1")
        addr = urlsplit(url)
        if addr.scheme not in ("http", "https") or not addr.hostname or addr.username or addr.password or addr.query or addr.fragment:
            raise SystemExit("Укажите HTTP(S) Base URL без ключа, например https://api.openai.com/v1")
        if os.environ.get(agent.OPENAI_URL_ENV) and url != agent.openai_url():
            raise SystemExit(f"Измените или снимите {agent.OPENAI_URL_ENV}: она переопределяет сохранённый адрес.")
        files[Path(agent.openai_url_file())] = url
    for name, env, keyfile in (("openai", agent.OPENAI_ENV, agent.openai_key_file),
                               ("openrouter", agent.OPENROUTER_ENV, agent.openrouter_key_file)):
        if name in used:
            print(f"Ключ: {env} или файл {keyfile()}.")
            if sys.stdin.isatty() and not os.environ.get(env):
                secret = getpass.getpass("Новый ключ (ввод скрыт; Enter — пропустить): ").strip()
                if secret:
                    files[Path(keyfile())] = secret
            if (name == "openai" and agent.openai_url() and url != agent.openai_url()
                    and Path(keyfile()).is_file() and Path(keyfile()) not in files):
                raise SystemExit("При смене адреса API замените прежний сохранённый ключ или удалите его файл.")
    for dest, value in files.items():
        save(dest, value)
    save(path(), json.dumps(settings, indent=2, ensure_ascii=False))
    print(f"Сохранено: {path()}")
    if "codex" in used:
        print("Для входа через ChatGPT: установите Codex CLI и один раз выполните codex login.")
    if "opencode" in used:
        print("OpenCode использует собственные подключения. При необходимости выполните /connect в OpenCode.")
