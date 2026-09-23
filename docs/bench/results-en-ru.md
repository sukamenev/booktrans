# Результаты бенчмарка en → ru

Строки добавляются руками из отчёта прогона (раздел «Строка для таблицы
результатов»). Сравнивать можно только строки одной версии бенчмарка. Если одну
модель судили разные судьи, их прогоны складываются в одну строку: среднее и
разброс общие, в колонке judge перечислены все судьи. Области: acc — точность смысла, full — полнота, calq — кальки,
lex — словарь и идиомы, style — образность и стиль, verse — стихи и игра
слов, cons — согласованность, gend — пол и обращения, name — имена и
клички, norm — норма языка, note — сноски, reg — регистр и откровенность;
penalty — штрафы суммой. Методика — в [README.md](README.md).

## Бенчмарк качества перевода en → ru, версия 1.3 — три прогона, среднее

| date | translator | score | runs | attempts | acc | full | calq | lex | style | verse | cons | gend | name | norm | note | reg | penalty | booktrans | test | judge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-23 | codex:gpt-6-astra:medium | 90.0 | 3 (88–92) | 1/1/1 | 14 | 9 | 8 | 9 | 9 | 6 | 8 | 8 | 7 | 6 | 4 | 2 | 0 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | agy:gemini-3.8-flash:medium | 88.0 | 3 (82–91) | 1/1/1 | 15 | 9 | 10 | 9 | 9 | 8 | 7 | 8 | 6 | 5 | 4 | 2 | -1 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | codex:gpt-5.6-sol:medium | 86.7 | 3 (85–89) | 1/1/1 | 12 | 8 | 9 | 9 | 9 | 6 | 8 | 7 | 7 | 5 | 4 | 3 | -1 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | openai@router.bynara.id:glm-5.3:medium | 84.0 | 3 (78–88) | 2/2/2 | 14 | 9 | 11 | 9 | 10 | 7 | 8 | 7 | 5 | 4 | 4 | 2 | -4 | 1.10.80 | 1.3 | agy:claude-opus-4-6-thinking |
| 2026-09-23 | openai@router.bynara.id:muse-spark-1.3:medium | 84.0 | 3 (80–87) | 1/1/1 | 14 | 9 | 9 | 9 | 8 | 6 | 8 | 7 | 6 | 6 | 4 | 2 | -3 | 1.10.89 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | agy:gemini-3.1-pro:high | 83.0 | 3 (81–85) | 1/1/1 | 12 | 9 | 9 | 7 | 8 | 7 | 8 | 7 | 7 | 3 | 3 | 3 | 0 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | openai@router.bynara.id:muse-spark-1.3-contributor:medium | 82.7 | 3 (82–83) | 1/1/1 | 13 | 9 | 9 | 9 | 9 | 6 | 7 | 6 | 6 | 5 | 2 | 2 | 0 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | openai@router.bynara.id:deepseek-v4-flash:medium | 82.3 | 3 (78–88) | 1/3/2 | 13 | 9 | 8 | 9 | 10 | 7 | 8 | 7 | 5 | 5 | 4 | 3 | -7 | 1.10.80 | 1.3 | agy:claude-opus-4-6-thinking, openai@router.bynara.id:claude-opus-5:medium |
| 2026-09-23 | agy:gemini-3.7-flash:medium | 82.0 | 3 (79–86) | 1/2/2 | 15 | 9 | 9 | 9 | 9 | 6 | 7 | 7 | 6 | 4 | 4 | 2 | -6 | 1.10.78 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | codex:gpt-6-sol:medium | 81.7 | 3 (79–85) | 1/1/1 | 13 | 8 | 8 | 9 | 8 | 5 | 7 | 7 | 7 | 4 | 4 | 2 | -1 | 1.10.87 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | claude:claude-fable-5-1:medium | 79.0 | 3 (77–82) | 1/1/1 | 9 | 8 | 10 | 9 | 9 | 6 | 7 | 5 | 6 | 4 | 3 | 2 | 0 | 1.10.78 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:kimi-k2.7-code:medium | 78.6 | 5 (68–86) | 1/1/1/1/1 | 13 | 9 | 5 | 8 | 10 | 6 | 8 | 8 | 5 | 5 | 2 | 4 | -1 | 1.10.85 | 1.3 | codex:gpt-5.6-sol:medium, openai@router.bynara.id:claude-opus-5:medium |
| 2026-09-23 | codex:gpt-5.6-terra:medium | 78.3 | 3 (76–81) | 1/2/1 | 12 | 9 | 6 | 9 | 9 | 4 | 8 | 7 | 6 | 4 | 2 | 2 | 0 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | openai@router.bynara.id:deepseek-v4.1-flash:medium | 78.3 | 3 (71–85) | 2/1/1 | 13 | 9 | 7 | 9 | 10 | 5 | 8 | 7 | 6 | 2 | 4 | 2 | -3 | 1.10.78 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | claude:claude-opus-5-5:medium | 78.3 | 3 (76–81) | 1/1/1 | 9 | 8 | 9 | 9 | 10 | 6 | 6 | 5 | 7 | 4 | 3 | 2 | 0 | 1.10.78 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:hy4-preview:medium | 78.3 | 3 (74–81) | 1/1/1 | 13 | 9 | 7 | 9 | 10 | 6 | 8 | 5 | 4 | 4 | 3 | 3 | -1 | 1.10.85 | 1.3 | openai@router.bynara.id:claude-opus-5:medium |
| 2026-09-23 | openai@router.bynara.id:deepseek-v4-pro:medium | 77.7 | 3 (75–79) | 1/2/2 | 13 | 9 | 2 | 9 | 10 | 4 | 8 | 6 | 7 | 5 | 4 | 2 | 0 | 1.10.78 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | claude:claude-opus-5:medium | 76.7 | 3 (71–81) | 1/1/1 | 11 | 8 | 6 | 9 | 10 | 4 | 7 | 7 | 7 | 4 | 3 | 2 | 0 | 1.10.78 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | claude:claude-opus-4-8:medium | 75.7 | 3 (74–78) | 1/1/1 | 11 | 8 | 8 | 9 | 10 | 5 | 8 | 5 | 6 | 3 | 3 | 2 | -3 | 1.10.87 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | codex:gpt-6-luna:medium | 73.7 | 3 (73–74) | 1/1/1 | 13 | 9 | 7 | 9 | 8 | 3 | 8 | 4 | 5 | 3 | 4 | 2 | -1 | 1.10.87 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | codex:gpt-5.6-luna:medium | 72.7 | 3 (72–74) | 1/1/1 | 12 | 9 | 6 | 8 | 8 | 4 | 8 | 5 | 5 | 4 | 2 | 2 | -1 | 1.10.77 | 1.3 | claude:claude-opus-5-5:medium |
| 2026-09-23 | openai@opencode.ai:minimax-m3:medium | 72.3 | 3 (66–78) | 3/1/2 | 13 | 8 | 6 | 7 | 10 | 5 | 8 | 7 | 5 | 2 | 3 | 2 | -3 | 1.10.85 | 1.3 | agy:claude-opus-4-6-thinking, codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:qwen3.7-max:medium | 69.3 | 3 (68–70) | 1/1/1 | 12 | 8 | 4 | 6 | 9 | 4 | 8 | 5 | 7 | 2 | 3 | 2 | 0 | 1.10.85 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | claude:claude-sonnet-5:medium | 68.7 | 3 (62–77) | 1/1/1 | 9 | 8 | 6 | 8 | 9 | 4 | 6 | 4 | 6 | 3 | 3 | 2 | -1 | 1.10.78 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:mimo-v2.6-pro:medium | 68.7 | 3 (66–72) | 2/3/2 | 11 | 7 | 8 | 9 | 7 | 5 | 7 | 7 | 5 | 2 | 4 | 2 | -6 | 1.10.87 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:kimi-k3:medium | 68.2 | 5 (41–79) | 4/1/4/1/1 | 12 | 8 | 7 | 8 | 9 | 5 | 7 | 5 | 5 | 2 | 3 | 2 | -3 | 1.10.85 | 1.3 | agy:claude-opus-4-6-thinking, codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:qwen3.8-max:medium | 68.0 | 3 (67–69) | 1/1/1 | 10 | 8 | 5 | 8 | 9 | 5 | 7 | 4 | 5 | 3 | 3 | 2 | -1 | 1.10.87 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@router.bynara.id:qwen3.8-flash:medium | 66.0 | 3 (63–71) | 1/1/1 | 15 | 9 | 4 | 9 | 9 | 4 | 6 | 6 | 3 | 1 | 2 | 3 | -7 | 1.10.85 | 1.3 | openai@router.bynara.id:claude-opus-5:medium |
| 2026-09-23 | openai@opencode.ai:omen-alpha:medium | 66.0 | 2 (65–67) | 5/4 | 12 | 9 | 4 | 8 | 10 | 6 | 8 | 5 | 7 | 3 | 3 | 1 | -9 | 1.10.88 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@router.bynara.id:step-5-preview:medium | 64.7 | 3 (57–70) | 1/1/1 | 13 | 9 | 2 | 7 | 10 | 5 | 6 | 4 | 5 | 3 | 3 | 2 | -2 | 1.10.87 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:longcat-2.0:medium | 60.7 | 3 (55–66) | 1/1/4 | 13 | 8 | 8 | 6 | 9 | 3 | 7 | 5 | 4 | 2 | 3 | 2 | -9 | 1.10.85 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | claude:claude-haiku-4-5:medium | 54.3 | 3 (52–56) | 1/2/1 | 14 | 9 | 4 | 6 | 9 | 6 | 7 | 5 | 1 | 2 | 2 | 2 | -12 | 1.10.80 | 1.3 | agy:claude-opus-4-6-thinking, codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:glm-5.3-flash:medium | 50.0 | 3 (0–77) | 1/1/5 | 12 | 9 | 7 | 5 | 10 | 5 | 7 | 4 | 6 | 3 | 3 | 2 | 0 | 1.10.88 | 1.3 | codex:gpt-5.6-sol:medium, — |
| 2026-09-23 | openai@opencode.ai:mimo-v2.6-flash:medium | 48.0 | 3 (45–50) | 2/2/2 | 11 | 6 | 6 | 3 | 8 | 2 | 7 | 5 | 3 | 1 | 2 | 3 | -8 | 1.10.87 | 1.3 | codex:gpt-5.6-sol:medium |
| 2026-09-23 | openai@opencode.ai:mimo-v2.5-pro:medium | 22.0 | 3 (0–66) | 5/3/5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1.10.88 | 1.3 | codex:gpt-5.6-sol:medium, — |

## Бенчмарк качества перевода en → ru, версия 1.1 — один прогон

| date | translator | score | runs | attempts | acc | full | calq | lex | style | verse | cons | gend | name | norm | note | reg | penalty | booktrans | test | judge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-23 | codex:gpt-6-astra:medium | 95.0 | 1 | 1 | 15 | 9 | 9 | 9 | 10 | 7 | 8 | 8 | 7 | 6 | 4 | 3 | 0 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | openai@router.bynara.id:deepseek-v4.1-flash:high | 89.0 | 1 | 1 | 15 | 9 | 8 | 9 | 10 | 7 | 8 | 8 | 7 | 5 | 4 | 2 | -3 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-6-astra:high | 88.0 | 1 | 1 | 15 | 9 | 9 | 9 | 10 | 7 | 8 | 8 | 7 | 5 | 4 | 3 | -6 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | openai@router.bynara.id:muse-spark-1.3-contributor:medium | 87.0 | 1 | 1 | 14 | 9 | 9 | 9 | 11 | 7 | 8 | 8 | 5 | 4 | 4 | 2 | -3 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | agy:gemini-3.1-pro:high | 86.0 | 1 | 1 | 14 | 9 | 9 | 9 | 10 | 8 | 7 | 7 | 7 | 3 | 4 | 2 | -3 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-luna:high | 85.0 | 1 | 1 | 13 | 9 | 8 | 8 | 10 | 7 | 8 | 5 | 7 | 4 | 4 | 2 | 0 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-terra:medium | 84.0 | 1 | 1 | 14 | 9 | 8 | 9 | 10 | 5 | 8 | 7 | 6 | 5 | 4 | 2 | -3 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-opus-5-5:high | 84.0 | 1 | 1 | 11 | 8 | 7 | 9 | 10 | 7 | 8 | 7 | 6 | 5 | 3 | 3 | 0 | 1.10.77 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | agy:gemini-3.7-flash:high | 83.0 | 1 | 1 | 15 | 9 | 10 | 9 | 10 | 7 | 8 | 8 | 6 | 4 | 4 | 2 | -9 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-terra:high | 83.0 | 1 | 1 | 14 | 9 | 9 | 8 | 10 | 6 | 8 | 8 | 7 | 4 | 4 | 2 | -6 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-sol:medium | 82.0 | 1 | 1 | 13 | 8 | 11 | 9 | 10 | 5 | 8 | 6 | 7 | 4 | 4 | 3 | -6 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | agy:gemini-3.8-flash:medium | 82.0 | 1 | 1 | 14 | 9 | 9 | 9 | 10 | 8 | 7 | 8 | 6 | 4 | 4 | 3 | -9 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-sol:high | 81.0 | 1 | 1 | 13 | 9 | 10 | 9 | 10 | 7 | 8 | 8 | 7 | 3 | 4 | 2 | -9 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-opus-5:medium | 79.0 | 1 | 1 | 13 | 9 | 6 | 9 | 9 | 5 | 7 | 7 | 7 | 5 | 3 | 2 | -3 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-opus-5-5:medium | 79.0 | 1 | 1 | 11 | 8 | 8 | 9 | 10 | 6 | 6 | 5 | 6 | 5 | 3 | 2 | 0 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-fable-5-1:high | 78.0 | 1 | 1 | 12 | 8 | 9 | 7 | 10 | 6 | 8 | 6 | 7 | 3 | 3 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-opus-5:high | 76.0 | 1 | 1 | 13 | 8 | 8 | 8 | 10 | 7 | 7 | 4 | 7 | 3 | 2 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | openai@router.bynara.id:deepseek-v4-pro:high | 76.0 | 1 | 1 | 13 | 9 | 9 | 9 | 10 | 7 | 7 | 5 | 6 | 3 | 1 | 2 | -5 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-opus-4-8:medium | 76.0 | 1 | 1 | 11 | 9 | 7 | 9 | 9 | 6 | 7 | 4 | 6 | 3 | 3 | 2 | 0 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | agy:gemini-3.8-flash:high | 75.0 | 1 | 1 | 14 | 9 | 9 | 9 | 10 | 6 | 7 | 8 | 6 | 4 | 3 | 2 | -12 | 1.10.74 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-opus-4-8:high | 75.0 | 1 | 1 | 12 | 8 | 5 | 9 | 10 | 5 | 6 | 5 | 5 | 5 | 3 | 2 | 0 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | agy:gemini-3.7-flash:medium | 75.0 | 1 | 1 | 14 | 9 | 10 | 9 | 10 | 7 | 6 | 8 | 6 | 3 | 3 | 2 | -12 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-fable-5-1:medium | 74.0 | 1 | 1 | 11 | 9 | 8 | 9 | 10 | 6 | 6 | 5 | 5 | 3 | 3 | 2 | -3 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-sonnet-5:high | 70.0 | 1 | 1 | 10 | 9 | 4 | 8 | 9 | 4 | 7 | 7 | 7 | 3 | 3 | 2 | -3 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | openai@router.bynara.id:muse-spark-1.3-contributor:high | 66.0 | 1 | 1 | 14 | 9 | 9 | 8 | 10 | 6 | 8 | 4 | 5 | 4 | 2 | 2 | -15 | 1.10.75 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | codex:gpt-5.6-luna:medium | 66.0 | 1 | 1 | 13 | 9 | 9 | 8 | 9 | 6 | 8 | 5 | 5 | 3 | 3 | 2 | -14 | 1.10.76 | 1.1 | claude:claude-opus-5:high |
| 2026-09-23 | claude:claude-sonnet-5:medium | 54.0 | 1 | 1 | 9 | 9 | 7 | 8 | 8 | 4 | 8 | 4 | 7 | 3 | 3 | 2 | -18 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-haiku-4-5:medium | 47.0 | 1 | 1 | 10 | 9 | 1 | 5 | 9 | 3 | 8 | 5 | 2 | 1 | 1 | 2 | -9 | 1.10.76 | 1.1 | codex:gpt-5.6-sol:high |
| 2026-09-23 | claude:claude-haiku-4-5:high | 39.0 | 1 | 1 | 13 | 7 | 1 | 5 | 6 | 3 | 8 | 7 | 3 | 1 | 4 | 1 | -20 | 1.10.75 | 1.1 | codex:gpt-5.6-sol:high |
