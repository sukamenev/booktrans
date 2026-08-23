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

    # Системный промпт обязан быть пуст: иначе справочник уезжает на вход по
    # разу на каждый запрос, а модель, увидев его целиком, перекраивает всё.
    seen = []

    class Watch(Obedient):
        def run(self, system, user):
            seen.append(system)
            return Obedient.run(self, system, user)

    got = P._condense_scout(BOOK, [Watch()], "", 1, hush, out)
    ok("справочник не уезжает в системный промпт",
       seen and not any(x.strip() for x in seen), seen[:1])
    ok("справочник уложился в предел", len(got) <= P.SCOUT_BUDGET,
       f"{len(got)} знаков при пределе {P.SCOUT_BUDGET}")
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
       len(first) > P.SCOUT_BUDGET and cache.get("упрямая") == len(first),
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

    # Имена соседних книг цикла (`--like`). В разведку уходят только имена и
    # термины: справочник целиком — тридцать тысяч знаков, а эти два раздела
    # — три-семь. Порядок значим: он же порядок выхода книг.
    import shutil as _sh
    for k, (title, who) in enumerate((("The First", "Пётр"), ("The Second", "Иван")), 1):
        os.makedirs(f"{d}/cyc{k}/en", exist_ok=True)
        open(f"{d}/cyc{k}/en/scout.md", "w", encoding="utf-8").write(
            f"## META — Выходные данные\n\ntitle_target = {title}\n\n"
            f"## CHARACTERS — Персонажи\n\nдлинная проза про персонажей\n\n"
            f"## NAMES — Имена\n\n| {who} | {who} |\n\n"
            f"## TERMS — Термины\n\n| меч | sword |\n")
    got = P.cycle_names([f"{d}/cyc1", f"{d}/cyc2"], "en")
    ok("книги идут в заданном порядке",
       [l for l in got.splitlines() if l.startswith("### ")]
       == ["### 1. The First", "### 2. The Second"],
       [l for l in got.splitlines() if l.startswith("### ")])
    ok("взяты только имена и термины",
       "Пётр" in got and "меч" in got and "проза про персонажей" not in got,
       got[:80])
    # Сквозное имя есть в каждой книге цикла; повторы съедали бюджет и
    # вытесняли редкие имена. Остаётся запись самой ранней книги.
    os.makedirs(f"{d}/cyc3/en", exist_ok=True)
    open(f"{d}/cyc3/en/scout.md", "w", encoding="utf-8").write(
        "## META — Выходные данные\n\ntitle_target = The Third\n\n"
        "## NAMES — Имена\n\n| Пётр | Питер |\n| Анна | Анна |\n")
    got3 = P.cycle_names([f"{d}/cyc1", f"{d}/cyc3"], "en")
    ok("повтор имени выброшен, ранняя запись главнее",
       "Питер" not in got3 and "| Пётр | Пётр |" in got3 and "Анна" in got3,
       [l for l in got3.splitlines() if "Пётр" in l or "Анна" in l])

    ok("несуществующая книга пропускается",
       P.cycle_names([f"{d}/нет-такой"], "en") == "")
    # Предел бережёт запрос разведки: справочник цикла в него уходит целиком.
    was, P.CYCLE_BUDGET = P.CYCLE_BUDGET, 120
    small = P.cycle_names([f"{d}/cyc1", f"{d}/cyc2"], "en")
    P.CYCLE_BUDGET = was
    ok("предел соблюдается", len(small) < 400, len(small))
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

    import shutil
    shutil.rmtree(d, ignore_errors=True)
    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
