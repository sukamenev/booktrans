#!/usr/bin/env python3
"""Проверка командной строки: этапы, номера кусков, замок папки, лог.

Всё это — механика запуска, которая не зовёт модель и ломается тихо:
пропущенный этап не падает, а просто не делается; снятый замок пускает
второй прогон в ту же папку; лишняя пустая строка в логе никого не
остановит, а читать его станет труднее.

    python3 tests/cli_check.py
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import cli, lang                             # noqa: E402
from booktrans.run import chunk_numbers, locked             # noqa: E402

for k in list(os.environ):
    if k.startswith("BT_"):
        del os.environ[k]


def args(*argv):
    return cli.parser("ru").parse_args(["книга.epub", *argv])


def main():
    bad = seen = 0
    lang.set_ui("ru")

    def ok(name, cond, got=""):
        nonlocal bad, seen
        seen += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    # ---- этапы
    got = cli.steps_of(args())
    ok("без ключей — все этапы по порядку", got == list(cli.STEPS), got)
    got = cli.steps_of(args("--only", "build"))
    ok("--only оставляет один", got == ["build"], got)
    got = cli.steps_of(args("--skip", "edit,verify"))
    ok("--skip выбрасывает названные",
       got == [s for s in cli.STEPS if s not in ("edit", "verify")], got)
    ok("notes — только через --only", "notes" not in cli.STEPS
       and cli.steps_of(args("--only", "notes")) == ["notes"])

    # ---- номера кусков
    got = chunk_numbers("5, 6,41-43")
    ok("номера и диапазоны", got == {5, 6, 41, 42, 43}, got)

    # ---- язык и профили до разбора
    argv, ui = cli.expand(["книга.epub", "--ui", "ru"])
    ok("--ui читается до разбора", ui == "ru" and argv == ["книга.epub", "--ui", "ru"])
    d = tempfile.mkdtemp()
    p = os.path.join(d, "п.conf")
    open(p, "w", encoding="utf-8").write("--ui en\n--jobs 3\n")
    argv, ui = cli.expand(["книга.epub", "--profile", p])
    ok("профиль развёрнут в начало, язык взят из него",
       argv == ["--ui", "en", "--jobs", "3", "книга.epub"] and ui == "en", (argv, ui))

    # ---- замок папки
    lock = os.path.join(d, "running.pid")
    with locked(d, ["книга.epub", "--only", "build"]):
        ok("замок записан своим pid",
           open(lock).read().split()[0] == str(os.getpid()))
        try:
            with locked(d):
                pass
            ok("второй прогон в ту же папку не пускается", False)
        except SystemExit:
            ok("второй прогон в ту же папку не пускается", True)
        ok("чужая попытка замка не снимает", os.path.exists(lock))
    ok("замок снят на выходе", not os.path.exists(lock))
    open(lock, "w").write("999999999 мёртвый")     # такого процесса нет
    with locked(d):
        ok("замок упавшего прогона перезаписывается",
           open(lock).read().split()[0] == str(os.getpid()))
    ok("снят и он", not os.path.exists(lock))
    try:
        with locked(d):
            raise SystemExit(1)
    except SystemExit:
        pass
    ok("замок снят и при выходе по ошибке", not os.path.exists(lock))

    # ---- лог
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        log = cli.Log()
        log("")
        log("")
        log("раз")
        log("")
        log("")
        log("два ", end="")
        log("три")
        log("\n\nчетыре")
    lines = out.getvalue().split("\n")
    ok("первая пустая строка печатается", lines[0] == "")
    ok("повторные пустые давятся",
       lines[1].endswith("раз") and lines[2] == "" and lines[3].endswith("два три"),
       lines)
    ok("штамп времени в начале строки",
       len(lines[1]) == len("00:00:00 раз") and lines[1][2] == lines[1][5] == ":",
       lines[1])
    ok("продолжение строки без штампа", "три" in lines[3] and lines[3].count(":") == 2,
       lines[3])
    ok("переводы строк в начале — штамп после них",
       lines[4] == "" and lines[5] == "" and lines[6].endswith("четыре")
       and lines[6][2] == ":", lines[4:7])

    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {seen}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
