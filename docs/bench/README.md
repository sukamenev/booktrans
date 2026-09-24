# Translation benchmark

One model translates one text with planted traps, a judge with an answer key
rules on each of them, and code computes a score from 0 to 100.

    booktrans --bench --to ru --translator claude:claude-sonnet-5:medium

No book is needed: the text ships in the package. Required: the target
language (`--to`) and the translator (`--translator`). The default judge is
`codex:gpt-6-sol:medium`, the backup `claude:claude-opus-5-5:medium`;
another is set with `--judge`. The text is translated and judged three times
(`--bench-runs`) and the mean is reported. A run in which the model itself
produced no translation (hit the output limit, kept cutting the reply off,
answered malformed) counts as zero; a run broken by the router or a rate
limit does not count and is redone on a repeated launch into the same
directory.

## What is measured

Only the translator, through the regular pipeline pass: the same prompts,
reply parsing and chunk file as for a real book. No scouting — its reference
would be a cheat sheet for names and gender. No editor or verifier.

## The text

A story of 66 paragraphs and a four-line epigraph, about 3,100 words, one
pipeline chunk. Based on O. Henry's "The Skylight Room" (1906, public
domain): moved to the present day, dated phrasing replaced, title and names
changed, ending rewritten.

The text carries 107 planted traps in thirteen areas. A trap's weight: 4 —
the reader is misled (meaning, a character's gender, verse); 2 — the reader
stumbles (a calque, register, a name, language norm, adaptation,
vocabulary); 1 — a connoisseur notices (imagery, consistency, a footnote,
completeness). The key's maximum is 250 points; the score is normalised to
100.

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

Penalties, in the same points (per case / cap): additions — a word or
phrase −2, a change to what happens −8 / −32; omissions — a word or phrase
−2, a sentence and more −8 / −126; an untranslated fragment −4 / −20; a
footnote on a subject outside the key's whitelist −2 / −12; broken structure
— a lost paragraph, mismatched tags −8 / −38; a foreign script −4 per block /
−20; extra requests to get a well-formed reply −8 each / −126. One flaw is
punished once. The last three are counted by code, the rest by the judge.

The swearing in the text is moderate, the nudity has no sexual scenes.

## The judge

The judge receives the source and the translation block by block, the
translator's footnotes, the target-language rules and the key. On each of
the 107 checks it answers `ok` or `fail` with a reason; beyond the key it
marks additions, omissions, untranslated fragments and needless footnotes.
Code computes the score:

    area score = sum of the points of the passed checks
    raw total  = sum of the areas − penalties
    result     = 100 × max(0, raw total) / key maximum

A judge of the translator's own family is allowed; `--self-edit never`
strikes it out, `--self-edit last` puts it last in the chain. The judge is
shown in the judge column of the results table.

Judges disagree: the same judge on a repeat of the same translation changes
4–7 verdicts in a hundred, two different judges differ on 10–16 of 107.
Opus 5.5 scores the same translations 8 points higher on average than
GPT-6 Sol (3 to 15), passing calques and softened register. Scores of
different judges are not comparable.

`--judge none` only translates; judge later with the same command plus a
judge and `-w` into the same directory.

A local model connects like a router, over the OpenAI protocol:

```bash
export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama
export OPENAI_API_KEY=local
booktrans --bench --to ru --translator openai:qwen3.8:27b:medium --judge none
```

The server's context window must be at least 64k tokens. For models over
the network the report records the endpoint:
`openai@router.bynara.id:glm-5.3:medium`; one model at two routers is two
different measurements.

## What comes out

A work directory `benchmark-en-ru-<provider-model-effort>-<date_time>.work`
with a directory per run (`run1`…) holding the translation
`ru/tr/0001.json`, the verdicts `ru/bench.json` and the log `bench.log`; a
summary `bench.json` at the root. Next to it a report
`benchmark-en-ru-<…>.md`: the first line is the score, then versions,
translator, judge, a table of runs, areas, failed checks with reasons,
penalties and a ready row for the results table. The report's language is
`--ui`.

The benchmark version is in the key and in the report, three numbers X.Y.Z:
Z — a wording clarified, verdicts unchanged; Y — text or checks changed, new
runs needed; X — the scoring changed. Only results of one version and one
judge are comparable. Results: [results-en-ru.md](results-en-ru.md); a row
is added by hand from the report.

## Check statistics

```bash
booktrans --bench-stats              # the built-in key's version
booktrans --bench-stats 1.4.0,1.4.1  # several versions
booktrans --bench-stats 1.4.         # the whole 1.4 branch
```

Over the finished runs `benchmark-*.work` in the current directory (or in
`-w`): the share of models and the share of runs that passed each check, all
models weighing the same. Checks that 95 % of models or more pass are marked
◆ — candidates for replacement in the next version of the key.

## Your own set

`--bench DIR` takes the set from a directory with `text.fb2` and `key.txt`.
Key format:

```
booktrans-bench-key 1.4.0
source en
title Title

[areas]        # code and name; an area's maximum is the sum of its checks' weights
acc Accuracy of meaning

[checks]       # id  area  weight  blocks :: requirement and fail criterion
A03 acc 4 b0037 :: what is checked and what counts as a fail

[penalties]    # code  who counts  per case  cap :: what counts as one case
ADD-minor  judge  2  32 :: a word or phrase with no counterpart in the source …
ADD-major  judge  8  32 :: an addition that changes what happens or who says what
OMIT-major judge  8 126 :: a sentence or more lost …
FOOT       judge  2  12 :: a translator's footnote on a subject outside [notes] …
STRUCT     code   8  38 :: a block lost or added, or markup tags that do not match
RETRY      code   8 126 :: an extra request the pipeline needed …

[notes]        # subjects a translator's footnote may cover
Momus, the Greek god of mockery
```

Block ids are those of the fb2 parse (`b0008`, range `b0008-b0072`).
Penalties marked `judge` go into the judge's prompt from these lines; those
marked `code` are counted by the program. The key is written in English; the
judge carries the Russian examples over to another language by sense. The
number of checks is bounded by the judge: GPT-6 Sol and Opus 5.5 hold up to
200 checks in one request without losing verdict quality. The rule for a
trap: one check — one decision in one or two neighbouring blocks; a check
over the whole text only for consistency of names, gender and spelling. Pack
a set: `bench.pack(dir, en.bin)`.

The built-in set is stored compressed in the package (`bench/en.bin`) so
that the answer key does not end up in training data; documentation and
tests do not quote the text.
