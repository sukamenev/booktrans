"""Один прогон конвейера: от файла книги до готового fb2 и отчётов.

Сначала приём книги — распознавание, разметка, чтение, метаданные, проверки,
стоит ли вообще переводить, — потом проходы по порядку `PASSES`. Что уже
сделано, лежит в рабочей папке и заново не делается.
"""
import contextlib
import json
import os
import re
import sys

from . import build, extract, lang, pipeline

# Проходы после приёма книги, по порядку. Распознавание и разметка идут при
# приёме: без них книгу не прочесть.
PASSES = ("scout", "translate", "edit", "verify", "notes", "build", "qa")

# Что понимает шапка файла указаний. Ключ не из списка молча пропал бы:
# написал человек title_ru вместо title_target — и книга вышла бы с
# заглавием оригинала, а почему, разбирайся сам.
META_KEYS = {"title", "author", "title_target", "author_target", "series",
             "series_target", "series_no", "year", "publisher", "isbn", "lang",
             "uid", "genre"}


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


@contextlib.contextmanager
def locked(work, argv=()):
    """Замок на рабочую папку.

    Два прогона по одной книге затирают друг другу куски: у каждого своя
    нумерация, и в сборке потом не хватает блоков. Проверено дорогой ценой —
    на книге, которую пришлось переводить заново.
    """
    lock = os.path.join(work, "running.pid")
    if os.path.exists(lock):
        try:
            old_pid = int(open(lock).read().split()[0])
            os.kill(old_pid, 0)             # жив ли тот процесс
        except (ValueError, ProcessLookupError, PermissionError, IndexError):
            os.unlink(lock)                 # остался от упавшего — снимаем
        else:
            sys.exit(lang.T("locked", work, old_pid))
    open(lock, "w").write(f"{os.getpid()} {' '.join(argv)}")
    try:
        yield
    finally:
        if os.path.exists(lock):
            os.unlink(lock)


def head(key, n=None):
    """Заголовок этапа: `=== 1. Правка дефектов распознавания ===`.

    Прописная буква ставится здесь, а не в переводах: за номером с точкой
    строчная читается обрывком предыдущей строки. Языкам без строчных и
    прописных это ничего не стоит.
    """
    s = lang.T(key)
    s = s[:1].upper() + s[1:]
    return f"=== {s} ===" if n is None else f"=== {n}. {s} ==="


def chunk_numbers(s):
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


class Run:
    """Прогон над одной книгой: её состояние и проходы над ним.

    Поля заполняются по ходу приёма (`go`): сначала есть только ключи и
    рабочая папка, после `book` — блоки и метаданные, после `measure` —
    куски. Проходам остаётся брать готовое.
    """

    def __init__(self, args, work, log, models, steps):
        self.args = args
        self.work = work
        self.log = log
        self.models = models
        self.steps = steps
        self.T = lang.T
        self.ext = os.path.splitext(args.book)[1].lower()
        self.only_chunks = chunk_numbers(args.chunks) if args.chunks else None
        self.agent = models.first()
        self.ocr_agent = models.first("ocrmodel")
        self.meta = self.blocks = self.chunks = None
        self.cover = None
        self.images = {}
        self.user_meta, self.user_text = {}, ""
        self.made_by_ocr = ""
        self.n = 0                  # номер этапа в заголовке
        self._said = set()          # о каких своих промптах уже сказано

    def go(self):
        if not self.ocr():
            return
        self.book()
        self.user_prompt()
        self.merge_meta()
        self.measure()
        self.ocr_fixes()
        for name in PASSES:
            if name in self.steps and not getattr(self, "step_" + name)():
                return

    # ------------------------------------------------------------ приём

    def _head(self, key):
        self.n += 1
        self.log("")
        self.log(head(key, self.n))

    def ocr(self):
        """Распознавание pdf. Ложь — прогон на этом кончается."""
        a = self.args
        if "ocr" not in self.steps:
            return True
        if self.ext != ".pdf":
            if a.only == "ocr":
                self.log("OCR stage is only applicable for PDFs.")
                return False
            return True
        self.log("")
        self.log(head("step_ocr"))
        self.log("")
        extract.ocr(a.book, self.models.chain("ocrmodel"), a.pages, jobs=a.jobs,
                    log=self.log, T=self.T, prompt=self.task("ocr"))
        return a.only != "ocr"

    def book(self):
        """Книга: из book.json, если уже разобрана, иначе читаем и разбираем.

        Разбор детерминированный, но разметку определяет модель.
        """
        bp = f"{self.work}/book.json"
        if os.path.exists(bp):
            self._load_book(bp)
        else:
            self._read_book(bp)

    def _load_book(self, bp):
        w = self.work
        d = json.load(open(bp, encoding="utf-8"))
        self.meta, self.blocks = d["meta"], d["blocks"]
        if os.path.exists(f"{w}/cover.bin"):
            self.cover = open(f"{w}/cover.bin", "rb").read()
        for sub in ("images", "pdf_pages/images"):
            if os.path.isdir(f"{w}/{sub}"):
                for n in sorted(os.listdir(f"{w}/{sub}")):
                    self.images[n] = open(f"{w}/{sub}/{n}", "rb").read()
                break

    def _read_book(self, bp):
        a, w, T, log = self.args, self.work, self.T, self.log
        styles_map = self._styles()
        marks = self._marks()
        pipeline.note_source(w, reader=self._pdf_reader())
        log("  " + T("reading", a.book))
        try:
            self.meta, self.blocks, self.cover, self.images = extract.read_book(
                a.book, styles_map, a.encoding, self.ask_model, marks,
                agent=self.ocr_agent)
        except extract.BadBook as e:
            sys.exit(f"\n  {e}")
        json.dump({"meta": self.meta, "blocks": self.blocks},
                  open(bp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if self.cover:
            open(f"{w}/cover.bin", "wb").write(self.cover)
        if self.images:
            os.makedirs(f"{w}/images", exist_ok=True)
            for n, raw in self.images.items():
                open(f"{w}/images/{n}", "wb").write(raw)
            log("  " + T("images", len(self.images)))
        cl = self.meta.pop("_cleaned", None) or {}
        if cl.get("watermarks") or cl.get("junk_pages"):
            log("  " + T("cleaned", cl.get("watermarks", 0), cl.get("junk_pages", 0)))

    def _styles(self):
        """Карта стилей: какие из них заголовки. Этап structure."""
        a, w, T, log = self.args, self.work, self.T, self.log
        if "structure" not in self.steps:
            sp = f"{w}/structure.json"
            if not os.path.exists(sp):
                return {}
            return {k: v for k, v in json.load(open(sp, encoding="utf-8")).items()
                    if not k.startswith("_")}
        # Язык проверяем до разметки: разметка стоит запроса к модели,
        # а книгу, уже написанную на языке перевода, переводить незачем.
        # Читаем без карты стилей — это бесплатно.
        try:
            _, pre_blocks, _, _ = extract.read_book(
                a.book, encoding=a.encoding, ask=self.ask_model, agent=self.ocr_agent)
        except extract.BadBook as e:
            sys.exit(f"\n  {e}")
        if sum(1 for b in pre_blocks if b["kind"] == "p") >= 20:
            pre = lang.target_share(pre_blocks, a.to)
            if pre > 0.9 and not a.force_translate:
                log("")
                log("  " + T("stop_same_lang", pre))
                log("  " + T("same_lang_hint"))
                sys.exit(1)
        log("")
        log(head("step_structure"))
        # Перепись стилей — работа опознавательная, та же, что разметка
        # pdf: посмотреть на образцы и сказать, что здесь заголовок.
        # Потому и модель та же, из `--formatter`.
        out = pipeline.detect_structure(
            w, extract.scan_styles(a.book), self.models.first("formatter"),
            self.task("structure"), a.retries, log,
            fallback=self.models.rest("formatter"))
        log("")
        return out

    def _marks(self):
        """Разметка pdf и txt: абзацы, заголовки, колонтитулы.

        Они приходят без разметки вовсе: абзацы рвутся посреди фразы,
        колонтитулы неотличимы от заголовков. Правилами это не выводится —
        спрашиваем модель, один раз на книгу.

        У markdown разметка своя и названа прямо в файле: заголовок с
        решёткой, забор листинга, столбики таблицы. Спрашивать о ней модель
        незачем — на статье в четырнадцать тысяч слов это стоило четыре
        минуты и шестьдесят центов.
        """
        a = self.args
        if self.ext not in (".pdf", ".txt", ".md") or extract.is_markdown(a.book):
            return None
        # Разметка идёт и без этапа structure — книгу всё равно надо
        # прочесть, — так что заголовок печатает она сама.
        if "structure" not in self.steps:
            self.log("")
            self.log(head("step_structure"))
            self.log("")
        return pipeline.format_marks(
            self.work, a.book, self.models.first("formatter"), self.task("format"),
            a.encoding, self.ask_model, self.log, self.models.rest("formatter"))

    def _pdf_reader(self):
        """Чем прочитан pdf — для отчёта об источниках."""
        if self.ext != ".pdf":
            return ""
        oa = self.ocr_agent
        # Страницы распознавания лежат рядом с книгой, а не в `--work`:
        # так их кладёт и ищет само распознавание.
        page1 = os.path.join(os.path.splitext(self.args.book)[0] + ".work",
                             "pdf_pages", "page_0001.md")
        if getattr(oa, "kind", "") in ("claude", "codex", "agy") or os.path.exists(page1):
            return self.T("doc_pdf_visual",
                          getattr(oa, "model", getattr(oa, "kind", "unknown")))
        return self.T("doc_pdftotext")

    def user_prompt(self):
        """Указания заказчика — запоминаются в рабочей папке: иначе пересборка
        без -p молча потеряет название, автора и серию."""
        a = self.args
        saved = pipeline.lpath(self.work, "prompt_meta.json", a.to)
        self.user_meta, self.user_text = read_prompt(a.prompt, self.log, a.prompt_text)
        if a.prompt or a.prompt_text:
            json.dump({"meta": self.user_meta, "text": self.user_text},
                      open(pipeline.mkparent(saved), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        elif os.path.exists(saved):
            d = json.load(open(saved, encoding="utf-8"))
            self.user_meta, self.user_text = d.get("meta") or {}, d.get("text") or ""
            self.log("  " + self.T("prompt_from", saved))

    def merge_meta(self):
        """Метаданные книги, от слабого источника к сильному:
          1) что нашла разведка в самом тексте — запасной вариант, только для
             полей, которых нет в файле (у txt и pdf их нет вовсе);
          2) метаданные формата — epub и fb2 хранят их сами;
          3) указания заказчика — сильнее всего, они правят и первое, и второе.
        """
        a, meta = self.args, self.meta
        # Нумерация блоков позиционная, и перечитанная страница сдвигает её у
        # всей книги: перевод лежит рядом, но под прежними номерами.
        # Перепривязка идёт по отпечаткам и обязана случиться раньше перевода
        # и сборки.
        pipeline.reanchor(self.work, self.blocks, a.to, self.log)
        for k, v in pipeline.scout_meta(self.work, a.to).items():
            meta.setdefault(k, v)
        # Разделы-указатели, найденные разведкой, — drop: true. _mark_back
        # идемпотентна: asis-блоки остаются asis, добавляется только drop.
        if meta.get("drop_sections"):
            extract._mark_back(self.blocks, drop_sections=meta["drop_sections"])
        meta.update(self.user_meta)
        meta.pop("_cleaned", None)
        # Язык перевода — свойство запуска, а не книги: по нему сборщик берёт
        # строки для читателя и проставляет <lang> в готовом файле.
        meta["target_lang"] = a.to

    def measure(self):
        """Нарезка на куски и счёт: есть ли что переводить.

        Дальше идти нельзя, если книга уже на языке перевода, если текста
        нет (комиксы, альбомы: молча выдать пустую книгу хуже, чем сказать
        прямо) или если у epub/fb2 не нашлось ни одного заголовка — перевод
        стоит часы и деньги, а нарезка вслепую разорвёт книгу посреди сцены.
        """
        a, T, log, blocks = self.args, self.T, self.log, self.blocks
        self.chunks = pipeline.make_chunks(blocks, a.chunk_words)
        paras = sum(1 for b in blocks if b["kind"] == "p")
        heads = sum(1 for b in blocks if b["kind"] == "title")
        total_words = sum(c["words"] for c in self.chunks)
        log("  " + T("counts", paras, total_words, len(self.chunks), heads))
        if self.meta.get("links"):
            log("  " + T("links_found", self.meta["links"]))
        share = lang.target_share(blocks, a.to)
        if share > 0.9 and not a.force_translate and paras >= 20:
            log("")
            log("  " + T("stop_same_lang", share))
            log("  " + T("same_lang_hint"))
            sys.exit(1)
        if paras < 20 and total_words < 500:
            log("")
            log("  " + T("stop_no_text", paras, total_words))
            log("  " + T("no_text_hint"))
            sys.exit(1)
        if not heads:
            # У epub и fb2 разделы размечены всегда — ноль заголовков там
            # значит, что распознаватель не понял вёрстку. У txt и pdf
            # разметки нет вовсе, и книга без глав — обычное дело.
            if self.ext in (".epub", ".fb2") and not a.no_headings:
                log("")
                log("  " + T("stop_no_head"))
                log("  " + T("no_head_hint"))
                log("")
                log("  " + T("todo"))
                sp = f"{self.work}/structure.json"
                if os.path.exists(sp):
                    log("  " + T("no_head_fix_file", sp))
                    log("  " + T("no_head_fix_redo", sp))
                else:
                    log("  " + T("no_head_fix_run"))
                log("  " + T("no_head_fix_force"))
                sys.exit(1)
            log("  " + T("no_head_plain"))
        log("  " + T("workdir", self.work))
        log("")

    def ocr_fixes(self):
        """Порча распознавания: найти и починить в оригинале до всего
        остального — иначе разведка соберёт справочник по испорченному, а
        переводчик будет разбирать порчу молча и всякий раз по-своему."""
        a = self.args
        self.made_by_ocr = pipeline.ocr_check(self.work, a.book,
                                              self.models.first("formatter"), self.log)
        if not self.made_by_ocr:
            return
        self.log("  " + self.T("ocr_made", self.made_by_ocr))
        if "ocrfix" in self.steps:
            self._head("step_ocrfix")
            pipeline.fix_ocr(self.work, self.blocks, self.models.first("ocrfixer"),
                             self.sysprompt(), self.task("ocrfix"), a.retries,
                             self.log, fallback=self.models.rest("ocrfixer"))
            self.log("")
        pipeline.apply_fixes(self.work, self.blocks, self.log)

    # ---------------------------------------------------------- промпты

    def ask_model(self, prompt):
        """Спросить модель — этим разрешаются споры о кодировке файла.

        Запрос уходит, только если разбор по содержимому не смог выбрать
        уверенно, и стоит доли цента.
        """
        return self.agent.run("Ты отличаешь настоящий текст от каши, возникшей "
                              "при чтении файла в неверной кодировке. "
                              "Отвечай одним числом.", prompt)[0]

    def task(self, name, **kwargs):
        """Задание проходу. О своём, перекрытом промпте — один раз за прогон."""
        text, own = lang.prompt(name, self.args.to)
        if kwargs:
            text = text.format(**kwargs)
        if own and name not in self._said:
            self._said.add(name)
            self.log("  " + self.T("prompt_own", own.replace(os.path.expanduser("~"), "~")))
            lost = lang.lost_tokens(name, text)
            if lost:
                self.log("  " + self.T("prompt_lost", ", ".join(lost)))
        return text

    def sysprompt(self, extra="", lean=False):
        """Системный промпт: язык, стиль, правила, справочник, указания.

        Порядок частей закреплён: всё здесь неизменно на всю книгу, и
        поставщики кешируют запрос по дословно совпадающему началу.
        """
        to = self.args.to
        parts = [lang.prompt("sys_language")[0].format(lang=lang.lang_name(to)),
                 self.task("style"), self.task("units")]
        # Порча от распознавания — свойство исходника, а не прохода: её видят
        # и разведка, и перевод, и редактура, поэтому место ей в общем промпте.
        if self.made_by_ocr:
            parts.append(self.task("ocr_error_fix"))
        rules = lang.rules(to)
        if rules:
            parts.append(lang.prompt("sys_rules")[0] + "\n\n" + rules)
        sp = pipeline.lpath(self.work, "scout.md", to)
        if os.path.exists(sp):
            ref = open(sp, encoding="utf-8").read()
            if lean:
                # Покусковым проходам таблицы имён и терминов не кладутся в
                # систему целиком: их строки приезжают с каждым куском своей
                # выжимкой (см. split_ref). Система остаётся неизменной на
                # всю книгу — транспорт кеширует её по совпадающему началу —
                # и не тащит сотни строк, из которых куску нужны единицы.
                ref = pipeline.split_ref(ref)[0]
            parts.append(lang.prompt("sys_ref")[0] + "\n\n" + ref)
        if self.user_text:
            parts.append(lang.prompt("sys_user")[0] + "\n\n" + self.user_text)
        if extra:
            parts.append(extra)
        return "\n\n---\n\n".join(parts)

    # ----------------------------------------------------------- проходы
    # Каждый возвращает истину, если прогон можно продолжать.

    def step_scout(self):
        a, T, log = self.args, self.T, self.log
        self._head("step_scout")
        hints = {"filename": os.path.basename(a.book), "meta": self.meta}
        ref = pipeline.scout(self.work, self.blocks, self.models.first("scout"),
                             self.sysprompt(), self.task("scout"), a.retries, log,
                             a.to, hints=hints, fallback=self.models.rest("scout"),
                             likes=a.like, jobs=a.scout_jobs)
        # Внедрённое обращение к машине — повод остановиться до перевода, а не
        # обнаружить его в готовой книге. Разведка отличает такое указание от
        # книги, которая об инъекциях рассказывает: вторую переводим молча.
        bad = pipeline.injected(ref)
        if bad:
            log("")
            log(T("inject_found", len(bad)))
            for x in bad[:10]:
                log(f"      {x[:150]}")
            log("")
            log(T("inject_hint"))
            if not a.force_injected:
                raise SystemExit(T("inject_stop"))
        # Имена соседних книг цикла (`--like`) сводятся после разведки кодом,
        # а не просьбой в промпте: замена в файле стопроцентна, бюджет не
        # нужен, и работает это даже когда разведка уже была сделана.
        pipeline.cycle_merge(self.work, a.like or [], a.to, self.blocks, log)
        # Выходные данные разведка находит только сейчас, а метаданные были
        # собраны до неё. Без этого перечитывания книга первого прогона
        # выходила с заглавием оригинала, и оно появлялось лишь при повторной
        # сборке — а человек к тому времени уже считал книгу готовой.
        for k, v in pipeline.scout_meta(self.work, a.to).items():
            self.meta.setdefault(k, v)
        self.meta.update(self.user_meta)
        if not self.meta.get("title_target"):
            log("  " + T("no_title", a.to))
        log("")
        return True

    def step_translate(self):
        a, T, log = self.args, self.T, self.log
        self._head("step_translate")
        tr, spare = self.models.first("translator"), self.models.rest("translator")
        pipeline.headings(self.work, self.blocks, tr, self.sysprompt(lean=True),
                          a.retries, log, fallback=spare, to=a.to)
        d, s, halted = pipeline.translate(
            self.work, self.chunks, tr, self.sysprompt(lean=True),
            self.task("translate", model=tr.model or tr.kind.capitalize()),
            a.retries, log, self.only_chunks, fallback=spare, to=a.to)
        # Листинги в перевод не идут, но комментарии в них — проза, и
        # читателю нужны они, а не английский подстрочник в коде.
        if a.code == "comments" and any(b["kind"] == "code" for b in self.blocks):
            pipeline.code_comments(self.work, self.blocks, tr, self.sysprompt(),
                                   self.task("code"), a.retries, log,
                                   fallback=spare, to=a.to)
        log("  " + (T("done_translate", d, s) if d else T("nothing_translate", s)))
        # Дальше идти незачем: следующие проходы зовут ту же цепочку, которая
        # только что перестала отвечать, а сборка книги с дырой всё равно
        # откажется. Прежде прогон послушно шёл до конца и печатал ещё десяток
        # сбоев, за которыми терялась причина.
        return not self._halted(halted)

    def step_edit(self):
        a, T, log = self.args, self.T, self.log
        self._head("step_edit")
        d, s, t, halted = pipeline.edit(
            self.work, self.chunks, self.models.first("editor"),
            self.sysprompt(lean=True), self.task("edit"), a.retries, log,
            self.only_chunks, a.jobs, fallback=self.models.rest("editor"),
            force=a.force_editing, to=a.to, self_edit=a.self_edit)
        if d or not halted:
            log("  " + (T("done_edit", d, s, t) if d else T("nothing_edit", s)))
        return not self._halted(halted)

    def _halted(self, halted):
        if halted:
            self.log("")
            self.log("  " + self.T("run_halted"))
        return halted

    def step_verify(self):
        a, T, log = self.args, self.T, self.log
        self._head("step_verify")
        d, s, fn, fx = pipeline.verify(
            self.work, self.chunks, self.models.first("verifier"),
            self.sysprompt(lean=True), self.task("verify"), a.retries, log,
            self.only_chunks, fallback=self.models.rest("verifier"), to=a.to,
            jobs=a.jobs, full=a.full_verify)
        log("  " + (T("done_verify", d, s, fn, fx) if d or s
                    else T("nothing_verify")))
        return True

    def step_notes(self):
        a, T, log = self.args, self.T, self.log
        self._head("step_notes")
        d, s, t = pipeline.notes(self.work, self.chunks, self.models.first("editor"),
                                 self.sysprompt(lean=True), self.task("notes"),
                                 a.retries, log, self.only_chunks, a.jobs,
                                 fallback=self.models.rest("editor"), to=a.to)
        log("  " + (T("done_notes", d, s, t) if d else T("notes_already", s)))
        log("")
        return True

    def step_build(self):
        """Без `-o` собираем оба ходовых формата. Язык книги не говорит, чем
        её будут читать: fb2 понимают русскоязычные читалки, epub — все
        прочие и все телефоны, а стоит вторая сборка секунд и ни одного
        запроса к модели. Выбирать за читателя тут не из чего.

        Fb2 — в архиве: так его и раздают библиотеки, так понимают почти все
        читалки, и весит он вдвое меньше. Голый нужен — назовите его сами.
        """
        a = self.args
        self._head("step_build")
        if a.out:
            dests = [a.out]
        else:
            base = os.path.splitext(a.book)[0]
            dests = [f"{build.out_name(self.meta, base, a.name_series)}.{e}"
                     for e in ("fb2.zip", "epub")]
        for dest in dests:
            # Затереть исходник переводом — потеря невосполнимая, а совпасть
            # имена могут легко: fb2 на входе, fb2 на выходе.
            if os.path.abspath(dest) == os.path.abspath(a.book):
                sys.exit(self.T("would_overwrite", dest))
            # Свой набор картинок каждому: сборка дописывает в него
            # отрисованные формулы, и второму формату досталось бы чужое.
            build.build_book(self.work, self.meta, self.blocks, self.cover, dest,
                             self.log, a.partial, dict(self.images))
        self.log("")
        return True

    def step_qa(self):
        a, w, log = self.args, self.work, self.log
        self._head("step_qa")
        build.qa(w, self.blocks, log, self.T, self.meta.get("lang"), a.to,
                 bool(self.made_by_ocr))
        build.unfinished_edits(w, log, self.T, a.to)
        build.review_report(w, log, to=a.to)
        build.sources_report(w, log, to=a.to)
        build.usage_report(w, log, self.T, a.to)
        return True

