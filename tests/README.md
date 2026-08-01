# Test corpus

*[Русская версия](README.ru.md)*

Eleven books, two chapters each, about 19,000 words in total — roughly a fifth
of a novel. Two chapters are the point: with a single chapter the pipeline
never has to carry a plot digest from one chunk to the next, so that part of
the machinery goes untested.

The corpus is permanent. A new version of the pipeline is run against these
same files rather than against freshly cut ones: only like-for-like
comparisons mean anything.

## Using it

**Free, after any change to extraction:**

```bash
python3 tests/check.py
```

Reads every book and compares it against `manifest.json`: language, paragraph
count, verse lines, headings, footnotes, links, images, cover. No model
requests at all; it finishes in seconds. A mismatch means one of two things —
either extraction has broken, or extraction has improved. Only a human can
tell which, so the manifest is never updated on its own. Once you have
satisfied yourself that things got better:

```bash
python3 tests/check.py --update
```

**A full run, which does cost money:**

```bash
./booktrans tests/corpus/01_Semiosis_en.epub --to ru -o /tmp/out/01.fb2
```

The language each book should be translated into is recorded in the manifest
under `переводить_на`: several of the books are Russian and exist to exercise
the reverse direction.

## What each book is for

| Book | Why it is here |
|---|---|
| 01–03 Sue Burke | trade epub layout: headings by class, cover, dedication, "About the author" with a photograph, advertising and images at the end. Three books from one publisher — markup must be worked out afresh for each |
| 04 The Long Watch | strict epub3 with genuine `epub:type` footnotes. Written for this corpus: no book with that markup could be found in the wild |
| 05 Herodotus | Project Gutenberg: footnotes marked by class rather than `epub:type`, the anchor sitting in a separate empty paragraph, a "return" link at the end |
| 06 Moby-Dick | Project Gutenberg without footnotes: long paragraphs, archaic English |
| 07 Lem | fb2: author's footnotes in the book's second body, `xlink` references from the text, translated out of Russian |
| 08 Belyanin | fb2 with verse in `poem/stanza/v` tags |
| 09 afranij | plain text in cp1251 with no headings: encoding detection and working without markup |
| 10 paper | pdf: running headers, hyphenation, no markup at all |
| 11 Neverness | epub built from anonymously named fragments: epigraphs with attributions and verse are told apart only by style, which means only by the model. The verse includes Blake and Housman, both of whom have recognised Russian translations — so it also shows whether the pipeline reaches for those instead of inventing its own |

## Provenance

Books 05 and 06 come from Project Gutenberg and are in the public domain.
Book 04 was written for this corpus. The rest are two-chapter extracts from
books in the owner's library; they are here as specimens of layout, and must
not be distributed with the pipeline.
