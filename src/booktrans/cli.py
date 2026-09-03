#!/usr/bin/env python3
"""booktrans — перевод книги на русский язык одним запуском.

    ./booktrans книга.epub -p мой_глоссарий.md -o Книга.fb2

Делает всё сам: читает epub/fb2/pdf/txt, разведывает голоса и имена,
переводит, редактирует, предлагает сноски и проверяет факты, собирает fb2
и прогоняет проверки.

Прерывать можно в любой момент: прогресс лежит в рабочей папке и при
перезапуске не переделывается. Упрётся в лимиты подписки — подождёт и
продолжит сам.

Здесь только командная строка: ключи, профили, проверки того, что argparse
не умеет. Сам прогон — в `run`, выбор моделей — в `models`.
"""
import argparse
import os
import shlex
import sys
import time

from . import build, doctor, lang, pipeline, tune
from .models import AGENTS, EFFORTS, Models
from .run import Run, locked
from .tune import config_dir

HERE = os.path.dirname(os.path.abspath(__file__))
# Сноски делает проход редактуры: он и так держит оригинал с переводом
# рядом, а отдельный проход перечитывал бы всю книгу второй раз. Шаг notes
# остался для случая, когда редактуру пропускают.
STEPS = ("ocr", "structure", "ocrfix", "scout", "translate", "edit", "verify",
         "build", "qa")
ALL_STEPS = STEPS + ("notes",)


class Log:
    """Печать с временем в начале строки и подавлением пустых повторов.

    Пустую строку печатает и конец этапа, и заголовок следующего — а откуда
    именно, зависит от того, какие этапы включены. Проще давить повторы здесь,
    чем следить за этим в каждом месте.

    Штамп времени ставится каждой новой строке: по логу видно, когда кусок
    начался и когда кончился, а зависание отличимо от работы без вопросов
    к запущенному процессу. Сообщение, начинающееся с переводов строк,
    получает штамп после них — на своей строке.
    """

    def __init__(self):
        # Ложь, а не истина: самая первая пустая строка нужна — она отбивает
        # вывод конвейера от строки приглашения оболочки.
        self.blank = False
        # Строка не закрыта: прошлый вызов печатал с end="" — продолжение
        # той же строки штамповать нельзя.
        self.mid = False

    def __call__(self, msg="", end="\n"):
        if not msg and end == "\n":
            if self.blank:
                return
            self.blank = True
        else:
            self.blank = False
        if msg and not self.mid:
            i = 0
            while i < len(msg) and msg[i] == "\n":
                i += 1
            if i < len(msg):
                msg = msg[:i] + time.strftime("%H:%M:%S ") + msg[i:]
        out = msg + end
        if out:
            self.mid = not out.endswith("\n")
        print(msg, end=end, flush=True)


def profile_roots():
    """Где искать профили, от старшего к младшему. Как у промптов.

    Последних две — одна и та же папка, увиденная с двух сторон: у
    установленного пакета профили лежат внутри него, а в рабочей копии — в
    корне репозитория, откуда их и забирает сборка пакета.
    """
    out = [os.environ.get("BOOKTRANS_PROFILES"),
           os.path.join(config_dir(), "profiles"),
           os.path.join(HERE, "profiles"),
           os.path.join(HERE, os.pardir, os.pardir, "profiles")]
    return [p for p in out if p and os.path.isdir(p)]


def read_profile(name, roots=None):
    """Ключи из профиля: те же, что в командной строке, по одному на строку.

    Своего синтаксиса у файла нет намеренно. Строки разбираются как командная
    строка и вставляются в неё же — значит любой ключ работает сразу, включая
    те, что появятся потом, и перечислять их отдельным списком не нужно.

        # умный набор для antigravity
        --agent agy
        --translator gemini-3.1-pro-high,claude:claude-opus-5

    Имя ищется по папкам, путь берётся как есть.
    """
    tried = [name]
    if not os.path.exists(name):
        for root in roots or profile_roots():
            tried += [os.path.join(root, name), os.path.join(root, name + ".conf")]
    path = next((p for p in tried if os.path.isfile(p)), None)
    if path is None:
        raise SystemExit(lang.T("prof_nofile", name,
                                ", ".join(roots or profile_roots())))
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            out += shlex.split(line)
    # Профиль в профиле — путь к кольцу и к отладке чужого конфига. Одна
    # ступень, и хватит.
    if any(a == "--profile" or a.startswith("--profile=") for a in out):
        raise SystemExit(lang.T("prof_nested", path))
    return out


def with_profiles(argv):
    """Ключи профиля — в начало командной строки.

    Именно в начало: argparse берёт последнее вхождение ключа, поэтому
    названное руками само собой оказывается сильнее профиля, а профиль —
    сильнее набора агента. Три уровня, и помнить нужно одно правило.
    """
    keys, rest, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--profile" and i + 1 < len(argv):
            keys += read_profile(argv[i + 1])
            i += 2
        elif a.startswith("--profile="):
            keys += read_profile(a.split("=", 1)[1])
            i += 1
        else:
            rest.append(a)
            i += 1
    return keys + rest


def _ui_of(argv, ui):
    """Язык интерфейса из командной строки, если он там назван."""
    for i, a in enumerate(argv):
        if a == "--ui" and i + 1 < len(argv):
            ui = argv[i + 1]
        elif a.startswith("--ui="):
            ui = a.split("=", 1)[1]
    return ui


def expand(argv):
    """Командная строка к разбору: профили развёрнуты, язык интерфейса известен.

    Язык выбирается до разбора ключей: argparse печатает справку сам, и к
    этому мигу --ui уже должен быть известен. Умолчание берётся из окружения:
    так booktrans_ru задаёт русский, ничего не дублируя, а явный ключ всё
    равно сильнее. Профиль разворачивается тоже до разбора: дальше всё идёт
    так, будто человек написал эти ключи руками. Язык перечитывается — профиль
    мог назвать свой.
    """
    ui = _ui_of(argv, os.environ.get("BT_UI", "en"))
    lang.set_ui(ui)              # чтобы ошибка профиля вышла на нужном языке
    argv = with_profiles(argv)
    return argv, _ui_of(argv, ui)


def parser(ui):
    """Все ключи. Умолчания берутся из окружения — тем же путём, что и язык:
    так обёртки вроде bt_claude сводятся к одной строке, а вшитое
    предпочтение одного поставщика не навязывается тому, кто пользуется
    другим."""
    T = lang.set_ui(ui)
    ap = argparse.ArgumentParser(
        description=T("h_desc"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=T("h_epilog"))
    ap.add_argument("book", nargs="?", help=T("h_book"))
    ap.add_argument("--check", action="store_true", help=T("h_check"))
    ap.add_argument("--profile", help=T("h_profile"))
    ap.add_argument("-p", "--prompt", help=T("h_prompt"))
    ap.add_argument("-pt", "--prompt-text", help=T("h_prompt_text"))
    ap.add_argument("-o", "--out", help=T("h_out"))
    ap.add_argument("-w", "--work", help=T("h_work"))
    ap.add_argument("--only", choices=ALL_STEPS, help=T("h_only"))
    ap.add_argument("--skip", default="", help=T("h_skip"))
    ap.add_argument("--chunks", help=T("h_chunks"))
    ap.add_argument("--pages", help="Comma-separated list of page numbers to run OCR on (for PDF)")
    ap.add_argument("--agent", default=os.environ.get("BT_AGENT", "claude"),
                    choices=AGENTS)
    ap.add_argument("--agent-cmd", help=T("h_agentcmd"))
    ap.add_argument("--model", default=os.environ.get("BT_MODEL"), help=T("h_model"))
    ap.add_argument("--effort", choices=EFFORTS,
                    help="Effort level (low, medium, high; xhigh/max where supported)")
    ap.add_argument("--scout", help=T("h_scout"))
    ap.add_argument("--like", action="append", help=T("h_like"))
    ap.add_argument("--name-series", action="store_true", help=T("h_name_series"))
    ap.add_argument("--translator", help=T("h_translator"))
    ap.add_argument("--editor", help=T("h_editor"))
    ap.add_argument("--verifier", help=T("h_verifier"))
    ap.add_argument("--full-verify", action=argparse.BooleanOptionalAction,
                    default=True, help=T("h_full_verify"))
    ap.add_argument("--ocrmodel", help=T("h_ocrmodel"))
    ap.add_argument("--formatter", default=os.environ.get("BT_FORMATTER"),
                    help=T("h_formatter"))
    ap.add_argument("--ocrfixer", default=os.environ.get("BT_OCRFIXER"),
                    help=T("h_ocrfixer"))
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--wait", type=int, default=900, help=T("h_wait"))
    ap.add_argument("--max-wait", type=int, default=86400, help=T("h_maxwait"))
    ap.add_argument("--chunk-words", type=int, default=pipeline.TARGET_WORDS)
    ap.add_argument("--jobs", type=int, default=1, help=T("h_jobs"))
    ap.add_argument("--scout-jobs", type=int, default=1,
                    help=T("h_scout_jobs"))
    ap.add_argument("--refresh", action="store_true", help=T("h_refresh"))
    ap.add_argument("--self-edit", choices=["allow", "last", "never"],
                    default="allow", help=T("h_self_edit"))
    ap.add_argument("--to", default=os.environ.get("BT_TO", "en"), help=T("h_to"))
    ap.add_argument("--ui", default=ui, help=T("h_ui"))
    ap.add_argument("--force-translate", action="store_true", help=T("h_force_lang"))
    ap.add_argument("--force-editing", action="store_true", help=T("h_force_edit"))
    ap.add_argument("--force-injected", action="store_true",
                    help=T("h_force_injected"))
    ap.add_argument("--no-headings", action="store_true", help=T("h_nohead"))
    ap.add_argument("--partial", action="store_true", help=T("h_partial"))
    ap.add_argument("--encoding", help=T("h_encoding"))
    ap.add_argument("--code", choices=("comments", "asis"), default="comments",
                    help=T("h_code"))
    return ap


def steps_of(args):
    """Какие этапы делать: один названный или все, кроме пропущенных."""
    if args.only:
        return [args.only]
    return [s for s in STEPS if s not in args.skip.split(",")]


def check_args(args, models):
    """Чего argparse не проверит: файл книги, чужой агент в цепочке, двойное
    усилие у agy, язык без правил."""
    T = lang.T
    if not os.path.exists(args.book):
        sys.exit(T("h_nofile", args.book))
    models.check()
    if args.to not in lang.available_langs():
        sys.exit(f"нет правил для языка {args.to!r}; есть: "
                 f"{', '.join(lang.available_langs())}. Добавьте langs/{args.to}.md")


def main():
    argv, ui = expand(sys.argv[1:])
    ap = parser(ui)
    args = ap.parse_args(argv)
    T = lang.set_ui(args.ui)
    log = Log()
    log("")
    log(build.banner(args.ui))
    # Настройки из `tune.conf` меняют поведение конвейера молча, а лежат они
    # не в рабочей папке книги: забытая строка объяснит потом много странного.
    if tune.CHANGED:
        log("  " + T("tune_changed", tune.path(),
                     ", ".join(f"{k} {a}→{b}" for k, (a, b) in
                               sorted(tune.CHANGED.items()))))
    log("")

    if args.check:
        log(T("doc_head"))
        return 1 if doctor.check(log, args.agent) else 0
    if not args.book:
        ap.error(T("h_book"))
    models = Models(args, log)
    check_args(args, models)

    work = args.work or os.path.splitext(args.book)[0] + ".work"
    if args.refresh:
        # Обслуживание, не этап: пересчитать отпечатки и выйти.
        pipeline.refresh(work, log, args.to)
        return
    os.makedirs(work, exist_ok=True)
    with locked(work, argv):
        # Какая версия конвейера трогала папку — для архивов: спустя год по
        # этому файлу видно, какие миграции внутренних форматов нужны.
        pipeline.note_version(work)
        Run(args, work, log, models, steps_of(args)).go()


def run():
    """Точка входа: и для установленной команды, и для запуска из папки.
    Возвращает код выхода — `--check` отвечает им, чего-то не хватает или нет."""
    try:
        return main()
    except KeyboardInterrupt:
        # Потокам говорим остановиться и уходим сразу, не дожидаясь их: всё
        # сделанное уже на диске, а ожидание висящего запроса — это минуты
        # и ещё одно Ctrl+C от человека. os._exit минует уборку потоков,
        # из-за которой прежде вылезала трассировка threading.
        pipeline.STOP.set()
        sys.stderr.write("\n" + lang.UI(os.environ.get("BT_UI", "en"))("interrupted") + "\n")
        sys.stderr.flush()
        os._exit(130)
    except RuntimeError as e:
        sys.exit(f"\n\n❌ Ошибка: {e}\n(Перезапустите скрипт, чтобы продолжить с места падения)")


if __name__ == "__main__":
    sys.exit(run())
