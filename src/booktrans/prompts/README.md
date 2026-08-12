# Prompts, and how to write your own

These are the instructions the pipeline gives the model — one file per pass:
`translate.md`, `edit.md`, `scout.md`, `format.md`, `structure.md`, `notes.md`,
`code.md`, `ocr.md`, `ocrfix.md`, and `style.md`, which goes into every request.
They are written in Russian; the model works with any target language from
them.

## Replacing a prompt

Put your own version in the folder named after the **target language**. When
the book is translated into German and `de/translate.md` exists, the pipeline
uses it instead of `translate.md`:

    prompts/de/translate.md

Empty folders are here for the commonest languages — `en`, `es`, `fr`, `zh`,
`ja`, `hi`. Any other language code works the same: create the folder and drop
the file in. A folder is only ever consulted for the language being translated
into, so `--to de` never reads `es/`.

Note that a target language also needs its rules file, `langs/de.md`. Shipped
today: `de`, `en`, `es`, `fr`, `ja`, `ru`, `zh`. A prompt folder without one
will simply never be reached.

## Adding to a prompt instead of replacing it

Name the file `.add.md` and it is appended to the prompt rather than taking
its place:

    prompts/de/translate.add.md     added when translating into German
    prompts/translate.add.md        added for every language

This is what you want most of the time. A prompt is a balanced thing, and the
author's version keeps up with the code; an addition of your own survives an
upgrade of the pipeline, while a full copy quietly falls behind it.

## What you must not translate

`TERM:`, `TEXT:`, `SUMMARY:`, `TERMS:` and the markers `[[[P]]]`, `[[[V]]]`,
`[[[NOTE]]]`, `[[[META]]]` are protocol tokens, not words. The parser looks for
exactly those. Translate them and the footnotes, the running digest and the
term list disappear without any error — the book assembles, the checks report
nothing, and you find out much later.

The pipeline compares a replacement against the original and says so before the
first request if a token has gone missing. It cannot catch everything, so leave
them alone.

## Where these files live

In a git checkout they are right here. Installed from PyPI they sit inside the
package (`.../site-packages/booktrans/prompts/`); an upgrade overwrites the
folder, so keep your own copies elsewhere and put them back, or work from a
checkout.
