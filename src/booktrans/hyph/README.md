# Hyphenation patterns

Plain-text hyphenation patterns (`*.pat.txt`) and exceptions (`*.hyp.txt`) from
the [hyph-utf8](https://github.com/hyphenation/tex-hyphen) project, commit
`5684c0f51c0b` (2026-02-24), unmodified. They are the same patterns TeX Live
ships; booktrans carries them so that a book exported to TeX hyphenates the
same way on a machine where the language package of TeX is not installed.

Each `hyph-<lang>.lic.txt` is the original header of the corresponding
`hyph-<lang>.tex`: authors, copyright and licence of that language's patterns.

| File | Language | Licence |
|---|---|---|
| hyph-ru | Russian | LPPL 1.2 or later |
| hyph-de-1996 | German, reformed orthography | MIT |
| hyph-es | Spanish | MIT/X11 |
| hyph-fr | French | MIT |
| hyph-hi | Hindi | MIT (one of several offered) |

English needs nothing: its patterns are part of every TeX format. Japanese and
Chinese are not hyphenated.
