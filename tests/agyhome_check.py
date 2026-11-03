#!/usr/bin/env python3
"""Свой HOME для agy: разговоры конвейера не попадают в историю человека.

agy пишет каждый вызов в свою историю, и `/resume` заполнялся запросами
перевода. Конвейер запускает его с HOME во временной папке: вход и настройки
там — ссылками на настоящие, разговор после ответа удаляется.

    python3 tests/agyhome_check.py
"""
import json
import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import agent as A                            # noqa: E402

CONV = "11111111-2222-3333-4444-555555555555"


class Ran:
    """Ответ вместо запуска agy; запоминает команду и окружение."""

    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0
        self.cmd = self.kw = None

    def __call__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        return self


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:56} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got!r}"))
        bad += not cond

    if os.name == "nt":
        print("  на Windows свой HOME не заводится — проверка пропущена")
        print(f"\nслучаев: {cases}   с расхождениями: {bad}")
        return 0

    d = tempfile.mkdtemp()
    saved = A.AGY_REAL, A.AGY_AGENT_DIR, A.tempfile.tempdir, A.run_envelope
    try:
        real = os.path.join(d, "real", ".gemini", "antigravity-cli")
        os.makedirs(real)
        for n in A.AGY_SHARED:
            open(os.path.join(real, n), "w").write(n)
        tmp = os.path.join(d, "tmp")
        os.makedirs(tmp)
        A.AGY_REAL, A.AGY_AGENT_DIR, A.tempfile.tempdir = real, os.path.join(d, "shared-agents"), tmp

        home = A.agy_home()
        want = os.path.join(tmp, f"booktrans-agy-{os.getuid()}")
        ok("свой HOME во временной папке", home == want, home)
        ok("права только владельцу", stat.S_IMODE(os.stat(home).st_mode) == 0o700,
           oct(os.stat(home).st_mode))
        cli = os.path.join(home, ".gemini", "antigravity-cli")
        ok("вход и настройки — ссылками на настоящие",
           all(os.readlink(os.path.join(cli, n)) == os.path.join(real, n) for n in A.AGY_SHARED))

        # agy, обновляя токен, может заменить ссылку файлом — её вернут.
        tok = os.path.join(cli, A.AGY_SHARED[0])
        os.unlink(tok)
        open(tok, "w").write("старый")
        A.agy_home()
        ok("подменённая ссылка восстановлена", os.path.islink(tok), os.path.islink(tok))

        # Вызов: HOME передан, агент записан в свой HOME, разговор удалён.
        for sub, name in (("conversations", CONV + ".db"), ("presence", CONV + ".lock"),
                          ("conversations", "чужой.db")):
            os.makedirs(os.path.join(cli, sub), exist_ok=True)
            open(os.path.join(cli, sub, name), "w").write("x")
        os.makedirs(os.path.join(cli, "brain", CONV))
        spy = Ran(json.dumps({"conversation_id": CONV, "status": "SUCCESS", "response": "готово"}))
        A.run_envelope = spy
        got, _ = A.make_agent("agy", model="проба", timeout=60).run("система", "запрос")
        ok("ответ получен", got == "готово", got)
        ok("agy запущен со своим HOME", (spy.kw.get("env") or {}).get("HOME") == home,
           (spy.kw.get("env") or {}).get("HOME"))
        ok("агент конвейера — в своём HOME",
           os.path.exists(os.path.join(home, ".gemini", "config", "agents", "booktrans", "agent.md"))
           and not os.path.exists(A.AGY_AGENT_DIR))
        left = [p for sub in ("conversations", "presence", "brain")
                for p in os.listdir(os.path.join(cli, sub)) if p.startswith(CONV)]
        ok("разговор удалён после ответа", not left, left)
        ok("чужой разговор не тронут",
           os.path.exists(os.path.join(cli, "conversations", "чужой.db")))

        # Чужая папка на месте нашей — не пользуемся ею.
        shutil.rmtree(home)
        os.symlink(d, home)
        said = []
        ok("ссылка вместо папки — общий HOME", A.agy_home(log=said.append) is None)
        ok("о чужой папке сказано", said and home in said[0], said)
        os.unlink(home)

        # Входа в файле нет — изоляция не включается, работаем как раньше.
        os.unlink(os.path.join(real, A.AGY_SHARED[0]))
        spy = Ran(json.dumps({"status": "SUCCESS", "response": "готово"}))
        A.run_envelope = spy
        A.make_agent("agy", model="проба", timeout=60).run("система", "запрос")
        ok("без файла входа — общий HOME", spy.kw.get("env") is None, spy.kw.get("env"))
    finally:
        A.AGY_REAL, A.AGY_AGENT_DIR, A.tempfile.tempdir, A.run_envelope = saved
        shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
