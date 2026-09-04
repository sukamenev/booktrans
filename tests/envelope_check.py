#!/usr/bin/env python3
"""Проверка запуска агента по конверту: конец — напечатанный ответ, а не выход.

Agy 1.1.26 после ответа на большой запрос виснет на выходе, конверт уже
напечатав; прежний запуск ждал выхода, сжигал полчаса срока и выбрасывал
готовый ответ. Здесь вместо agy — маленький python-скрипт, ведущий себя
по-разному: выходит сам, виснет после ответа, приносит неудачный конверт,
не отвечает вовсе. Убивается вся группа процессов: ребёнок сам запускает
внука, и после любого исхода в живых не остаётся никого.

    python3 tests/envelope_check.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import agent as A                            # noqa: E402

# Ребёнок печатает конверт и делает, что велено первым аргументом. Внук
# (`sleep`) нужен, чтобы видеть, что убивается группа, а не один процесс.
CHILD = r"""
import subprocess, sys, time
mode, env, rc, pidfile = sys.argv[1:]
kid = subprocess.Popen(["sleep", "300"])
open(pidfile, "w").write(str(kid.pid))
sys.stdin.read()
if mode == "silent":
    time.sleep(300)
sys.stdout.write(env)
sys.stdout.flush()
if mode == "hang":
    time.sleep(300)
sys.exit(int(rc))
"""


def launch(mode, env, rc="0", timeout=5):
    """-> (результат, ошибка срока, секунды, жив ли внук)"""
    pidfile = tempfile.mktemp()
    cmd = [sys.executable, "-c", CHILD, mode, env, rc, pidfile]
    t0 = time.time()
    try:
        r = A.run_envelope(cmd, input="запрос", timeout=timeout, done=A.agy_done)
        err = None
    except subprocess.TimeoutExpired as e:
        r, err = None, e
    kid = int(open(pidfile).read())
    os.unlink(pidfile)
    return r, err, time.time() - t0, alive(kid)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    bad = seen = 0

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    good = '{"status": "SUCCESS", "response": "готово"}'

    r, err, dt, kid = launch("exit", good)
    ok("здоровый: конверт и свой код выхода",
       r and r.returncode == 0 and "готово" in r.stdout and dt < 3, (r, dt))
    ok("здоровый: внук убит вместе с группой", not kid)

    r, err, dt, kid = launch("hang", good)
    ok("повис после ответа: конверт принят за три секунды",
       r and r.returncode == 0 and "готово" in r.stdout and 2.5 < dt < 5,
       (r and r.returncode, dt))
    ok("повис после ответа: никого не осталось", not kid and not A.LIVE)

    r, err, dt, kid = launch("exit", good, rc="3")
    ok("вышел сам с ненулевым кодом: код сохранён", r and r.returncode == 3, r)

    r, err, dt, kid = launch("exit", '{"status": "ERROR", "error": "quota"}',
                             rc="1")
    ok("неудачный конверт: ждём выхода, код и текст на месте",
       r and r.returncode == 1 and "quota" in r.stdout, r)

    r, err, dt, kid = launch("silent", good, timeout=2)
    ok("молчит: срок вышел ошибкой TimeoutExpired",
       err is not None and r is None and 1.5 < dt < 4, (err, dt))
    ok("молчит: сирот после срока нет", not kid and not A.LIVE)

    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
