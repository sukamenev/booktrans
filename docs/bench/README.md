# Translation benchmark

One model, one text laden with traps, a judge with an answer key, and a
score from 0 to 100 computed by code. It answers the question "how well does
this model translate literary prose from English into language X" — so that
two runs of one model give close numbers and the numbers of different models
can be put side by side.

    booktrans --bench --to ru --translator claude:claude-sonnet-5:medium

No book is needed: the text ships inside the package. Only the target
language (`--to`) and the translator (`--translator`; without it, the
agent's default model) are required. The default judge is
`codex:gpt-6-sol:medium`, the backup `claude:claude-opus-5-5:medium`; name
another with `--judge`. The text is translated and judged **three times**
(`--bench-runs`) and the mean goes into the report: two translations of the
same text by a mid-range model differ by 10–20 points, by a strong one by
3–7, and a single run does not rank them. The mean rather than the median: a
book of thirty chunks comes out average in quality, and its failed chapter
stays in it, whereas the median of three runs throws the worst one away —
together with a zero for a model that broke down.

A run in which the model never produced a translation counts as zero if the
model itself broke: every attempt hit the output limit (it looped), cut the
reply off at the same place, or came back malformed. If even one attempt was
broken by the connection — the router dropped the stream, returned a service
error or a rate limit — the run does not count: not the model's fault. A
repeated launch into the same directory redoes such a run and keeps the zero.

## What is measured

Only the translator. There is no scouting on purpose: its reference fixes
the decisions on names, gender and terms — exactly what the test checks —
and a ready reference would be a cheat sheet. No editor or verifier either:
the pipeline's editing is blind (without the source) and deserves a benchmark
of its own. The translation goes through the regular pipeline pass — the
same prompts, the same reply parsing, the same chunk file as for a real book
— so the result describes the model under real pipeline conditions, not in a
chat.

## The text

A story of 66 paragraphs and a four-line epigraph, about 3,100 words —
exactly one pipeline chunk. Based on O. Henry's "The Skylight Room" (1906,
public domain), reworked: moved to the present day, dated phrasing replaced,
title and names changed, ending rewritten. The plot is kept — it is a whole
story, not a set of sentences.

The text carries 107 planted traps in thirteen areas. Each has a weight: 4 —
the reader is misled (meaning, a character's gender, verse), 2 — the reader
stumbles (a calque, register, a name, language norm, adaptation,
vocabulary), 1 — a connoisseur notices (imagery, consistency, a footnote,
completeness). The key's maximum is 250 points; the score is normalised to
100. Every trap is described in the judge's key: where it is, what counts as
correct and what as a fail, with examples.

| Area | Points | What is checked |
|---|---|---|
| Accuracy of meaning | 68 | distortions, false friends, habitual "would", "couldn't care less", "hardly", "the former / the latter", numbers and hedges |
| Completeness | 4 | dense enumerations, small qualifiers ("a little more", "roughly") |
| Freedom from calques | 30 | "are you okay?", "makes sense", passives, "there was talk of", "why, there's…", "a beat late", pronoun overload |
| Vocabulary and idioms | 20 | rare bookish words, astronomical and medical terms, phrasal verbs, idioms by sense |
| Imagery and style | 10 | images without pile-up, the rhythm of epithets, "and… and… and" chains, a series of ironic parentheses, the echo of the title |
| Verse and wordplay | 28 | the quatrain in metre and rhyme, puns, "gamma — alpha" as a school mark |
| Consistency | 6 | the repeated line, the term, nicknames, the echo of epithets, three senses of "fair" |
| Gender and address | 24 | a character's gender revealed after her first lines; agreement with the person, not with the gender of a nickname; formal/informal "you" kept |
| Names and nicknames | 14 | transliteration by sound (Cholmondeley, Siobhan, Leigh, Beauchamp, Waugh, Worcester), an initial matching the full name, nicknames by sense |
| Target-language norm and punctuation | 14 | dialogue layout, italics, spelling, commas at subordinate clauses and phrases, introductory words, numbers in dialogue as words |
| Adaptation | 20 | feet, pounds, °F, quarts — with the author's precision; idioms not converted; money and coins; floors; school and college; time and date; forms of address and the street address |
| Footnotes | 4 | present for mythology, Labor Day and New York realia |
| Register and frankness | 8 | swearing not softened, bodily details not dropped |

Penalties beyond the key, in the same points: additions — a fact, action or
judgement absent from the source (−2 per phrase, −8 if it changes what
happens, cap −32; an amplified epithet or one word unpacked into two is not
an addition); omissions — a lost word or phrase with a sense of its own (−2)
or a sentence and more (−8), cap −126, half the maximum: a translation with
half the text thrown out must not score near a full one; an untranslated
fragment (−4, cap −20); a needless footnote — on a subject outside the key's
whitelist (−2, cap −12); broken structure — a lost paragraph, mismatched
tags (−8, cap −38); a foreign script — letters of neither the source nor the
target language (−4 per block, cap −20); extra attempts — every repeated
request the pipeline needed to get a well-formed reply (−8, cap −126). One
flaw is punished once: what already failed a check is not penalised again.
The last three penalties are counted by code, not by the judge.

The swearing in the text is moderate and the nudity has no sexual scenes: the
test measures translation, not a model's willingness to translate. A set with
hard content, to test censorship, is planned separately.

## The judge

The judge receives the source and the translation block by block, the
translator's footnotes, the target-language rules (the same ones the
translator got) and the key. On each of the 107 checks it answers `ok` or
`fail` with a reason; beyond the key it marks additions, omissions,
untranslated fragments and needless footnotes. It gives no score — the code
does:

    area score = sum of the points of the passed checks
    raw total  = sum of the areas − penalties
    result     = 100 × max(0, raw total) / key maximum

A judge of the translator's own family is allowed: in the 1.4.0 validation
self-judging did not flatter — GPT-6 Sol gave its own translation less than
Opus gave it — while striking it out would hand a whole family of models to
the backup judge with a different scale (Opus is 5–10 points more generous
than GPT-6 Sol across all models). One judge for everyone matters more.
`--self-edit never` restores the ban, `--self-edit last` puts the same-family
judge last in the chain; the judge column always shows who judged.

`--judge none` only translates: when the judges' limits are exhausted, the
translations can be made in advance and judged later with the same command
plus a judge and `-w` into the same work directory — ready translations are
not paid for twice.

A local model connects like a router: llama.cpp, Ollama, LM Studio and vLLM
speak the OpenAI protocol.

```bash
export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama
export OPENAI_API_KEY=local
booktrans --bench --to ru --translator openai:qwen3.8:27b:medium --judge none
```

A colon inside an Ollama model name does not confuse the parser: the last
word is the effort, the first the agent, everything between is the model.
The server's context window must be at least 64k tokens (Ollama:
`OLLAMA_CONTEXT_LENGTH=65536`, llama.cpp: `-c 65536`): the chunk with its
prompt takes about 20k, and as much again goes to the translation with its
reasoning.

For models reached over the network (`openai`, `openrouter`) the report and
the table also record the endpoint: `openai@router.bynara.id:glm-5.3:medium`.
One model at two routers is two different measurements: a router is not
bound to serve the model it was asked for.

The judge's reply is lines of a strict form; a check without a verdict sends
the request back with the missing ones listed. One request covers all the
checks; if a judge model cannot manage in one reply, the retry asks only for
what is missing.

## What comes out

A work directory `benchmark-en-ru-<provider-model-effort>-<date_time>.work`
in the current directory, with a directory per run (`run1`, `run2`, `run3`)
laid out like any pipeline run: `book.json`, `ru/tr/0001.json` with the
translation (worth reading with your own eyes — the score flatters a cautious
translator and penalises a bold one), `ru/bench.json` with the verdicts and
`bench.log` with the run's progress; the runs go in parallel. At the root a
summary `bench.json`. Next to it a report `benchmark-en-ru-<…>.md`: the first
line is the mean, then the test and pipeline versions, the translator's
provider, model and effort, the judge, a table of runs (score, penalties,
fails, attempts, money, minutes), then the areas, the failed checks with
reasons and the penalties of the run nearest the mean, and a ready row for
the results table. The report's language is `--ui`.

Only results of one benchmark version and one judge are comparable. The
benchmark version is in the key (`booktrans-bench-key 1.4.0`) and in the
report, three numbers X.Y.Z. Z grows when a wording is clarified and the
verdicts do not change: results stay comparable. Y — when the text or the
checks changed: new runs are needed. X — when the scoring itself changes:
areas, scale, penalties. The judge is not infallible either: the same judge
on a repeat of the same translation disagrees with itself on 4 checks in a
hundred (Opus 5.5) or 7 (Sol 5.6), and two different judges on 10–16 checks
of 107. A difference of two or three points between models therefore means
nothing, and the scores of different judges are not comparable: Opus 5.5 is
more generous than GPT-6 Sol by 8 points on average (3 to 15), because it
passes calques and softened register that the key forbids; GPT-6 Sol is the
default as the judge that follows the key most closely. Measurements are
collected in [results-en-ru.md](results-en-ru.md); a row is added by hand
from the report.

## Check statistics

```bash
booktrans --bench-stats              # the built-in key's version
booktrans --bench-stats 1.4.0,1.4.1  # several versions at once
booktrans --bench-stats 1.4.         # the whole 1.4 branch: 1.4.0, 1.4.1…
```

Walks the finished runs `benchmark-*.work` in the current directory (or in
`-w`) and prints, for every check, the share of models and the share of runs
that passed it. A model is one translator with its effort and endpoint; all
models weigh the same, so a series of five runs does not outweigh a series
of three. A series summary repeats the verdicts of one of its runs and is not
counted, nor is a run where the model broke. The version is matched exactly;
a trailing dot takes the whole branch: `1.4.` is every 1.4.N (the third
number changes only wordings, the verdicts are the same). No asterisk: zsh
fails on `1.4.*` when no file matches, and no shell touches a dot. Checks
that 95 % of models or more pass are marked ◆ — they hardly separate anyone
and should be replaced in the next version of the key.

## Your own set

`--bench DIR` takes the set from a directory with `text.fb2` and `key.txt`
instead of the built-in one. Key format:

```
booktrans-bench-key 1.4.0
source en
title Title

[areas]        # code and name; an area's maximum is the sum of its checks' weights
acc Accuracy of meaning
…

[checks]       # id  area  weight  blocks :: requirement and fail criterion
A03 acc 4 b0037 :: what is checked and what counts as a fail
…

[penalties]    # code  who counts  per case  cap :: what counts as one case
ADD-minor  judge  2  32 :: a word or phrase with no counterpart in the source …
ADD-major  judge  8  32 :: an addition that changes what happens or who says what
UNTR       judge  4  20 :: a source-language fragment left untranslated …
OMIT-major judge  8 126 :: a sentence or more lost …
FOOT       judge  2  12 :: a translator's footnote on a subject outside [notes] …
STRUCT     code   8  38 :: a block lost or added, or markup tags that do not match
SCRIPT     code   4  20 :: a block with characters of a foreign script
RETRY      code   8 126 :: an extra request the pipeline needed …

[notes]        # subjects a translator's footnote may cover
Momus, the Greek god of mockery
…
```

A check's weight is how many points it earns; a penalty's price and cap are
in the same points, so a penalty reads against a check: "ADD-major 8 — two
failed checks of weight 4". The key's maximum is the sum of the weights; the
result is what was earned minus the penalties, normalised to 100; the report
shows penalties both in points and in final-score terms. Penalties marked
`judge` go into the judge's prompt straight from these lines; those marked
`code` are counted by the program, and the judge never hears of them.

Block ids are those of the fb2 parse (`b0008`, range `b0008-b0072`); a check
on a block the text does not have will not pack. The total of points need
not be round: the score is normalised to 100. What bounds the number of
checks is the judge — it holds the whole key and the whole text in one
request, and how many checks it bears without losing verdict quality has to
be measured on every new judge. Opus 5.5 and Sol 5.6 on keys of 50, 100, 150
and 200 checks answer equally steadily: the disagreement with the hundred is
the same as between two repeats of one hundred (4 checks for Opus, 7 for
Sol), and no verdicts go missing. The key is written in English and does not
depend on the target language: the judge carries the Russian examples over
to another language by sense. Pack a set into one package file:
`bench.pack(dir, en.bin)`.

The omission penalty `OMIT` (minor — a lost word or phrase with a sense of
its own, major — a sentence and more) is looked for by the judge only when
the key declares it: then the rule on omissions goes into its prompt. The cap
on omissions must be high: a translation with half the text thrown out
cannot score near a full one. The needless-footnote penalty `FOOT` works
from a whitelist: the `[notes]` section lists the subjects a translator's
footnote may cover, and the judge penalises a footnote on any other subject.
A whitelist rather than a blacklist, because "obvious" differs from reader
to reader: one knows Falstaff, another does not, while a list of needed and
tolerated subjects raises no dispute. A penalty the key does not declare does
not count.

The rule for writing checks: **one check — one decision in one or two
neighbouring blocks**. A check over the whole text only where the thing is
global by nature: consistency of names, gender, spelling. Easy checks are
not merged into one to save points: a judge asked to visit five places in
six paragraphs checks the first ones and says "passed", and the statistics
stop showing what exactly failed. An easy check — one that almost every
model passes (`--bench-stats`) — is replaced by a hard one, and completeness
as a whole is kept by the `OMIT` penalty, not by a trap on every sentence.

The built-in set is stored compressed in the package (`bench/en.bin`): the
repository is public, and an open answer key would sooner or later end up in
the training data of the very models it measures. That is why neither the
documentation nor the tests quote the text, and the reports carry only block
addresses and short quotations from the translation.
