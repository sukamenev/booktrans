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

    def run(self, system, prompt, image=None):
        self.calls += 1
        ids = re.findall(r"\[\[\[[PVT]\s+(\S+?)\]\]\]", prompt)
        body = "".join(f"[[[P {i}]]]\nперевод\n" for i in ids)
        return body + "\n[[[META]]]\nSUMMARY: было и прошло\n", \
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

    # Метка времени решает спор двух файлов кусков за один блок. У сплошных
    # карт «блок → работа» спорить не с кем, и метка легла бы туда как блок с
    # именем `saved`: проход, считающий работу по блокам, падал на ней с
    # «object of type float has no len()».
    import tempfile
    d = tempfile.mkdtemp()
    chunk, flat = f"{d}/0001.json", f"{d}/ocrfix.json"
    P._save(chunk, {"index": 1, "tr": {"s01.b0001": "перевод"}})
    P._save(flat, {"s01.b0001": ["правка"]}, stamp=False)
    got = json.load(open(chunk, encoding="utf-8"))
    ok("файл куска метку получает", "saved" in got, sorted(got))
    got = json.load(open(flat, encoding="utf-8"))
    ok("сплошная карта — нет", "saved" not in got, sorted(got))
    # А если метка уже легла от прежнего выпуска — снимаем при чтении.
    got["saved"] = 123.4
    json.dump(got, open(flat, "w", encoding="utf-8"))
    ok("прежняя метка снимается при чтении", "saved" not in P._blockmap(flat),
       sorted(P._blockmap(flat)))
    ok("нет файла — пустая карта", P._blockmap(f"{d}/нет.json") == {})

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

    # Имя файла — номер куска, а нарезка между прогонами меняется: убрали из
    # книги повторы — и восемьдесят первый кусок стал восьмидесятым. Запись
    # нового куска под тем же именем стирала работу, лежавшую там от старого:
    # на живой книге так пропало 104 блока из 701, и увидеть это можно было
    # только на сборке.
    d = tempfile.mkdtemp()
    p = d + "/0001.json"
    P._save(p, {"index": 1, "tr": {"s01.b0001": "первый"},
                "src": {"s01.b0001": "aaa"}})
    P._save(p, {"index": 1, "tr": {"s01.b0009": "девятый"},
                "src": {"s01.b0009": "bbb"}})
    got = json.load(open(p, encoding="utf-8"))
    ok("перезапись куска не стирает чужую работу",
       got["tr"] == {"s01.b0001": "первый", "s01.b0009": "девятый"}, got["tr"])
    ok("отпечатки переносятся вместе с ней",
       set(got["src"]) == {"s01.b0001", "s01.b0009"}, got["src"])
    P._save(p, {"index": 1, "tr": {"s01.b0001": "переведён заново"},
                "src": {"s01.b0001": "ccc"}})
    got = json.load(open(p, encoding="utf-8"))
    ok("а новую работу кладёт поверх старой",
       got["tr"]["s01.b0001"] == "переведён заново", got["tr"])
    shutil.rmtree(d)

    # Оборотная сторона той же бережливости: блок остаётся лежать и в старом
    # файле, и в новом. Читали их по имени — и побеждал не свежий перевод, а
    # больший номер: кусок переводился заново, а в книгу шёл прежний текст.
    d = tempfile.mkdtemp()
    os.makedirs(d + "/tr")
    P._save(d + "/tr/0059.json", {"index": 59, "tr": {"s17.b0224": "прежний"},
                                  "src": {"s17.b0224": "aaa"}})
    P._save(d + "/tr/0058.json", {"index": 58, "tr": {"s17.b0224": "свежий"},
                                  "src": {"s17.b0224": "aaa"}})
    ok("файлы кусков читаются в порядке записи",
       [n for n, _ in P.chunk_files(d + "/tr")] == ["0059.json", "0058.json"],
       [n for n, _ in P.chunk_files(d + "/tr")])
    tr, _ = P.all_translations(d)
    ok("побеждает свежая работа, а не больший номер",
       tr["s17.b0224"] == "свежий", tr["s17.b0224"])
    # У файлов прежних версий отметки времени внутри нет — порядок берётся по
    # самому файлу, и правило продолжает работать.
    for n in ("0058", "0059"):
        x = json.load(open(f"{d}/tr/{n}.json", encoding="utf-8"))
        x.pop("saved")
        json.dump(x, open(f"{d}/tr/{n}.json", "w", encoding="utf-8"), ensure_ascii=False)
    os.utime(d + "/tr/0059.json", (1, 1))
    os.utime(d + "/tr/0058.json", (2, 2))
    tr, _ = P.all_translations(d)
    ok("без отметки порядок берётся по времени файла",
       tr["s17.b0224"] == "свежий", tr["s17.b0224"])
    shutil.rmtree(d)

    # То же имя файла, но у правки: список `blocks` при перезаписи не
    # сливается, в отличие от карт `src` и `edits`. Готовность считалась по
    # нему — и работа, сделанная при прежней нарезке, выглядела несделанной.
    # Каждый запуск переделывал горстку кусков, а новая запись стирала
    # предыдущую, и конца этому не было: на живой книге так потеряли запись
    # о 201 блоке.
    d = tempfile.mkdtemp()
    os.makedirs(d + "/tr")
    os.makedirs(d + "/ed")
    os.makedirs(d + "/prompts")      # её делает cli; иначе не увидим расхождения
    was = [{"id": "s01.b0001", "kind": "p", "text": "Первый абзац."},
           {"id": "s01.b0002", "kind": "p", "text": "Второй абзац."}]
    now = [{"id": "s09.b0001", "kind": "p", "text": "Девятый абзац."}]
    P._save(d + "/tr/0001.json",
            {"index": 1, "tr": {b["id"]: "перевод" for b in was + now}})

    def edited(bs):
        P._save(d + "/ed/0001.json",
                {"index": 1, "model": "стенд", "cost_usd": 0, "notes": "",
                 "blocks": [b["id"] for b in bs], "edits": {},
                 "src": {b["id"]: P.fingerprint("перевод") for b in bs}})

    edited(was)                      # прогон вчерашней нарезки
    edited(now)                      # нарезка сдвинулась, файл переписан
    a = Stub()
    P.edit(d, [{"index": 1, "label": "", "words": 2, "blocks": was}],
           a, "", "", 1, lambda m="", end="\n": None)
    ok("правка по прежней нарезке не забывается", a.calls == 0,
       f"редактора звали {a.calls} раз")
    shutil.rmtree(d)

    print(f"\nслучаев: 17   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
