#!/usr/bin/env python3
"""Ввоз внутри функции не должен перекрывать ввоз наверху файла.

Питон решает, что имя местное, по всей функции сразу, а не с той строки, где
стоит `import`. Поэтому `from . import extract` в одной ветке делает `extract`
местным для всей функции — и обращение к нему выше по коду падает с
`UnboundLocalError`, хотя модуль ввезён в самом начале файла. Так в 1.8.76
перестал читаться любой файл, для которого ещё нет `book.json`.

    python3 tests/imports_check.py
"""
import ast
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src", "booktrans")


def _bound(node):
    """Имена, которые заводит один узел ввоза."""
    for a in node.names:
        yield (a.asname or a.name).split(".")[0]


def shadowed(tree):
    """Местный ввоз имени, уже ввезённого наверху файла: (имя, строка)."""
    top = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top.update(_bound(node))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in _bound(node):
                    if name in top:
                        out.append((name, node.lineno))
    return out


def main():
    bad = 0
    files = sorted(glob.glob(os.path.join(SRC, "*.py")))
    for p in files:
        hits = shadowed(ast.parse(open(p, encoding="utf-8").read()))
        name = os.path.basename(p)
        print(f"  {name:46} {'чисто' if not hits else 'РАСХОЖДЕНИЕ'}"
              + ("" if not hits else "   перекрыто: "
                 + ", ".join(f"{n} (строка {i})" for n, i in hits)))
        bad += bool(hits)
    print(f"\nслучаев: {len(files)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
