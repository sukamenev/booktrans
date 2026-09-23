# Результаты бенчмарка en → ru

Строки добавляются руками из отчёта прогона (раздел «Строка для таблицы
результатов»). Сравнивать можно только строки с одной версией теста и одним
судьёй. Области: acc — точность смысла, full — полнота, calq — кальки,
lex — словарь и идиомы, style — образность и стиль, verse — стихи и игра
слов, cons — согласованность, gend — пол и обращения, name — имена и
клички, norm — норма языка, note — сноски, reg — регистр и откровенность;
penalty — штрафы суммой. Методика — в [README.md](README.md).

| date | translator | score | acc | full | calq | lex | style | verse | cons | gend | name | norm | note | reg | penalty | booktrans | test | judge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-23 | codex:gpt-6-astra:high | 88.0 | 15 | 9 | 9 | 9 | 10 | 7 | 8 | 8 | 7 | 5 | 4 | 3 | -6 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | agy:gemini-3.1-pro:high | 86.0 | 14 | 9 | 9 | 9 | 10 | 8 | 7 | 7 | 7 | 3 | 4 | 2 | -3 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-luna:high | 85.0 | 13 | 9 | 8 | 8 | 10 | 7 | 8 | 5 | 7 | 4 | 4 | 2 | 0 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | agy:gemini-3.7-flash:high | 83.0 | 15 | 9 | 10 | 9 | 10 | 7 | 8 | 8 | 6 | 4 | 4 | 2 | -9 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-terra:high | 83.0 | 14 | 9 | 9 | 8 | 10 | 6 | 8 | 8 | 7 | 4 | 4 | 2 | -6 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-sol:high | 81.0 | 13 | 9 | 10 | 9 | 10 | 7 | 8 | 8 | 7 | 3 | 4 | 2 | -9 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-fable-5-1:high | 78.0 | 12 | 8 | 9 | 7 | 10 | 6 | 8 | 6 | 7 | 3 | 3 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-opus-5:high | 76.0 | 13 | 8 | 8 | 8 | 10 | 7 | 7 | 4 | 7 | 3 | 2 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | agy:gemini-3.8-flash:high | 75.0 | 14 | 9 | 9 | 9 | 10 | 6 | 7 | 8 | 6 | 4 | 3 | 2 | -12 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-opus-4-8:high | 75.0 | 12 | 8 | 5 | 9 | 10 | 5 | 6 | 5 | 5 | 5 | 3 | 2 | 0 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-sonnet-5:high | 70.0 | 10 | 9 | 4 | 8 | 9 | 4 | 7 | 7 | 7 | 3 | 3 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | openai:muse-spark-1.3-contributor:high | 66.0 | 14 | 9 | 9 | 8 | 10 | 6 | 8 | 4 | 5 | 4 | 2 | 2 | -15 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-haiku-4-5:high | 39.0 | 13 | 7 | 1 | 5 | 6 | 3 | 8 | 7 | 3 | 1 | 4 | 1 | -20 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
