#!/usr/bin/env python3
"""Проверка выжимки справочника для куска.

Таблицы имён и терминов — большая часть справочника, а куску нужны из них
считаные строки. В систему уходит костяк без таблиц, к куску приезжают
строки, чьи ключи встречаются в его тексте. Здесь проверяется делёж и отбор:
потерянная строка — разнобой в имени, нефильтрованная таблица — сотни строк
в каждом из сотен запросов.

    python3 tests/refrows_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                         # noqa: E402

REF = """# META — Выходные данные

```
title = Resplendent
title_target = Великолепие
```

# CHARACTERS — Персонажи

**Poole.** Говорит коротко, к своим — на «ты».

# NAMES — Имена и названия

| Оригинал | Перевод | Пояснение |
|---|---|---|
| Michael Poole | Майкл Пул | инженер |
| Qax | каксы | народ-завоеватель |
| **Ancestors** / Toolmakers | Предки / Орудийщики | стадии одного вида |

# TERMS — Термины

| drone | трутень | низшая каста |

# RISK — Опасные места

Сцена допроса в главе 9: переводить сдержанно.
"""


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    frame, rows = P.split_ref(REF)
    ok("костяк без строк таблиц", "|" not in frame, frame)
    ok("проза и разделы в костяке целы",
       "Говорит коротко" in frame and "# RISK" in frame
       and "Сцена допроса" in frame, frame[:120])
    ok("выходные данные в костяке целы",
       "title_target = Великолепие" in frame, None)
    ok("строки собраны, шапки и линейки нет",
       len(rows) == 4 and not any("---" in k or "Оригинал" in k
                                  for k, _ in rows),
       [k for k, _ in rows])

    got = P.ref_rows_for(rows, "Michael Poole met the Qax envoy.")
    ok("строка с именем из куска попала",
       any("Майкл Пул" in l for l in got), got)
    ok("народ из куска попал", any("каксы" in l for l in got), got)
    ok("чужие строки не попали",
       not any("трутень" in l or "Орудийщики" in l for l in got), got)
    ok("шапка таблицы не попала",
       not any("Пояснение" in l for l in got), got)

    got = P.ref_rows_for(rows, "The Toolmakers built their nests.")
    ok("составной ключ ловится любой частью",
       any("Орудийщики" in l for l in got), got)
    got = P.ref_rows_for(rows, "A lone Drone crossed the hall.")
    ok("регистр отбору не мешает", any("трутень" in l for l in got), got)
    ok("часть слова — не ключ",
       P.ref_rows_for(rows, "Poolesville and qaxophone.") == [], None)

    # Двухъярусный делёж: карточки персонажей и кандидаты в сноски — тоже
    # реестр, они едут только в куски, где их слово встречается. Разделы
    # VOICES и RISK — костяк: рассказчик в своих главах говорит «я» и по
    # имени не назван, ключом его не поймать.
    TWO = """## VOICES — Повествование и слог

- **Рассказчик:** Тейлор, 1-е лицо, прошедшее время.

## CHARACTERS — Персонажи

Одна строка прозы о составе.

- **Клокблокер (Clockpicker):** остряк, речь быстрая.

## FOOTNOTES — Кандидаты в сноски

- **PHO (форум)** — автор объясняет сам дальше.

## RISK — Опасные места

- **суд** — сцену суда переводить сдержанно.
"""
    frame, rows = P.split_ref(TWO)
    ok("карточка персонажа ключуется",
       any("Клокблокер" in l for _, l in rows), rows)
    ok("карточка едет по оригинальному написанию",
       any("остряк" in l for l in P.ref_rows_for(rows, "Clockpicker grinned.")),
       None)
    ok("карточка едет и по целевому написанию",
       any("остряк" in l for l in P.ref_rows_for(rows, "Клокблокер ухмыльнулся.")),
       None)
    ok("кандидат в сноски ключуется",
       any("PHO" in l for _, l in rows)
       and any("PHO" in l for l in P.ref_rows_for(rows, "Она открыла PHO.")),
       rows)
    ok("маркер в VOICES остаётся в костяке", "Рассказчик" in frame, frame)
    ok("маркер в RISK остаётся в костяке", "сцену суда" in frame, frame)
    ok("шапка раздела из одних строк выпала из костяка",
       "## FOOTNOTES" not in frame and "## CHARACTERS" in frame, frame)
    # Скобочная альтернатива в ключе таблицы тоже распахивается: строка
    # «| Скиттер (Skitter) | … |» иначе не совпала бы ни с одним куском.
    trows = P.split_ref("## CHARACTERS — Персонажи\n\n"
                        "| Скиттер (Skitter) | вожак Неформалов |\n")[1]
    ok("скобки в ключе таблицы — составной ключ",
       any("вожак" in l for l in P.ref_rows_for(trows, "Skitter nodded.")),
       trows)

    # Швы запроса — из prompts, и выжимка встаёт между терминами и текстом.
    chunk = {"index": 3, "label": "", "blocks": [
        {"id": "b1", "kind": "p", "text": "Poole waited."}]}
    p = P.translate_prompt(chunk, None, "", "", [],
                           ["| Michael Poole | Майкл Пул |"], "задание")
    ok("выжимка в запросе перевода",
       "Строки справочника" in p and "Майкл Пул" in p, p[:160])
    ok("шов из prompts/translate_fragment.md",
       "## Фрагмент для перевода" in p, None)
    p = P.translate_prompt(chunk, None, "", "", [], [], "задание")
    ok("без строк секции нет", "Строки справочника" not in p, None)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
