#!/usr/bin/env python3
"""booktrans — перевод книги на русский язык одним запуском.

    ./booktrans книга.epub -p мой_глоссарий.md -o Книга.fb2

Делает всё сам: читает epub/fb2/pdf/txt, разведывает голоса и имена,
переводит, редактирует, предлагает сноски и проверяет факты, собирает fb2
и прогоняет проверки.

Прерывать можно в любой момент: прогресс лежит в рабочей папке и при
перезапуске не переделывается. Упрётся в лимиты подписки — подождёт и
продолжит сам.
"""
import argparse
import atexit
import json
import os
import re
import sys

from . import build, doctor, extract, lang, pipeline
from .agent import make_agent

HERE = os.path.dirname(os.path.abspath(__file__))
# Сноски делает проход редактуры: он и так держит оригинал с переводом
# рядом, а отдельный проход перечитывал бы всю книгу второй раз. Шаг notes
# остался для случая, когда редактуру пропускают.
STEPS = ("structure", "ocrfix", "scout", "translate", "edit", "build", "qa")
ALL_STEPS = STEPS + ("notes",)


def log(msg="", end="\n"):
    print(msg, end=end, flush=True)


# Что понимает шапка файла указаний. Ключ не из списка молча пропал бы:
# написал человек title_ru вместо title_target — и книга вышла бы с
# заглавием оригинала, а почему, разбирайся сам.
META_KEYS = {"title", "author", "title_target", "author_target", "series",
             "series_no", "year", "publisher", "isbn", "lang", "uid"}


def read_prompt(path, log=None, text=None):
    """Указания заказчика: файл, строка или то и другое.

    Строкой и файлом можно пользоваться вместе — тогда файл идёт первым.
    Разными ключами они разведены нарочно: будь путь и текст одним ключом,
    опечатка в имени файла молча превратилась бы в указание переводчику.
    """
    if not path and not text:
        return {}, ""
    if path:
        if not os.path.exists(path):
            sys.exit(lang.T("prompt_nofile", path))
        text = open(path, encoding="utf-8").read() + ("\n\n" + text if text else "")
    meta = {}
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    odd = sorted(set(meta) - META_KEYS)
    if odd and log:
        log("  " + lang.T("meta_unknown", ", ".join(odd),
                          ", ".join(sorted(META_KEYS))))
    return meta, text.strip()


def _chunks(s):
    """Номера кусков: 5,6,7 и диапазоны 41-93.

    Диапазон нужен, чтобы обойти упрямый кусок: перевод последователен и
    после трёх отказов подряд встаёт, а перечислять полсотни номеров через
    запятую никто не станет. Названные явно — это осознанный пропуск, а не
    молчаливая дыра.
    """
    out = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out |= set(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def main():
    # Язык справки выбирается до разбора ключей: argparse печатает её сам,
    # и к этому мигу --ui уже должен быть известен.
    # Умолчания берутся из окружения: так booktrans_ru задаёт русские,
    # ничего не дублируя, а явный ключ всё равно сильнее.
    ui = os.environ.get("BT_UI", "en")
    for i, a in enumerate(sys.argv[1:]):
        if a == "--ui" and i + 2 <= len(sys.argv) - 1:
            ui = sys.argv[i + 2]
        elif a.startswith("--ui="):
            ui = a.split("=", 1)[1]
    T = lang.set_ui(ui)

    ap = argparse.ArgumentParser(
        description=T("h_desc"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=T("h_epilog"))
    ap.add_argument("book", nargs="?", help=T("h_book"))
    ap.add_argument("--check", action="store_true", help=T("h_check"))
    ap.add_argument("-p", "--prompt", help=T("h_prompt"))
    ap.add_argument("-pt", "--prompt-text", help=T("h_prompt_text"))
    ap.add_argument("-o", "--out", help=T("h_out"))
    ap.add_argument("-w", "--work", help=T("h_work"))
    ap.add_argument("--only", choices=ALL_STEPS, help=T("h_only"))
    ap.add_argument("--skip", default="", help=T("h_skip"))
    ap.add_argument("--chunks", help=T("h_chunks"))
    # Умолчания берутся из окружения — тем же путём, что и язык: так обёртки
    # вроде bt_claude сводятся к одной строке, а вшитое предпочтение одного
    # поставщика не навязывается тому, кто пользуется другим.
    ap.add_argument("--agent", default=os.environ.get("BT_AGENT", "claude"),
                    choices=("claude", "agy", "codex", "cmd"))
    ap.add_argument("--agent-cmd", help=T("h_agentcmd"))
    ap.add_argument("--model", default=os.environ.get("BT_MODEL"), help=T("h_model"))
    ap.add_argument("--effort", choices=("low", "medium", "high"), help="Effort level (low, medium, high)")
    ap.add_argument("--scout", help=T("h_scout"))
    ap.add_argument("--translator", help=T("h_translator"))
    ap.add_argument("--editor", help=T("h_editor"))
    ap.add_argument("--formatter", default=os.environ.get("BT_FORMATTER"),
                    help=T("h_formatter"))
    ap.add_argument("--ocrfixer", default=os.environ.get("BT_OCRFIXER"),
                    help=T("h_ocrfixer"))
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--wait", type=int, default=900, help=T("h_wait"))
    ap.add_argument("--max-wait", type=int, default=86400, help=T("h_maxwait"))
    ap.add_argument("--chunk-words", type=int, default=pipeline.TARGET_WORDS)
    ap.add_argument("--jobs", type=int, default=1, help=T("h_jobs"))
    ap.add_argument("--to", default=os.environ.get("BT_TO", "en"), help=T("h_to"))
    ap.add_argument("--ui", default=ui, help=T("h_ui"))
    ap.add_argument("--force-translate", action="store_true", help=T("h_force_lang"))
    ap.add_argument("--force-editing", action="store_true", help=T("h_force_edit"))
    ap.add_argument("--no-headings", action="store_true", help=T("h_nohead"))
    ap.add_argument("--partial", action="store_true", help=T("h_partial"))
    ap.add_argument("--encoding", help=T("h_encoding"))
    ap.add_argument("--code", choices=("comments", "asis"), default="comments",
                    help=T("h_code"))
    ap.add_argument("--fallback-agent", choices=("claude", "cmd", "agy"),
                    default=os.environ.get("BT_FALLBACK_AGENT"),
                    help=T("h_fb_agent"))
    ap.add_argument("--fallback-model", default=os.environ.get("BT_FALLBACK_MODEL"),
                    help=T("h_fb_model"))
    args = ap.parse_args()
    T = lang.set_ui(args.ui)
    log("")
    log(build.banner(args.ui))
    log("")

    if args.check:
        T = lang.set_ui(args.ui)
        log(T("doc_head"))
        return 1 if doctor.check(log, args.agent) else 0
    if not args.book:
        ap.error(T("h_book"))
    if not os.path.exists(args.book):
        sys.exit(T("h_nofile", args.book))

    # У agy усилие задаётся либо суффиксом в имени модели, либо ключом
    # --effort, и вместе они не работают. Отвергнуть это надо сейчас, а не
    # на середине книги невнятной ошибкой из чужой программы.
    for m in (args.model, args.translator, args.scout, args.editor,
              args.formatter, args.ocrfixer, args.fallback_model):
        if args.agent == "agy" and args.effort and m \
                and re.search(r"-(low|medium|high)$", m):
            sys.exit(T("effort_clash", m, args.effort))
    T = lang.set_ui(args.ui)
    if args.to not in lang.available_langs():
        sys.exit(f"нет правил для языка {args.to!r}; есть: "
                 f"{', '.join(lang.available_langs())}. Добавьте langs/{args.to}.md")
    pipeline.TARGET_WORDS = args.chunk_words
    pipeline.MAX_WORDS = int(args.chunk_words * 1.4)

    base = os.path.splitext(args.book)[0]
    work = args.work or base + ".work"
    os.makedirs(work, exist_ok=True)

    # Замок на рабочую папку. Два прогона по одной книге затирают друг другу
    # куски: у каждого своя нумерация, и в сборке потом не хватает блоков.
    # Проверено дорогой ценой — на книге, которую пришлось переводить заново.
    lock = os.path.join(work, "running.pid")
    if os.path.exists(lock):
        try:
            old_pid = int(open(lock).read().split()[0])
            os.kill(old_pid, 0)             # жив ли тот процесс
        except (ValueError, ProcessLookupError, PermissionError, IndexError):
            os.unlink(lock)                 # остался от упавшего — снимаем
        else:
            sys.exit(T("locked", work, old_pid))
    open(lock, "w").write(f"{os.getpid()} {' '.join(sys.argv[1:])}")
    atexit.register(lambda: os.path.exists(lock) and os.unlink(lock))
    steps = [args.only] if args.only else [s for s in STEPS if s not in args.skip.split(",")]
    only_chunks = _chunks(args.chunks) if args.chunks else None

    # Разметка — работа опознавательная, а не сочинительная: берём самую
    # дешёвую модель поставщика и низкое усилие. У agy усилие вшито в имя
    # модели, и отдельным ключом его туда передавать нельзя.
    CHEAP = {"claude": ("claude-sonnet-5", "low"),
             "agy": ("gemini-3.6-flash-low", None)}

    def agent_for(role=None):
        """Своя модель на проход: у разметки, разведки, перевода и редактуры
        разные требования, и платить за все одинаково незачем."""
        # Разметка и корректура — работа опознавательная, а не сочинительная,
        # и обеим хватает самой дешёвой модели поставщика.
        if role in ("formatter", "ocrfixer") \
                and not getattr(args, role) and args.agent in CHEAP:
            m, eff = CHEAP[args.agent]
            return make_agent(args.agent, m, args.agent_cmd, wait=args.wait,
                              max_wait=args.max_wait, log=log, effort=eff)
        m = (getattr(args, role, None) if role else None) or args.model
        return make_agent(args.agent, m, args.agent_cmd,
                          wait=args.wait, max_wait=args.max_wait, log=log, effort=args.effort)

    def fallback_agent():
        """Кем переводить кусок, от которого основная модель отказалась.

        Задан хоть один из двух ключей — подстраховка включена: недостающее
        берётся от основного прохода. Не задано ничего — отказной кусок
        просто пропускается, как и раньше.
        """
        if not (args.fallback_agent or args.fallback_model):
            return None
        return make_agent(args.fallback_agent or args.agent,
                          args.fallback_model or args.translator or args.model,
                          args.agent_cmd, wait=args.wait, max_wait=args.max_wait,
                          log=log, effort=args.effort)

    agent = agent_for()
    os.makedirs(f"{work}/prompts", exist_ok=True)

    def ask_model(prompt):
        """Спросить модель — этим разрешаются споры о кодировке файла.

        Запрос уходит, только если разбор по содержимому не смог выбрать
        уверенно, и стоит доли цента.
        """
        return agent.run("Ты отличаешь настоящий текст от каши, возникшей "
                         "при чтении файла в неверной кодировке. "
                         "Отвечай одним числом.", prompt)[0]

    def task(name):
        return open(f"{HERE}/prompts/{name}.md", encoding="utf-8").read()

    # ---- разбор книги (детерминированный, но разметку определяет модель)
    bp = f"{work}/book.json"
    if os.path.exists(bp):
        d = json.load(open(bp, encoding="utf-8"))
        meta, blocks = d["meta"], d["blocks"]
        cover = open(f"{work}/cover.bin", "rb").read() if os.path.exists(f"{work}/cover.bin") else None
        images = {}
        if os.path.isdir(f"{work}/images"):
            for n in sorted(os.listdir(f"{work}/images")):
                images[n] = open(f"{work}/images/{n}", "rb").read()
    else:
        styles_map = {}
        if "structure" in steps:
            # Язык проверяем до разметки: разметка стоит запроса к модели,
            # а книгу, уже написанную на языке перевода, переводить незачем.
            # Читаем без карты стилей — это бесплатно.
            try:
                _, pre_blocks, _, _ = extract.read_book(
                    args.book, encoding=args.encoding, ask=ask_model)
            except extract.BadBook as e:
                sys.exit(f"\n  {e}")
            if sum(1 for b in pre_blocks if b["kind"] == "p") >= 20:
                pre = lang.target_share(pre_blocks, args.to)
                if pre > 0.9 and not args.force_translate:
                    log("")
                    log("  " + T("stop_same_lang", pre))
                    log("  " + T("same_lang_hint"))
                    sys.exit(1)
            log(f"=== {T('step_structure')} ===")
            styles_map = pipeline.detect_structure(
                work, extract.scan_styles(args.book), agent,
                task("structure"), args.retries, log)
            log("")
        elif os.path.exists(f"{work}/structure.json"):
            styles_map = {k: v for k, v in
                          json.load(open(f"{work}/structure.json", encoding="utf-8")).items()
                          if not k.startswith("_")}
        # pdf и txt приходят без разметки вовсе: абзацы рвутся посреди фразы,
        # колонтитулы неотличимы от заголовков. Правилами это не выводится —
        # спрашиваем модель, один раз на книгу.
        marks = None
        if os.path.splitext(args.book)[1].lower() in (".pdf", ".txt", ".md"):
            # Разметка идёт и без этапа structure — книгу всё равно надо
            # прочесть, — так что заголовок печатает она сама.
            if "structure" not in steps:
                log(f"=== {T('step_structure')} ===")
                log("")
            marks = pipeline.format_marks(
                work, args.book, agent_for("formatter"), task("format"),
                args.encoding, ask_model, log)
        pipeline.note_source(work, reader={
            ".pdf": "pdftotext + pdfimages (poppler)"}.get(
                os.path.splitext(args.book)[1].lower(), ""))
        log("  " + T("reading", args.book))
        try:
            meta, blocks, cover, images = extract.read_book(
                args.book, styles_map, args.encoding, ask_model, marks)
        except extract.BadBook as e:
            sys.exit(f"\n  {e}")
        json.dump({"meta": meta, "blocks": blocks}, open(bp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        if cover:
            open(f"{work}/cover.bin", "wb").write(cover)
        if images:
            os.makedirs(f"{work}/images", exist_ok=True)
            for n, raw in images.items():
                open(f"{work}/images/{n}", "wb").write(raw)
            log("  " + T("images", len(images)))
        cl = meta.pop("_cleaned", None) or {}
        if cl.get("watermarks") or cl.get("junk_pages"):
            log("  " + T("cleaned", cl.get("watermarks", 0), cl.get("junk_pages", 0)))

    # Указания заказчика запоминаются в рабочей папке: иначе пересборка
    # без -p молча потеряет название, автора и серию.
    saved = f"{work}/prompt_meta.json"
    user_meta, user_text = read_prompt(args.prompt, log, args.prompt_text)
    if args.prompt or args.prompt_text:
        json.dump({"meta": user_meta, "text": user_text},
                  open(saved, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elif os.path.exists(saved):
        d = json.load(open(saved, encoding="utf-8"))
        user_meta, user_text = d.get("meta") or {}, d.get("text") or ""
        log("  " + T("prompt_from", saved))
    # Порядок старшинства метаданных, от слабого к сильному:
    #   1) что нашла разведка в самом тексте — запасной вариант, только для
    #      полей, которых нет в файле (у txt и pdf их нет вовсе);
    #   2) метаданные формата — epub и fb2 хранят их сами;
    #   3) указания заказчика — сильнее всего, они правят и первое, и второе.
    for k, v in pipeline.scout_meta(work).items():
        meta.setdefault(k, v)
    meta.update(user_meta)
    meta.pop("_cleaned", None)
    # Язык перевода — свойство запуска, а не книги: по нему сборщик берёт
    # строки для читателя и проставляет <lang> в готовом файле.
    meta["target_lang"] = args.to

    # Заглавие на целевом языке — то, что читатель увидит первым, и то, чем
    # назовётся файл. Если его нет ни в указаниях, ни в разведке, книга
    # выйдет с заглавием оригинала. Сказать об этом надо сейчас, пока правка
    # стоит одной строки в файле указаний, а не после часа перевода.
    # О заглавии предупреждаем один раз и после разведки — там, где она
    # уже отработала (см. ниже). Здесь только собираем метаданные.
    chunks = pipeline.make_chunks(blocks)
    paras = sum(1 for b in blocks if b["kind"] == "p")
    heads = sum(1 for b in blocks if b["kind"] == "title")
    total_words = sum(c["words"] for c in chunks)
    log("  " + T("counts", paras, total_words, len(chunks), heads))
    share = lang.target_share(blocks, args.to)
    if share > 0.9 and not args.force_translate and paras >= 20:
        log("")
        log("  " + T("stop_same_lang", share))
        log("  " + T("same_lang_hint"))
        sys.exit(1)

    if paras < 20 and total_words < 500:
        # Комиксы, альбомы и сборники иллюстраций: текста нет, переводить
        # нечего. Молча выдать пустую книгу было бы хуже, чем сказать прямо.
        log("")
        log("  " + T("stop_no_text", paras, total_words))
        log("  " + T("no_text_hint"))
        sys.exit(1)

    if not heads:
        # У epub и fb2 разделы размечены всегда — ноль заголовков там значит,
        # что распознаватель не понял вёрстку. У txt и pdf разметки нет вовсе,
        # и книга без глав — обычное дело: пугать незачем.
        if os.path.splitext(args.book)[1].lower() in (".epub", ".fb2") \
                and not args.no_headings:
            # Дальше идти нельзя: перевод стоит часы и деньги, а нарезка
            # вслепую разорвёт книгу посреди сцены или между рассказчиками.
            log("")
            log("  " + T("stop_no_head"))
            log("  " + T("no_head_hint"))
            log("")
            log("  " + T("todo"))
            sp = f"{work}/structure.json"
            if os.path.exists(sp):
                log("  " + T("no_head_fix_file", sp))
                log("  " + T("no_head_fix_redo", sp))
            else:
                log("  " + T("no_head_fix_run"))
            log("  " + T("no_head_fix_force"))
            sys.exit(1)
        else:
            log("  " + T("no_head_plain"))
    log("  " + T("workdir", work))
    log("")

    made_by_ocr = pipeline.ocr_check(work, args.book, agent_for("formatter"), log)
    if made_by_ocr:
        log("  " + T("ocr_made", made_by_ocr))

    def sysprompt(extra=""):
        parts = [f"Язык перевода: **{lang.lang_name(args.to)}**. Переводить на него.",
                 open(f"{HERE}/prompts/style.md", encoding="utf-8").read()]
        # Порча от распознавания — свойство исходника, а не прохода: её видят
        # и разведка, и перевод, и редактура, поэтому место ей в общем промпте.
        if made_by_ocr:
            parts.append(open(f"{HERE}/prompts/ocr.md", encoding="utf-8").read())
        rules = lang.rules(args.to)
        if rules:
            parts.append("# Правила целевого языка\n\n" + rules)
        sp = f"{work}/scout.md"
        if os.path.exists(sp):
            parts.append("# Справочник по этой книге\n\n"
                         "Собран разведочным проходом. Решения по именам, родам и "
                         "интонациям приняты здесь и обсуждению не подлежат.\n\n"
                         + open(sp, encoding="utf-8").read())
        if user_text:
            parts.append("# Указания заказчика\n\n"
                         "**Имеют приоритет над всем вышесказанным.**\n\n" + user_text)
        if extra:
            parts.append(extra)
        return "\n\n---\n\n".join(parts)

    n = 0
    # Порчу распознавания чиним в оригинале и до всего остального: иначе
    # разведка соберёт справочник по испорченному, а переводчик будет
    # разбирать порчу молча и всякий раз по-своему.
    if made_by_ocr:
        if "ocrfix" in steps:
            n += 1
            log(f"=== {n}. {T('step_ocrfix')} ===")
            pipeline.fix_ocr(work, blocks, agent_for("ocrfixer"), sysprompt(),
                               task("ocrfix"), args.retries, log)
            log("")
        pipeline.apply_fixes(work, blocks, log)

    if "scout" in steps:
        n += 1
        log(f"=== {n}. {T('step_scout')} ===")
        pipeline.scout(work, blocks, agent_for("scout"), sysprompt(), task("scout"),
                       args.retries, log, args.to,
                       src_name=os.path.basename(args.book))
        # Выходные данные разведка находит только сейчас, а метаданные были
        # собраны до неё. Без этого перечитывания книга первого прогона
        # выходила с заглавием оригинала, и оно появлялось лишь при повторной
        # сборке — а человек к тому времени уже считал книгу готовой.
        for k, v in pipeline.scout_meta(work).items():
            meta.setdefault(k, v)
        meta.update(user_meta)
        if not meta.get("title_target"):
            log("  " + T("no_title", args.to))
        log("")

    if "translate" in steps:
        n += 1
        log(f"=== {n}. {T('step_translate')} ===")
        pipeline.headings(work, blocks, agent_for("translator"), sysprompt(), args.retries, log)
        d, s = pipeline.translate(work, chunks, agent_for("translator"), sysprompt(), task("translate"),
                                  args.retries, log, only_chunks,
                                  fallback=fallback_agent())
        # Листинги в перевод не идут, но комментарии в них — проза, и
        # читателю нужны они, а не английский подстрочник в коде.
        if args.code == "comments" and any(b["kind"] == "code" for b in blocks):
            pipeline.code_comments(work, blocks, agent_for("translator"),
                                   sysprompt(), task("code"), args.retries, log)
        log("  " + (T("done_translate", d, s) if d else T("nothing_translate", s)))
        log("")

    if "edit" in steps:
        n += 1
        log(f"=== {n}. {T('step_edit')} ===")
        d, s, t = pipeline.edit(work, chunks, agent_for("editor"), sysprompt(), task("edit"),
                                args.retries, log, only_chunks, args.jobs,
                                fallback=fallback_agent(),
                                force=args.force_editing)
        log("  " + (T("done_edit", d, s, t) if d else T("nothing_edit", s)))
        log("")

    if "notes" in steps:
        n += 1
        log(f"=== {n}. {T('step_notes')} ===")
        d, s, t = pipeline.notes(work, chunks, agent_for("editor"), sysprompt(), task("notes"),
                                 args.retries, log, only_chunks, args.jobs)
        log("  " + (T("done_notes", d, s, t) if d else T("notes_already", s)))
        log("")

    if "build" in steps:
        n += 1
        log(f"=== {n}. {T('step_build')} ===")
        dest = args.out or f"{build.out_name(meta, base)}.fb2"
        # Затереть исходник переводом — потеря невосполнимая, а совпасть
        # имена могут легко: fb2 на входе, fb2 на выходе.
        if os.path.abspath(dest) == os.path.abspath(args.book):
            sys.exit(T("would_overwrite", dest))
        build.build_fb2(work, meta, blocks, cover, dest, log, args.partial, images)
        log("")

    if "qa" in steps:
        n += 1
        log(f"=== {n}. {T('step_qa')} ===")
        build.qa(work, blocks, log, T, meta.get("lang"), args.to)
        build.unfinished_edits(work, log, T)
        build.review_report(work, log)
        build.sources_report(work, log)
        build.usage_report(work, log, T)


def run():
    """Точка входа: и для установленной команды, и для запуска из папки."""
    try:
        main()
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
    run()
