#!/usr/bin/env python3
"""Проверка пересжатия справочника.

Справочник разведки уходит в КАЖДЫЙ запрос на перевод, поэтому его держат в
пределе. Просьба «уложиться в столько-то знаков» стоит в промпте сведения, но
исполняется плохо, и есть отдельный проход, который дожимает.

На живой книге он не справился: справочник в 53 712 знаков стал 50 146 при
пределе 24 000. Причина была не в модели, а в арифметике — раздел, где две
трети занимают биографии, а треть таблица имён, разрешалось ужать не больше
чем на треть от всего раздела разом. Даже послушайся модель дословно, вышло бы
33 437.

    python3 tests/scout_check.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402


def table(n):
    return [f"| Имя {i} | Перевод {i} | пояснение про это имя, строка длинная |"
            for i in range(n)]


def prose(n):
    return [f"**Перемены {i}.** Здесь пересказ событий, биография и голос "
            f"персонажа — то, что режется первым, потому что сюжет конвейер "
            f"помнит отдельным конспектом. Строка {i}." for i in range(n)]


# Раздел, каких в жизни большинство: треть — таблица, две трети — проза.
MIXED = "## ПЕРСОНАЖИ\n\n" + "\n".join(table(90) + prose(140)) + "\n"
PLAIN = "## ОБРАЩЕНИЯ\n\n" + "\n".join(prose(60)) + "\n"
BOOK = MIXED + "\n" + PLAIN


def _part(user):
    """Справочник из запроса: он лежит между заданием и конвертом."""
    return user.split("---\n\n", 1)[1].split("\n\n---\n\n")[0]


def _box(text, want):
    """Ответ в конверте, как его теперь требует промпт."""
    return f"[[[SHRINK {want}]]]\n{text}\n[[[/SHRINK {want}]]]"


class Obedient:
    """Модель, которая делает ровно то, о чём просят: режет прозу до
    названного размера и не трогает ни одной строки таблицы."""

    model = "послушная"
    kind = "стенд"

    def run(self, system, user):
        want = int(re.search(r"надо около (\d+)", user).group(1))
        part = _part(user)
        rows = [l for l in part.splitlines() if l.startswith("|")]
        rest = [l for l in part.splitlines() if not l.startswith("|")]
        out = list(rest)
        # Выбрасываем прозу с конца, пока не уложились.
        while len("\n".join(out + rows)) > want and len(out) > 2:
            out.pop()
        return _box("\n".join(out[:2] + rows + out[2:]) + "\n", want), \
            {"model": self.model, "cost_usd": 0}


class Greedy(Obedient):
    """Модель, которая вместо прозы вычёркивает таблицу — самый простой
    способ уложиться в предел и самый разрушительный."""

    model = "жадная"

    def run(self, system, user):
        want = int(re.search(r"надо около (\d+)", user).group(1))
        part = _part(user)
        head = part.splitlines()[:2]
        return _box("\n".join(head + [l for l in part.splitlines()
                                      if not l.startswith("|")][:5]) + "\n",
                    want), {"model": self.model, "cost_usd": 0}


def hush(m="", end="\n"):
    pass


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:50} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    tbl = sum(len(l) + 1 for l in MIXED.splitlines() if l.startswith("|"))
    # Мера берётся по прозе: таблицы стережёт отдельная проверка, и урезать их
    # заданием не просят вовсе.
    ok("таблицы в пороге не режутся",
       P._floor(MIXED, 10 ** 6) >= tbl, P._floor(MIXED, 10 ** 6))
    ok("проза уходит на две трети",
       P._floor(MIXED, 10 ** 6) == tbl + (len(MIXED) - tbl) // 3,
       P._floor(MIXED, 10 ** 6))
    ok("просят не больше, чем нужно",
       P._floor(MIXED, 100) == len(MIXED) - 100, P._floor(MIXED, 100))
    ok("раздел без таблиц ужимается сильнее",
       P._floor(PLAIN, 10 ** 6) == len(PLAIN) // 3, P._floor(PLAIN, 10 ** 6))

    import tempfile
    d = tempfile.mkdtemp()
    out = f"{d}/scout.md"

    # Справочника в системном промпте быть не должно: иначе он уезжает на
    # вход по разу на каждый запрос, а модель, увидев его целиком,
    # перекраивает всё. Совсем пустым промпт тоже не оставить — агентная
    # обёртка без системы отвечает пустотой, — поэтому там ровно одна
    # пристёгивающая строка из prompts/text_only.md.
    seen = []

    class Watch(Obedient):
        def run(self, system, user):
            seen.append(system)
            return Obedient.run(self, system, user)

    pin = P._text_only()
    got = P._condense_scout(BOOK, [Watch()], "", 1, hush, out)
    ok("справочник не уезжает в системный промпт",
       seen and all(x == pin for x in seen), seen[:1])
    # Бюджет меряется по костяку: реестр не в счёт, он едет с куском.
    ok("костяк уложился в предел", len(P.split_ref(got)[0]) <= P.SCOUT_BUDGET,
       f"{len(P.split_ref(got)[0])} знаков при пределе {P.SCOUT_BUDGET}")
    ok("послушная модель таблиц не трогает", P._rows(BOOK) == P._rows(got),
       f"было {len(P._rows(BOOK))}, стало {len(P._rows(got))}")
    ok("сжатое записано на диск",
       os.path.exists(out) and open(out, encoding="utf-8").read() == got)

    # Строки таблиц с исходными больше не сверяются: первой ступенью лестницы
    # стоит «выбросить очевидное», а очевидное — это как раз строки. Мерой
    # осталась длина: ответ короче половины запрошенного — не сжатие, а
    # выброшенный справочник.
    got = P._condense_scout(BOOK, [Greedy()], "", 1, hush, f"{d}/g.md")
    ok("выпотрошенный справочник отвергнут", got == BOOK,
       f"стало {len(got)} знаков вместо {len(BOOK)}")

    # Дожали не до конца — при неизменном справочнике ту же модель не
    # переспрашиваем: на живой книге второй прогон затевал то же сжатие
    # заново, с тем же исходом и за те же деньги.
    class Halfway(Obedient):
        """Уступает, но до предела не дожимает."""
        model = "упрямая"
        def run(self, system, user):
            import re as _re
            want = int(_re.search(r"надо около (\d+)", user).group(1))
            part = _part(user)
            out = part[:max(want + 3000, len(part) * 2 // 3)]
            return _box(out, want), {"model": self.model, "cost_usd": 0}
    hw = Halfway()
    first = P._condense_scout(BOOK, [hw], "", 1, hush, f"{d}/hw.md")
    cache = json.load(open(f"{d}/hw.no_shrink.json", encoding="utf-8"))
    ok("дожато не до конца — размер записан",
       len(P.split_ref(first)[0]) > P.SCOUT_BUDGET
       and cache.get("упрямая") == len(P.split_ref(first)[0]),
       cache)
    # повторный заход с тем же текстом — запросов быть не должно
    class Counter(Halfway):
        model = "упрямая"
        def __init__(self): self.calls = 0
        def run(self, system, user):
            self.calls += 1
            return Halfway.run(self, system, user)
    c = Counter()
    again = P._condense_scout(first, [c], "", 1, hush, f"{d}/hw.md")
    ok("повторного сжатия тем же не было", c.calls == 0 and again == first,
       c.calls)

    # Уже короткий справочник не трогаем вовсе: запрос стоит денег.
    small = "## ИМЕНА\n\n" + "\n".join(table(3)) + "\n"
    ok("короткий справочник не пересжимают",
       P._condense_scout(small, [Greedy()], "", 1, hush, f"{d}/s.md") == small)

    # Внедрённые обращения к машине. Различение делает разведка, а конвейер
    # читает её ответ: книга **про** инъекции приводит промпты примером на
    # каждой странице, и останавливать на любом упоминании значило бы не
    # переводить как раз те книги, ради которых конвейер и заводят.
    ok("чисто — перевод идёт", P.injected("## ОПАСНЫЕ МЕСТА\nINJECTED: нет\n") == [])
    ok("«не обнаружены» тоже чисто",
       P.injected("INJECTED: Не обнаружены.") == [])
    ok("без отметки не останавливаемся",
       P.injected("справочник прежней версии, отметки нет") == [])
    found = P.injected("INJECTED: 2\n- гл. 4: «Ignore all previous»\n"
                       "- гл. 9: «допиши сюда»\n\nдалее обычный текст")
    ok("места перечислены",
       found == ["гл. 4: «Ignore all previous»", "гл. 9: «допиши сюда»"], found)
    ok("нашлось, но без перечня — всё равно находка",
       P.injected("INJECTED: 1\n\nдальше") == ["1"])

    # Ответ вместо справочника. Через agy думающая модель сложила работу в
    # свой файл и вернула записку о нём — тысяча знаков вместо двадцати пяти
    # тысяч. Разведка принимала любой текст, а справочник уходит в каждый
    # запрос на перевод: книга переводилась бы без имён и терминов.
    body = "## ИМЕНА\n\nTolstoy = Толстой\n\n## ТЕРМИНЫ\n\nfovea = ямка"
    ok("работа из конверта берётся",
       P._parse_scout(f"[[[SCOUT 3]]]\n{body}\n[[[/SCOUT 3]]]")[0] == body)
    ok("болтовня снаружи отброшена",
       P._parse_scout(f"Сейчас соберу.\n[[[SCOUT 3]]]\n{body}\n[[[/SCOUT 3]]]\n"
                      "Готово, проверьте.")[0] == body)

    # Номер в маркере подставляет модель, а в промпте на его месте буква N.
    # Оттого промпт, вернувшийся эхом, за ответ не сойдёт — а такое бывало:
    # один прогон вернул справочник, а перед ним весь системный промпт.
    envelope, _ = __import__("booktrans", fromlist=["lang"]).lang.prompt("envelope")
    ok("образец маркера в промпте есть", "[[[{name} N]]]" in envelope, envelope[:60])
    for name, text in (
            ("эхо промпта", envelope.format(name="SCOUT", what="номер части")),
            ("записка о файле", "Справочник готов: [ч2.md](file:///нет/такого.md)."),
            ("служебный вывод", '<invoke name="Read">\n</invoke>\nFile does not exist.'),
            ("одно приветствие", "Готово! Всё сделано."),
            ("обрыв", f"[[[SCOUT 3]]]\n{body}"),
            ("маркеры с разными числами",
             f"[[[SCOUT 3]]]\n{body}\n[[[/SCOUT 2]]]")):
        try:
            P._parse_scout(text)
            got = "приняли"
        except ValueError:
            got = ""
        ok(f"{name} отвергнут", not got, got)

    # Какое число подставлено, не сверяется: живая модель на первой части
    # написала «4», и повтор всё исправил, но стоил целого запроса. От эха
    # бережёт сам факт подстановки цифр.
    ok("число не то, но подставлено — принимаем",
       P._parse_scout(f"[[[SCOUT 4]]]\n{body}\n[[[/SCOUT 4]]]")[0] == body)

    # Справочник без конверта: работа сделана, маркеров нет. Принимается по
    # той же мере, что файл, — есть разделы; приписка до первого раздела
    # отрезается. Эхо промпта не пройдёт: в нём образец маркера из конверта.
    ok("справочник без конверта принят",
       P._parse_scout(f"Сейчас соберу.\n{body}")[0] == body,
       P._parse_scout(f"Сейчас соберу.\n{body}")[0][:40])

    # Работа при этом сделана и оплачена, а путь назван — забираем файл.
    # Маркеров в нём нет и быть не может, поэтому мера тут другая: разделы.
    f = f"{d}/справочник часть 2.md"
    open(f, "w", encoding="utf-8").write(body)
    said = f"Справочник готов: [файл](file://{f.replace(' ', '%20')})."
    ok("названный файл подбирается", P._parse_scout(said)[0] == body,
       P._parse_scout(said)[0][:40] if os.path.exists(f) else "нет файла")

    # Выходные данные из справочника. Заголовок раздела модель пишет на
    # целевом языке — «PUBLICATION DATA», — и по списку из четырёх названий
    # он не находился: книга выходила под именем файла, без автора и с
    # непереведённым словом в заглавии.
    # Ключ латиницей — то, на что разбор опирается теперь; прочие случаи
    # оставлены ради справочников прежних выпусков.
    for name, head in (("с ключом", "## META — Выходные данные"),
                       ("ключ по-японски", "## META — 奥付"),
                       ("прежний, по-русски", "## ВЫХОДНЫЕ ДАННЫЕ"),
                       ("прежний, по-английски", "## PUBLICATION DATA"),
                       ("прежний, по-испански", "## DATOS DE PUBLICACIÓN")):
        os.makedirs(f"{d}/xx", exist_ok=True)
        open(f"{d}/xx/scout.md", "w", encoding="utf-8").write(
            f"{head}\n\ntitle = Несуществующий\n"
            "title_target = The Nonexistent One\n"
            "author = Виктория Викторовна Зименкова\n"
            "author_target = Viktoria Viktorovna Zimenkova\n"
            "genre = sf_fantasy\n\n## NAMES — Имена\n\nПётр = Пётр\n")
        got = P.scout_meta(d, "xx")
        ok(f"выходные данные найдены, заголовок {name}",
           got.get("author_target") == "Viktoria Viktorovna Zimenkova"
           and got.get("genre") == "sf_fantasy", got)

    # Двоящиеся термины ищутся в таблицах имён и терминов, и раздел прежде
    # узнавался по названию: на японском или испанском справочнике не
    # находилось ничего — тоже молча. Ключ снимает и это.
    pair = ("| Оригинал | Русский |\n|---|---|\n"
            "| grand illusion | великая иллюзия / большой обман |\n")
    ok("двоящийся термин виден под ключом",
       [t for t, _ in P._forked(f"## TERMS — 用語\n\n{pair}", "ru")]
       == ["grand illusion"],
       P._forked(f"## TERMS — 用語\n\n{pair}", "ru"))
    ok("вне таблиц имён и терминов не ищем",
       P._forked(f"## CHARACTERS — Персонажи\n\n{pair}", "ru") == [])

    # Имена соседних книг цикла (`--like`) сводятся ПОСЛЕ разведки кодом:
    # просьба в промпте оставляла модели волю (на живой книге разведка молча
    # переименовала заглавие при живом каноне), а бюджет промпта обрезал
    # цикл на второй книге из четырёх.
    import shutil as _sh
    for k, (title, row) in enumerate((("The First", "| Пётр | Pyotr |"),
                                      ("The Second", "| Michael Poole | Michael Poole |")), 1):
        os.makedirs(f"{d}/cyc{k}/en", exist_ok=True)
        open(f"{d}/cyc{k}/en/scout.md", "w", encoding="utf-8").write(
            f"## META — Выходные данные\n\ntitle_target = {title}\n"
            "author_target = Viktoria Zimenkova\n\n"
            "## CHARACTERS — Персонажи\n\nдлинная проза\n\n"
            f"## NAMES — Имена\n\n{row}\n| Анна | Anna |\n\n"
            "## TERMS — Термины\n\n| меч-кладенец | magic sword |\n")
    os.makedirs(f"{d}/cur/en", exist_ok=True)
    sp = f"{d}/cur/en/scout.md"
    open(sp, "w", encoding="utf-8").write(
        "## META — Выходные данные\n\ntitle_target = The Third\n"
        "author_target = Victoria Zimenkova\n\n"
        "## NAMES — Имена\n\n| Пётр | Peter |\n| Poole | Пул |\n\n"
        "## TERMS — Термины\n\n| изба | hut |\n")
    blocks = [{"text": "Пётр и Анна вошли, Poole ждал их у избы."}]
    P.cycle_merge(f"{d}/cur", [f"{d}/cyc1", f"{d}/cyc2"], "en", blocks)
    got = open(sp, encoding="utf-8").read()
    ok("имя сведено к канону старшей книги",
       "| Пётр | Pyotr |" in got and "Peter" not in got,
       [l for l in got.splitlines() if "Пётр" in l or "Peter" in l])
    ok("подмножество слов гасит дописывание, не замену",
       "| Poole | Пул |" in got and "| Michael Poole |" not in got,
       [l for l in got.splitlines() if "Poole" in l])
    ok("пропущенное разведкой имя дописано из канона",
       "| Анна | Anna |" in got, [l for l in got.splitlines() if "Анна" in l])
    ok("термина, которого нет в тексте, не дописываем",
       "меч-кладенец" not in got, [l for l in got.splitlines() if "меч" in l])
    ok("своя строка без канона не тронута", "| изба | hut |" in got)

    ok("автор наследуется от старшей книги",
       "author_target = Viktoria Zimenkova" in got,
       [l for l in got.splitlines() if "author_target" in l])
    P.cycle_merge(f"{d}/cur", [f"{d}/cyc1", f"{d}/cyc2"], "en", blocks)
    ok("сведение идемпотентно", open(sp, encoding="utf-8").read() == got)
    P.cycle_merge(f"{d}/cur", [f"{d}/нет-такой"], "en", blocks)
    ok("несуществующая книга пропускается",
       open(sp, encoding="utf-8").read() == got)
    ok("перед записью остаётся копия",
       os.path.exists(sp + ".bak"))

    # Сведение, изменившее справочник, равняет кэш «дожать не вышло» на новый
    # размер: иначе пересжатие и сведение играют в пинг-понг — пересжатие
    # выбрасывает строки имён цикла, сведение возвращает, размер меняется, и
    # каждый запуск платит за то же сжатие заново.
    nsp = P._no_shrink_path(sp)
    json.dump({"m1": 11, "m2": None}, open(nsp, "w", encoding="utf-8"))
    open(sp, "w", encoding="utf-8").write(
        "## META — Выходные данные\n\ntitle_target = The Third\n\n"
        "## NAMES — Имена\n\n| Пётр | Peter |\n")
    P.cycle_merge(f"{d}/cur", [f"{d}/cyc1"], "en", blocks)
    size = len(open(sp, encoding="utf-8").read())
    got_ns = json.load(open(nsp, encoding="utf-8"))
    ok("сведение равняет кэш пересжатия на новый размер",
       got_ns == {"m1": size, "m2": size}, (got_ns, size))
    # Имена живут и в подразделах: `## NAMES` кончается не на `### Люди`,
    # а на следующем заголовке того же уровня. Пока сведение видело только
    # тело `## NAMES` — пустое, — свои строки оставались невидимы, и канон
    # дописывался целиком: 360 строк на живой книге.
    os.makedirs(f"{d}/sub/en", exist_ok=True)
    sp2 = f"{d}/sub/en/scout.md"
    open(sp2, "w", encoding="utf-8").write(
        "## META — Выходные данные\n\ntitle_target = Subs\n\n"
        "## NAMES — Имена\n\n### Люди\n\n| Пётр | Peter |\n\n"
        "### Места\n\n| изба | hut |\n\n"
        "## GENDER — Род\n\n| Пётр | м |\n")
    P.cycle_merge(f"{d}/sub", [f"{d}/cyc2"], "en", blocks)
    got2 = open(sp2, encoding="utf-8").read()
    ok("строка в подразделе видна сведению",
       got2.count("Пётр") == 2 and "Peter" in got2,
       [l for l in got2.splitlines() if "Пётр" in l])
    ok("дописанное встаёт в хвост раздела, не в GENDER",
       got2.index("| Анна | Anna |") < got2.index("## GENDER"),
       got2[got2.index("### Места"):][:120])
    # Дописывание — только по полной фразе ключа: у терминов слова обычные,
    # и правило «хватит любого слова» тащило канон целиком.
    ok("однословного совпадения мало",
       "Michael Poole" not in got2,
       [l for l in got2.splitlines() if "Poole" in l])
    # Артикль не рознит ключи, а разделы без решёток читаются: на живой
    # книге «META» голым словом оставило книгу без заглавия, а «Qax» против
    # канонного «the Qax» — расу в чужом переводе.
    os.makedirs(f"{d}/bare/en", exist_ok=True)
    sp3 = f"{d}/bare/en/scout.md"
    open(sp3, "w", encoding="utf-8").write(P._headify(
        "META\ntitle_target = Bare\n\nNAMES\nQax = кваксы\n"))
    os.makedirs(f"{d}/cyc4/en", exist_ok=True)
    open(f"{d}/cyc4/en/scout.md", "w", encoding="utf-8").write(
        "## META — Выходные данные\n\ntitle_target = Old\n\n"
        "## NAMES — Имена\n\n| the Qax | хаксы |\n")
    P.cycle_merge(f"{d}/bare", [f"{d}/cyc4"], "en",
                  [{"text": "the Qax ruled"}])
    got3 = open(sp3, encoding="utf-8").read()
    ok("голое слово-раздел стало заголовком", "## META" in got3
       and "## NAMES" in got3, got3.splitlines()[:2])
    ok("артикль не мешает сведению",
       "| the Qax | хаксы |" in got3 and "кваксы" not in got3,
       [l for l in got3.splitlines() if "акс" in l])
    _sh.rmtree(f"{d}/cyc1", ignore_errors=True)

    # Сноска начинается с термина: по ссылке читалка показывает её одну,
    # без абзаца вокруг. Начатую с термина не дублируем, склонение — по
    # основе слова.
    for it, want in (({"text": "a TV channel.", "term": "Vesti"},
                      "Vesti — a TV channel."),
                     ({"text": "Vesti is a TV channel.", "term": "Vesti"},
                      "Vesti is a TV channel."),
                     ({"text": "Эсхатологию поминают всуе.", "term": "эсхатология"},
                      "Эсхатологию поминают всуе."),
                     # составной TERM: лид — первая часть, а текст, начатый
                     # со второй, лида не получает («же — Же…» не нужно)
                     ({"text": "Же — кратность силы тяжести.", "term": "микроже, же"},
                      "Же — кратность силы тяжести."),
                     ({"text": "кратность силы тяжести.", "term": "микроже, же"},
                      "микроже — кратность силы тяжести."),
                     ({"text": "без термина.", "term": ""}, "без термина.")):
        ok(f"термин впереди: {it['term'] or 'пусто'}", P._lead(it) == want,
           P._lead(it))

    # Сведение пачками: транспорт молча режет длинный вход, поэтому разборы
    # группируются в пределах лимита — жадно и с сохранением порядка.
    f10 = [f"разбор {i} " + "x" * 24000 for i in range(10)]
    bs = P._merge_batches(f10, 100000)
    ok("пачки: каждая в пределе",
       all(sum(len(x) for x in b) <= 100000 for b in bs),
       [sum(len(x) for x in b) for b in bs])
    ok("пачки: ничего не потеряно, порядок цел",
       [x for b in bs for x in b] == f10, len(bs))
    ok("пачки: мелочь одной пачкой",
       len(P._merge_batches(["a", "b"], 100)) == 1, None)
    ok("пачки: переросток отдельно и не падает",
       [len(b) for b in P._merge_batches(["y" * 200, "z"], 100)] == [1, 1],
       None)
    ok("пачки: пусто — пусто", P._merge_batches([], 100) == [], None)

    # Сведение переживает рестарт: каждая сведённая пачка тут же ложится в
    # scout.merge.json, и оборванный прогон продолжает пирамиду с уцелевшего,
    # а не пересводит уже оплаченное с нуля.
    import tempfile as _tf
    md = _tf.mkdtemp()
    f3 = [f"## РАЗБОР {i}\n\n" + "строка наблюдений; " * 2500 for i in range(3)]
    half = os.path.join(md, "ru", "scout.part.json")
    os.makedirs(os.path.dirname(half), exist_ok=True)
    json.dump(f3, open(half, "w", encoding="utf-8"), ensure_ascii=False)

    class Mortal:
        model, kind = "смертная", "стенд"

        def __init__(self):
            self.calls = 0

        def run(self, system, user):
            self.calls += 1
            if self.calls > 1:
                raise P.agent_mod.Fatal("обрыв между пачками")
            return ("[[[SCOUT 2]]]\n## СВОДКА\n\nпачка сведена\n[[[/SCOUT 2]]]",
                    {"model": self.model, "cost_usd": 0})

    crashed = False
    try:
        P.scout(md, [], Mortal(), "", "задание", 1, hush)
    except P.agent_mod.Fatal:
        crashed = True
    mfile = os.path.join(md, "ru", "scout.merge.json")
    saved = (json.load(open(mfile, encoding="utf-8"))
             if os.path.exists(mfile) else [])
    ok("обрыв сведения: сведённая пачка уцелела",
       crashed and len(saved) == 2 and "пачка сведена" in saved[0]
       and saved[1] == f3[2], (crashed, len(saved)))

    class Counter(Mortal):
        model = "счётная"

        def run(self, system, user):
            self.calls += 1
            return ("[[[SCOUT 2]]]\n## СВОДКА\n\nвсё сведено\n[[[/SCOUT 2]]]",
                    {"model": self.model, "cost_usd": 0})

    c = Counter()
    got = P.scout(md, [], c, "", "задание", 1, hush)
    ok("рестарт доводит сведение одним запросом",
       c.calls == 1 and "всё сведено" in got, (c.calls, got[:40]))
    ok("кэши сведения убраны за собой",
       not os.path.exists(mfile) and not os.path.exists(half), None)

    # Канон цикла едет в запрос части — но только строками, чьи имена в
    # части встречаются: канон целиком уже резал бюджет на живом цикле.
    md2 = _tf.mkdtemp()
    prev = os.path.join(md2, "prev.work")
    os.makedirs(os.path.join(prev, "ru"), exist_ok=True)
    open(os.path.join(prev, "ru", "scout.md"), "w", encoding="utf-8").write(
        "## NAMES — Имена\n\n| the Qax | хаксы | раса |\n"
        "| Poole | Пул | человек |\n")
    seen = []

    class Spy:
        model, kind = "шпион", "стенд"

        def run(self, system, user):
            seen.append(user)
            raise P.agent_mod.Fatal("хватит")

    blocks2 = [{"kind": "p", "text": "The Qax ruled Earth. " * 40}]
    try:
        P.scout(os.path.join(md2, "new.work"), blocks2, Spy(), "", "задание",
                1, hush, likes=[prev])
    except P.agent_mod.Fatal:
        pass
    ok("канон цикла в части: упомянутое есть, лишнего нет",
       bool(seen) and "| the Qax | хаксы" in seen[0] and "Пул" not in seen[0],
       (seen[0][:160] if seen else "запроса не было"))

    # Часть, начавшаяся посреди главы, знает эту главу, а разборы приходят
    # на сведение подписанными: пачки пирамиды друг друга не видят, и
    # перемену («женщина» в части 1 — «мужчина» в части 2) сведению нечем
    # было бы датировать.
    md3 = _tf.mkdtemp()
    cap = {"parts": [], "merge": ""}

    class Teller:
        model, kind = "рассказчик", "стенд"

        def run(self, system, user):
            if "## Часть" in user:
                cap["parts"].append(user)
                i = len(cap["parts"])
                return (f"[[[SCOUT {i}]]]\n## РАЗБОР\n\nчасть {i}\n"
                        f"[[[/SCOUT {i}]]]",
                        {"model": self.model, "cost_usd": 0})
            cap["merge"] = user
            raise P.agent_mod.Fatal("хватит")

    blocks3 = ([{"kind": "title", "text": "Глава первая"}]
               + [{"kind": "p", "text": "слово " * 200} for _ in range(180)])
    try:
        P.scout(os.path.join(md3, "w.work"), blocks3, Teller(), "", "задание",
                1, hush)
    except P.agent_mod.Fatal:
        pass
    p1, p2 = (cap["parts"] + ["", ""])[:2]
    ok("часть с заголовком — без подсказки о главе",
       "## Часть 1 из 2" in p1 and "посреди главы" not in p1, p1[:120])
    ok("часть посреди главы знает её имя",
       "## Часть 2 из 2" in p2 and "посреди главы «Глава первая»" in p2,
       p2[:120])
    ok("разборы на сведении подписаны частями",
       "[разбор части 1 из 2]" in cap["merge"]
       and "[разбор части 2 из 2]" in cap["merge"], cap["merge"][:160])

    import shutil
    shutil.rmtree(md3, ignore_errors=True)
    shutil.rmtree(md2, ignore_errors=True)
    shutil.rmtree(md, ignore_errors=True)

    # Реестр: строки всех частей сводятся кодом и без потерь. Пирамида
    # пересжимала их к бюджету на каждом уровне, и на большой книге
    # второстепенные имена выпадали до того, как становилось известно,
    # нужны ли они.
    f_a = ("## CHARACTERS — Персонажи\n\n"
           "| Скиттер (Skitter) | девушка, говорит коротко |\n\n"
           "## NAMES — Имена\n\n| Brockton Bay | Броктон-Бей |\n")
    f_b = ("## CHARACTERS — Персонажи\n\n"
           "| Скиттер (Skitter) | девушка, говорит коротко |\n"
           "- **Клокблокер (Clockpicker):** шутник.\n\n"
           "## NAMES — Имена\n\n| Brockton Bay | Броктон-Бэй |\n")
    order, groups, heads = P._registry([f_a, f_b])
    ok("реестр: дословный повтор не двоится",
       groups[("CHARACTERS", P._norm_key("Скиттер / Skitter"))]
       == [(1, "| Скиттер (Skitter) | девушка, говорит коротко |")],
       groups)
    ok("реестр: карточка-маркер ключуется",
       ("CHARACTERS", P._norm_key("Клокблокер / Clockpicker")) in groups,
       list(groups))
    con = [(gk, key, groups[gk]) for gk, key, _ in order
           if len(groups[gk]) > 1]
    ok("реестр: разноголосица найдена",
       [gk[1] for gk, _, _ in con] == [P._norm_key("Brockton Bay")], con)
    ok("составной ключ не зависит от порядка половин",
       P._norm_key("Skitter / Скиттер") == P._norm_key("Скиттер / Skitter"))
    ok("артикль не рознит ключи",
       P._norm_key("the Qax") == P._norm_key("Qax"))
    text = P._render_registry(order, groups, heads)
    ok("реестр собран под шапками разделов",
       text.index("## CHARACTERS") < text.index("Клокблокер")
       < text.index("## NAMES"), text[:200])
    ok("несведённый ключ пока держит оба варианта",
       "Броктон-Бей" in text and "Броктон-Бэй" in text, text)

    # Разноголосицу сводит модель — точечно, только спорные ключи, с
    # подписями частей для датировки перемен. Не свелось — остаётся вариант
    # самой ранней части.
    asked = []

    class Judge:
        model, kind = "судья", "стенд"

        def run(self, system, user):
            asked.append(user)
            return ("[[[SCOUT 1]]]\n| Brockton Bay | Броктон-Бей |\n"
                    "[[[/SCOUT 1]]]", {"model": self.model, "cost_usd": 0})

    got_rows = P._settle_rows(con, 2, [Judge()], "", 1, hush)
    ok("спорному ключу — вопрос с подписанными вариантами",
       asked and "### brockton bay" in asked[0].lower()
       and "[разбор части 1 из 2]" in asked[0]
       and "[разбор части 2 из 2]" in asked[0], (asked or [""])[0][:200])
    ok("ответ судьи лёг по ключу",
       got_rows == {con[0][0]: "| Brockton Bay | Броктон-Бей |"}, got_rows)

    class Mute:
        model, kind = "немой", "стенд"

        def run(self, system, user):
            raise P.agent_mod.Fatal("молчу")

    ok("судья пал — реестр не потерян",
       P._settle_rows(con, 2, [Mute()], "", 1, hush) == {}, None)

    # Сквозной прогон: строки частей доезжают до scout.md без потерь, а
    # имя из части 1 приезжает каноном в запрос части 2.
    md4 = _tf.mkdtemp()
    cap4 = []

    class TwoParts:
        model, kind = "двухчастный", "стенд"

        def run(self, system, user):
            if "## Часть" in user:
                cap4.append(user)
                i = len(cap4)
                body = (f_a, f_b)[i - 1]
                return (f"[[[SCOUT {i}]]]\n{body}\n[[[/SCOUT {i}]]]",
                        {"model": self.model, "cost_usd": 0})
            return ("[[[SCOUT 2]]]\n## VOICES — Слог\n\nсухой и быстрый\n"
                    "[[[/SCOUT 2]]]", {"model": self.model, "cost_usd": 0})

    blocks4 = [{"kind": "p", "text": "Skitter watched Brockton Bay. " * 40}
               for _ in range(181)]
    got4 = P.scout(os.path.join(md4, "w.work"), blocks4, TwoParts(), "",
                   "задание", 1, hush)
    ok("канон своей части едет в следующую",
       len(cap4) == 2 and "| Скиттер (Skitter) |" in cap4[1]
       and "| Скиттер (Skitter) |" not in cap4[0],
       (len(cap4), cap4[1][:200] if len(cap4) > 1 else ""))
    ok("строки частей дошли до справочника без пирамиды",
       "Клокблокер" in got4 and "| Скиттер (Skitter) |" in got4, got4[:300])
    ok("костяк пирамиды в справочнике",
       "сухой и быстрый" in got4, got4[:200])
    ok("шапка без прозы в костяк не идёт",
       "## NAMES" not in P.split_ref(got4)[0], P.split_ref(got4)[0])
    shutil.rmtree(md4, ignore_errors=True)

    # Волны по --jobs: части одной волны идут параллельно и друг друга не
    # видят, канон своих частей обновляется на границе волн.
    import threading as _th
    md5 = _tf.mkdtemp()
    lock5, seen5 = _th.Lock(), {}

    class Wavy:
        model, kind = "волновой", "стенд"

        def run(self, system, user):
            m = re.search(r"## Часть (\d) из 4", user)
            if not m:
                return ("[[[SCOUT 4]]]\n## VOICES\n\nровный слог\n[[[/SCOUT 4]]]",
                        {"model": self.model, "cost_usd": 0})
            i = int(m.group(1))
            with lock5:
                seen5[i] = user
            extra = "\n| Redmane | Красногрив |" if i == 1 else ""
            return (f"[[[SCOUT {i}]]]\n## NAMES — Имена\n\n"
                    f"| Hero{i} | Герой{i} |{extra}\n[[[/SCOUT {i}]]]",
                    {"model": self.model, "cost_usd": 0})

    blocks5 = [{"kind": "p", "text": "Redmane rode. " * 9050 + f"Hero{i} walked."}
               for i in range(1, 5)]
    got5 = P.scout(os.path.join(md5, "w.work"), blocks5, Wavy(), "",
                   "задание", 1, hush, jobs=2)
    ok("волны: все четыре части разобраны", sorted(seen5) == [1, 2, 3, 4],
       sorted(seen5))
    ok("волны: сосед по волне не виден",
       "Красногрив" not in seen5.get(2, "") and "Красногрив" not in seen5.get(1, ""),
       None)
    ok("волны: следующая волна видит канон первой",
       "| Redmane | Красногрив |" in seen5.get(3, "")
       and "| Redmane | Красногрив |" in seen5.get(4, ""), seen5.get(3, "")[:200])
    ok("волны: реестр собрал все части по порядку",
       all(f"| Hero{i} | Герой{i}" in got5 for i in range(1, 5))
       and got5.index("Герой1") < got5.index("Герой3"), got5[-300:])

    # Обрыв волны оставляет в кэше пустышки на месте неразобранных частей —
    # перезапуск обязан их доразобрать, а не упасть на накоплении канона.
    md6 = _tf.mkdtemp()
    half6 = os.path.join(md6, "w.work", "ru", "scout.part.json")
    os.makedirs(os.path.dirname(half6), exist_ok=True)
    json.dump(["## NAMES — Имена\n\n| Hero1 | Герой1 |", None, None, None],
              open(half6, "w", encoding="utf-8"), ensure_ascii=False)
    seen5.clear()
    got6 = P.scout(os.path.join(md6, "w.work"), blocks5, Wavy(), "",
                   "задание", 1, hush, jobs=2)
    ok("перезапуск после обрыва волны доразобрал пустышки",
       sorted(seen5) == [2, 3, 4]
       and all(f"| Hero{i} | Герой{i}" in got6 for i in range(1, 5)),
       (sorted(seen5), got6[-200:]))
    shutil.rmtree(md6, ignore_errors=True)
    shutil.rmtree(md5, ignore_errors=True)
    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
