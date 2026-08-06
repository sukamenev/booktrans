#!/usr/bin/env python3
"""Проверка возобновления: что считается уже сделанным.

Идентификатор блока позиционный — `s51.b0002` значит «пятьдесят первый
раздел, второй абзац». Стоит книге перечитаться с другой разметкой, и тот же
идентификатор указывает уже на другой текст. Раньше готовность считалась по
одним идентификаторам, поэтому кусок объявлялся переведённым, а перевод
доставался чужому абзацу — молча, потому что и блок на месте, и перевод есть.

Ошибка тут страшнее пропуска: пропуск видно на сборке, а чужой текст под
правильным номером не видно никогда.

    python3 tests/resume_check.py
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import pipeline as P                        # noqa: E402


class Stub:
    """Считает обращения: позвали — значит, кусок сочли несделанным."""

    model = "стенд"

    def __init__(self):
        self.calls = 0

    def run(self, system, prompt):
        self.calls += 1
        ids = re.findall(r"<<<[PVT]\s+(\S+?)>>>", prompt)
        body = "".join(f"<<<P {i}>>>\nперевод\n" for i in ids)
        return body + "\n<<<META>>>\nSUMMARY: было и прошло\n", \
            {"model": self.model, "cost_usd": 0}


def blocks(second="Второй абзац."):
    return [{"id": "s01.b0001", "kind": "p", "text": "Первый абзац."},
            {"id": "s01.b0002", "kind": "p", "text": second}]


def work(bl, fingerprints=True):
    d = tempfile.mkdtemp()
    os.makedirs(d + "/tr")
    rec = {"index": 1, "model": "стенд", "cost_usd": 0, "footnotes": [],
           "tr": {b["id"]: "перевод" for b in bl}}
    if fingerprints:
        rec["src"] = {b["id"]: P.fingerprint(b["text"]) for b in bl}
    json.dump(rec, open(d + "/tr/0001.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    return d


def ran(d, bl, said=None):
    """Позвали ли модель на этом куске."""
    chunks = [{"index": 1, "label": "", "words": 2, "blocks": bl}]
    a = Stub()
    P.translate(d, chunks, a, "", "", 1,
                lambda m="", end="\n": (said.append(m) if said is not None else None))
    return a.calls > 0


def main():
    bad = 0

    def ok(name, cond, got=""):
        nonlocal bad
        print(f"  {name:46} {'совпадает' if cond else 'РАСХОЖДЕНИЕ'}"
              + ("" if cond else f"   вышло: {got}"))
        bad += not cond

    ok("отпечаток не зависит от пробелов и разметки",
       P.fingerprint("Текст  <b>жирный</b>\nдальше")
       == P.fingerprint("Текст <b>жирный</b> дальше"))
    ok("другой текст — другой отпечаток",
       P.fingerprint("Текст") != P.fingerprint("Текст."))

    bl = blocks()
    d = work(bl)
    ok("текст тот же — кусок пропущен", not ran(d, bl))
    shutil.rmtree(d)

    # Книгу перечитали, между абзацами вставился новый — под тем же номером
    # оказался другой текст.
    d = work(blocks())
    ok("текст сменился — кусок переводится заново",
       ran(d, blocks("Совсем другой абзац, вставший на это место.")))
    shutil.rmtree(d)

    # Файлы прежних версий отпечатка не несут: сверить нечем, верим на слово,
    # но говорим об этом вслух.
    d = work(blocks(), fingerprints=False)
    said = []
    ok("без отпечатка кусок берут на веру",
       not ran(d, blocks("Другой текст."), said))
    ok("и предупреждают об этом",
       any("прежней версией" in m or "older version" in m for m in said), said)
    shutil.rmtree(d)

    print(f"\nслучаев: 6   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
