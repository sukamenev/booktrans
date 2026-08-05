"""Разметка книг без разметки: pdf и простой текст.

В epub и fb2 структура записана самим издательством, а в pdf её нет вовсе.
Правилами она не выводится: на одной книге колонтитул «THE SCIENTIST»
распознался пятнадцатью разными способами и все пятнадцать стали заголовками
глав, а настоящие заголовки не нашлись ни разу.

Через модель проходит не текст, а его опись: куски пронумерованы, обратно
приходят одни пометки. Текст остаётся байт в байт — подменить его модель не
может при всём желании.
"""
import re

WINDOW = 200        # кусков в одном запросе
HEAD, TAIL = 110, 60    # сколько знаков куска показывать с начала и с конца
KINDS = {"+", "title", "skip", "verse"}


def _show(i, p):
    p = re.sub(r"\s+", " ", p).strip()
    if len(p) > HEAD + TAIL + 3:
        p = p[:HEAD] + " … " + p[-TAIL:]
    return f"{i} {p}"


def _parse(out, lo, hi):
    """Ответ модели → {номер куска: метка}. Чужие номера отбрасываем."""
    got = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s*$", line)
        if not m:
            continue
        i, kind = int(m.group(1)), m.group(2).strip("`*.,")
        if lo <= i <= hi and kind in KINDS:
            got[i] = kind
    return got


def plan(paras, run, log):
    """Пометки для каждого куска. `run` — вызов модели: (prompt) → текст."""
    marks = {}
    for lo in range(0, len(paras), WINDOW):
        part = paras[lo:lo + WINDOW]
        body = "\n".join(_show(lo + i + 1, p) for i, p in enumerate(part))
        out = run(body)
        marks.update(_parse(out, lo + 1, lo + len(part)))
    return marks


def apply(paras, marks):
    """Склеить и разметить по пометкам: [(вид, текст), ...]."""
    out = []
    for i, p in enumerate(paras, 1):
        kind = marks.get(i, "p")
        if kind == "skip":
            continue
        if kind == "+" and out:
            sep = "" if out[-1][1].endswith("-") else " "
            out[-1] = (out[-1][0], out[-1][1].rstrip("-") + sep + p)
            continue
        out.append(("p" if kind == "+" else kind, p))
    return out
