#!/usr/bin/env python3
"""Проверка профилей: файла с ключами командной строки.

Профиль нужен затем, что строка запуска не помещается в экран: четыре роли,
у каждой цепочка из двух-трёх моделей. Своего синтаксиса у файла нет — те же
ключи, что в строке, — и вставляются они в саму строку, поэтому любой ключ
работает сразу, включая те, что появятся потом.

Главное здесь — старшинство. Названное руками обязано быть сильнее профиля,
иначе профиль нельзя поправить на один запуск, не правя файл.

    python3 tests/profile_check.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import cli, lang                             # noqa: E402

PROFILE = """\
# набор для проверки
--agent agy
--translator первая,claude:вторая     # цепочка с чужим агентом
--editor     третья

--jobs 5
"""


def main():
    bad = 0
    lang.set_ui("ru")

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:50} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    d = tempfile.mkdtemp()
    p = os.path.join(d, "проба.conf")
    open(p, "w", encoding="utf-8").write(PROFILE)

    keys = cli.read_profile(p)
    ok("комментарии и пустые строки выброшены",
       keys == ["--agent", "agy", "--translator", "первая,claude:вторая",
                "--editor", "третья", "--jobs", "5"], keys)

    ok("имя ищется по папкам",
       cli.read_profile("проба", roots=[d]) == keys)
    ok("имя с расширением тоже находится",
       cli.read_profile("проба.conf", roots=[d]) == keys)

    # Явный ключ обязан быть сильнее профиля, а для этого профиль встаёт в
    # начало строки: argparse берёт последнее вхождение.
    out = cli.with_profiles(["книга.epub", "--profile", p, "--editor", "своя"])
    ok("ключи профиля встают перед остальными", out[:2] == ["--agent", "agy"], out[:3])
    ok("книга и явные ключи на месте",
       out[-3:] == ["книга.epub", "--editor", "своя"], out[-3:])
    ok("сам --profile из строки убран", "--profile" not in out, out)
    ok("последним стоит явный редактор",
       out.index("--editor") < out.index("своя")
       and out[out.index("своя") - 1] == "--editor"
       and out.count("--editor") == 2, out)

    ok("форма --profile=имя понимается",
       cli.with_profiles([f"--profile={p}", "книга.epub"])[:2] == ["--agent", "agy"])

    two = os.path.join(d, "второй.conf")
    open(two, "w", encoding="utf-8").write("--jobs 9\n")
    got = cli.with_profiles(["--profile", p, "--profile", two, "книга.epub"])
    ok("два профиля разворачиваются по порядку",
       got.count("--jobs") == 2 and got[-3:] == ["--jobs", "9", "книга.epub"], got[-4:])

    # Профиль в профиле не разворачивается: это путь к кольцу и к отладке
    # чужого конфига.
    nested = os.path.join(d, "вложенный.conf")
    open(nested, "w", encoding="utf-8").write("--profile проба\n--jobs 2\n")
    try:
        cli.read_profile(nested)
        ok("профиль в профиле отвергнут", False, "прошёл молча")
    except SystemExit as e:
        ok("профиль в профиле отвергнут", "--profile" in str(e), str(e))

    try:
        cli.read_profile("нет-такого", roots=[d])
        ok("о ненайденном профиле сказано", False, "прошёл молча")
    except SystemExit as e:
        ok("о ненайденном профиле сказано", "нет-такого" in str(e), str(e))

    # Строка без профиля не должна меняться вовсе.
    plain = ["книга.epub", "--to", "ru"]
    ok("без профиля строка не трогается", cli.with_profiles(plain) == plain)

    # Готовые профили в пакете обязаны читаться: они уезжают в сборку, и
    # опечатка в них видна только на запуске.
    root = os.path.join(os.path.dirname(HERE), "profiles")
    names = sorted(n[:-5] for n in os.listdir(root) if n.endswith(".conf"))
    ok("готовые профили на месте", len(names) >= 3, names)
    for n in names:
        keys = cli.read_profile(n, roots=[root])
        ok(f"профиль {n} читается и не пуст",
           bool(keys) and all(k.startswith("-") or " " not in k for k in keys),
           keys)

    import shutil
    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {13 + len(names)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
