# Test corpus

*[Русская версия](README.ru.md)*

Twelve books: eleven of two chapters each, about 19,000 words in total —
roughly a fifth of a novel. Two chapters are the point: with a single chapter
the pipeline never has to carry a plot digest from one chunk to the next, so
that part of the machinery goes untested.

The twelfth is a whole publisher's pdf, 634 pages, and it is whole on purpose.
Everything the pdf path does lives at the scale of a book: the back matter is
recognised by sitting at the end, running heads by repeating across pages, the
index by its hyperlinked numbers. A couple of chapters cut out of it would test
none of that. It costs about fifty seconds of the check, of which forty-five go
on decoding its 84 pictures.

The corpus is permanent. A new version of the pipeline is run against these
same files rather than against freshly cut ones: only like-for-like
comparisons mean anything.

## Using it

**Free, after any change to extraction:**

```bash
python3 tests/check.py
```

Reads every book and compares it against `manifest.json`: language, paragraph
count, verse lines, headings, footnotes, links, images, cover. Then it runs
every `*_check.py` beside it — one per rule of the pipeline. Their list is
written nowhere; they are simply picked up from this folder, so a new check
needs no registering, and a check nobody remembers to run is a check that does
not exist. No model requests at all; the whole thing finishes in seconds.

To check the books alone, without the rules: `python3 tests/check.py --books`. A mismatch means one of two things —
either extraction has broken, or extraction has improved. Only a human can
tell which, so the manifest is never updated on its own. Once you have
satisfied yourself that things got better:

```bash
python3 tests/check.py --update
```

**A full run, which does cost money:**

```bash
./booktrans tests/corpus/10_paper_en.pdf --to ru -o /tmp/out/01.fb2
```

The language each book should be translated into is recorded in the manifest
under `переводить_на`: several of the books are Russian and exist to exercise
the reverse direction.

Books are not in the repository, and neither is the inventory: the file names
name copyrighted books. Build the corpus with `build_corpus.py`, then create
the inventory with

```bash
python3 tests/check.py --update
```

It writes down what was read today, and from then on guards against
discrepancies. The shape of the file is in `manifest.json.example`.

## Provenance

Books 05 and 06 come from Project Gutenberg and are in the public domain.
Book 04 was written for this corpus. The rest are two-chapter extracts from
books in the owner's library; they are here as specimens of layout, and must
not be distributed with the pipeline.
