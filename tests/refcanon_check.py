#!/usr/bin/env python3
"""Проверка перекладки справочника в строки одного вида.

Старый ключ «Перевод (Оригинал)» отбирался для куска только когда оригинал
угадывался в скобках, а род и свойства сущности жили в отдельных таблицах.
Теперь у сущности одна строка `| оригинал | перевод | род | содержимое |`.
Здесь проверяется разбор старого ключа, перекладка разведки, вливание
GENDER и WORLD в строки сущностей и однократная конвертация рабочей папки
по её версии.

    python3 tests/refcanon_check.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import lang                                  # noqa: E402
from booktrans import refconvert as R                       # noqa: E402

lang.set_ui("ru")

OLD = """## META — Выходные данные

title_target = Проба

## VOICES — Повествование

| Рассказчик | Тейлор, 1-е лицо |

## CHARACTERS — Персонажи

| Оригинал | Карточка |
|---|---|
| Разлом (Faultline) | руководительница наёмников |
- **Taylor:** Тейлор — школьница.
- **Клокблокер (Clockpicker):** остряк.
| Сыон / Scion | золотой человек |

## NAMES — Имена и названия

| Brockton Bay | Броктон-Бей | город |
| Zoe | Зои | вымышленный | мать Эммы |

## TERMS — Термины

| cape | кейп — сверхчеловек |

## GENDER — Род и склонение

| Имя / сущность (оригинал) | Род и склонение | Пояснение |
| Разлом (Faultline) | ж, склоняется |
| Земля-Алеф | средний |
| Taylor, Clockpicker | пол не сказан |
| Vantage | Вэнтидж — мужчина, склоняется |

## ADDRESS — Обращения

| Taylor — Danny | отец и дочь: на «ты» |
| Тейлор и Рейчел | на «ты» |
| Эмма и Софи | на «ты» |

## WORLD — Мир

| Brockton Bay | портовый город |
| bullet ant | муравей-пуля — ядовитый муравей |
| Hebert house | дом Эбертов | старый дом с тонкими стенами |

## FOOTNOTES — Кандидаты в сноски

| Cliff notes («краткая выжимка») | американская реалия |

## ОКОНЧАТЕЛЬНЫЙ ВЫБОР ПО ТЕРМИНАМ

| Scion | Сыон |
"""


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    tgt = R._script_re("ru")
    row = lambda line, sec="CHARACTERS", **kw: R.canon_row(   # noqa: E731
        line, sec, tgt, **kw)

    ok("«Перевод (Оригинал)» — четыре ячейки, род пуст",
       row("| Разлом (Faultline) | руководительница |")[0]
       == "| Faultline | Разлом | | руководительница |",
       row("| Разлом (Faultline) | руководительница |"))
    ok("карточка-маркер — строкой таблицы",
       row("- **Клокблокер (Clockpicker):** остряк.")[0]
       == "| Clockpicker | Клокблокер | | остряк. |",
       row("- **Клокблокер (Clockpicker):** остряк."))
    ok("«Перевод — справка» одной ячейкой — надвое",
       row("- **Taylor:** Тейлор — школьница.")[0]
       == "| Taylor | Тейлор | | школьница. |",
       row("- **Taylor:** Тейлор — школьница."))
    ok("одно написание — перевод, содержимого нет",
       row("| Brockton Bay | Броктон-Бей. |", "NAMES")[0]
       == "| Brockton Bay | Броктон-Бей | | |",
       row("| Brockton Bay | Броктон-Бей. |", "NAMES"))
    ok("справка без заглавной — не перевод",
       row("| GstringGirl | собеседница по игре; говорит резко |")[0]
       == "| GstringGirl | | | собеседница по игре; говорит резко |",
       row("| GstringGirl | собеседница по игре; говорит резко |"))
    ok("«Перевод, справка» — надвое, звание строчными — перевод",
       row("| Mr. Gladly | мистер Гладли, учитель |", "NAMES")[0]
       == "| Mr. Gladly | мистер Гладли | | учитель |",
       row("| Mr. Gladly | мистер Гладли, учитель |", "NAMES"))
    ok("два написания через запятую — перевод целиком",
       row("| the States, U.S. | Штаты, США. |", "NAMES")[0]
       == "| the States, U.S. | Штаты, США | | |",
       row("| the States, U.S. | Штаты, США. |", "NAMES"))
    ok("«Перевод: справка» — надвое",
       row("| Gilpatrick | Гилпатрик: взрослый мужчина, офицер |")[0]
       == "| Gilpatrick | Гилпатрик | | взрослый мужчина, офицер |",
       row("| Gilpatrick | Гилпатрик: взрослый мужчина, офицер |"))
    ok("двоеточие в кавычках — часть написания",
       row("| Bay: Crime | «Бей: преступность» — трактат |", "NAMES")[0]
       == "| Bay: Crime | «Бей: преступность» | | трактат |",
       row("| Bay: Crime | «Бей: преступность» — трактат |", "NAMES"))
    ok("«Имя (Имя)» — одно имя",
       row("| Fern (Fern) | Ферн, подруга |")[0]
       == "| Fern | Ферн | | подруга |", row("| Fern (Fern) | Ферн, подруга |"))
    ok("термин со строчной — перевод",
       row("| cape | кейп — сверхчеловек |", "TERMS")[0]
       == "| cape | кейп | | сверхчеловек |",
       row("| cape | кейп — сверхчеловек |", "TERMS"))
    ok("«Оригинал (перевод)» у сносок — три ячейки",
       row("| Cliff notes («краткая выжимка») | реалия |", "FOOTNOTES")[0]
       == "| Cliff notes | «краткая выжимка» | реалия |",
       row("| Cliff notes («краткая выжимка») | реалия |", "FOOTNOTES"))
    ok("«Перевод / Оригинал» — по письменности",
       row("| Сыон / Scion | золотой |")[0] == "| Scion | Сыон | | золотой |",
       row("| Сыон / Scion | золотой |"))
    ok("несколько имён — через «;»",
       row("| Рой; Тейлор (Skitter; Taylor) | школьница |")[0]
       == "| Skitter; Taylor | Рой; Тейлор | | школьница |",
       row("| Рой; Тейлор (Skitter; Taylor) | школьница |"))
    ok("«Имя (Прозвище)» без перевода — оба оригиналы",
       row("| Taylor (Skitter) | школьница |")[0]
       == "| Taylor; Skitter | | | школьница |",
       row("| Taylor (Skitter) | школьница |"))
    ok("пара ADDRESS — имена через «;»",
       row("| Taylor — Danny | на «ты» |", "ADDRESS")[0]
       == "| Taylor; Danny | на «ты» |",
       row("| Taylor — Danny | на «ты» |", "ADDRESS"))
    dead = row("| Тейлор и Рейчел | на «ты» |", "ADDRESS")
    ok("строка без оригинала — мёртвая",
       dead == ("| Тейлор; Рейчел | на «ты» |", True, True), dead)
    old4 = "| Zoe | Зои | вымышленный | мать Эммы |"
    ok("старые четыре ячейки — справка в одну",
       row(old4, "NAMES", legacy=True)[0]
       == "| Zoe | Зои | | вымышленный; мать Эммы |",
       row(old4, "NAMES", legacy=True))
    ok("новые четыре ячейки не тронуты",
       row(old4, "NAMES") == (old4, False, False), row(old4, "NAMES"))
    same = row("| Faultline | Разлом | ж | руководительница |")
    ok("строка нового вида не тронута", same == (
        "| Faultline | Разлом | ж | руководительница |", False, False), same)
    ok("шапка и разделитель не тронуты",
       not row("| Оригинал | Карточка |")[1] and not row("|---|---|")[1])
    ok("без письменности — ключ целиком",
       R.canon_row("| Сыон / Scion | з |", "CHARACTERS", None)[0]
       == "| Сыон / Scion | | | з |",
       R.canon_row("| Сыон / Scion | з |", "CHARACTERS", None))

    new, n, dd = R.canon_ref(OLD, "ru", legacy=True)
    ok("переложены все строки реестра", n == 20 and dd == 2, (n, dd))
    ok("VOICES не тронут", "| Рассказчик | Тейлор, 1-е лицо |" in new, new)
    ok("шапка старой таблицы выброшена",
       "| Оригинал |" not in new and "|---|" not in new, new)
    ok("род влит в третью ячейку",
       "| Faultline | Разлом | ж, склоняется | руководительница наёмников |"
       in new, new)
    ok("свойства влиты в четвёртую, WORLD пропал",
       "| Brockton Bay | Броктон-Бей | | город; портовый город |" in new
       and "## WORLD" not in new, new)
    ok("шапка старой таблицы выброшена", "| оригинал |" not in new, new)
    ok("мёртвая строка GENDER остаётся в своём разделе",
       "## GENDER — Род и склонение\n\n| Земля-Алеф | средний |" in new, new)
    ok("род на несколько имён — каждому персонажу",
       "| Taylor | Тейлор | пол не сказан | школьница. |" in new
       and "| Clockpicker | Клокблокер | пол не сказан | остряк. |" in new, new)
    ok("род без сущности — строкой персонажа в хвосте CHARACTERS",
       "| Scion | Сыон | | золотой человек |\n"
       "| Vantage | Вэнтидж | мужчина, склоняется | |" in new, new)
    ok("свойства без сущности — строкой имени или термина",
       "| Zoe | Зои | | вымышленный; мать Эммы |\n"
       "| Hebert house | дом Эбертов | | старый дом с тонкими стенами |" in new
       and "\n| bullet ant | муравей-пуля | | ядовитый муравей |" in new
       and new.index("## TERMS") < new.index("| bullet ant |"), new)
    ok("старые четыре ячейки переложены",
       "| Zoe | Зои | | вымышленный; мать Эммы |" in new, new)
    ok("ключ первой ячейкой",
       "| Taylor; Danny | отец и дочь: на «ты» |" in new, new)
    ok("мёртвой паре оригинал по справочнику, чужое имя как было",
       "| Taylor; Рейчел | Тейлор; Рейчел | на «ты» |" in new, new)
    ok("раздел окончательного выбора выброшен",
       "ОКОНЧАТЕЛЬНЫЙ" not in new and "| Scion | Сыон |\n" not in new, new)
    ok("перекладка идемпотентна", R.canon_ref(new, "ru") == (new, 0, 2),
       R.canon_ref(new, "ru")[1:])
    kept = R.canon_ref(OLD, "ru")[0]
    ok("без legacy шапка и окончательный выбор целы",
       "|---|---|" in kept and "ОКОНЧАТЕЛЬНЫЙ" in kept, kept)

    # Конвертация папки — по её версии: старую перекладывает однократно с
    # копией, новую не трогает, без versions.json считает старой.
    d = tempfile.mkdtemp()
    said = []
    try:
        work = os.path.join(d, "old.work")
        os.makedirs(os.path.join(work, "ru"))
        sp = os.path.join(work, "ru", "scout.md")
        open(sp, "w", encoding="utf-8").write(OLD)
        json.dump({"last": {"pipeline": "1.10.8 abc"}},
                  open(os.path.join(work, "versions.json"), "w"))
        R.convert_ref(work, "ru", said.append)
        got = open(sp, encoding="utf-8").read()
        ok("старая папка переложена", got == new, got[:200])
        ok("копия старого справочника рядом",
           open(sp + ".bak", encoding="utf-8").read() == OLD)
        ok("в логе — сколько переложено и сколько мёртвых",
           len(said) == 2 and "20" in said[0] and "2" in said[1], said)
        said.clear()
        json.dump({"last": {"pipeline": R.REF_FORMAT + " abc"}},   # note_version
                  open(os.path.join(work, "versions.json"), "w"))
        R.convert_ref(work, "ru", said.append)
        ok("повторный вызов молчит", not said and
           open(sp, encoding="utf-8").read() == new, said)

        work2 = os.path.join(d, "new.work")
        os.makedirs(os.path.join(work2, "ru"))
        sp2 = os.path.join(work2, "ru", "scout.md")
        open(sp2, "w", encoding="utf-8").write(OLD)
        json.dump({"last": {"pipeline": R.REF_FORMAT + " abc"}},
                  open(os.path.join(work2, "versions.json"), "w"))
        R.convert_ref(work2, "ru", said.append)
        ok("папку новой версии не трогаем",
           open(sp2, encoding="utf-8").read() == OLD and not said, said)

        work3 = os.path.join(d, "bare.work")
        os.makedirs(os.path.join(work3, "ru"))
        sp3 = os.path.join(work3, "ru", "scout.md")
        open(sp3, "w", encoding="utf-8").write(OLD)
        R.convert_ref(work3, "ru", said.append)
        ok("без versions.json — старая",
           open(sp3, encoding="utf-8").read() == new, said)
        R.convert_ref(os.path.join(d, "none.work"), "ru", said.append)
        ok("папка без справочника — не падает", True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
