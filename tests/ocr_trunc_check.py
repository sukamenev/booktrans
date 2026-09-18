#!/usr/bin/env python3
"""Проверка на обрезку при визуальном распознавании: надписи внутри
вырезанных картинок в норму не входят.

Схему с текстом модель по правилам отдаёт картинкой, а сырой слой страницы
её надписи содержит; без вычитания честный ответ выглядел обрезанным.

    python3 tests/ocr_trunc_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E                          # noqa: E402


class FakePage:
    """Текстовый слой: что лежит в какой полосе страницы 600×800 (ось Y снизу)."""

    def __init__(self):
        self.calls = []

    def get_text_bounded(self, left, bottom, right, top):
        self.calls.append((left, bottom, right, top))
        return "label  " * 100 if top > 500 else ""          # надписи в верхней части


def main():
    bad = cases = 0

    def ok(name, cond, got=""):
        nonlocal bad, cases
        cases += 1
        print(f"  {name:52} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    text = "# Глава\n\n![image]([100, 0, 500, 1000])\n\n*Figure 1. Caption*\n\n![x]([bad])\n\n![image]([600, 50, 590, 900])"
    boxes = E._img_boxes(text)
    ok("рамки читаются, кривые отброшены", boxes == [(100, 0, 500, 1000)], boxes)
    ok("ответ без картинок — рамок нет", E._img_boxes("Просто текст страницы.") == [])
    page = FakePage()
    n = E._chars_in_boxes(page, (600, 800), boxes)
    ok("координаты переведены в точки pdf, Y снизу", page.calls == [(0.0, 400.0, 600.0, 720.0)], page.calls)
    ok("знаки внутри рамки сосчитаны без лишних пробелов", n == len(("label " * 100).strip()), n)
    ok("рамка над пустым местом ничего не вычитает", E._chars_in_boxes(FakePage(), (600, 800), [(600, 0, 900, 1000)]) == 0)

    class Broken:
        def get_text_bounded(self, **kw):
            raise RuntimeError("no text layer")
    ok("сбой текстового слоя не роняет страницу", E._chars_in_boxes(Broken(), (600, 800), boxes) == 0)

    print(f"\nслучаев: {cases}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
