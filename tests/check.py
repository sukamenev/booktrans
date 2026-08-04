#!/usr/bin/env python3
"""Проверка разбора книг из набора — без единого обращения к модели.

Читает каждую книгу и сверяет с описью `manifest.json`. Расхождение значит
одно из двух: либо в разборе завелась ошибка, либо разбор стал лучше и опись
пора обновить. Различить это может только человек, поэтому скрипт не
исправляет опись сам.

    python3 tests/check.py            сверить
    python3 tests/check.py --update   принять нынешние числа за верные
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import extract as E, lang as G          # noqa: E402

FIELDS = ("язык", "абзацев", "стихов", "заголовков", "сносок",
          "со_ссылками", "картинок", "обложка", "слов")


def measure(path):
    meta, blocks, cover, imgs = E.read_book(path)
    ps = [b for b in blocks if b["kind"] == "p"]
    vs = [b for b in blocks if b["kind"] == "verse"]
    nt = [b for b in blocks if b["kind"] == "note"]
    tt = [b for b in blocks if b["kind"] in ("title", "subtitle")]
    lk = [b for b in blocks if b.get("links")]
    return {
        "язык": G.detect(" ".join(b["text"] for b in (ps + vs)[:150]))[0],
        "абзацев": len(ps), "стихов": len(vs), "заголовков": len(tt),
        "сносок": len(nt), "со_ссылками": len(lk), "картинок": len(imgs),
        "обложка": bool(cover),
        "слов": sum(len(b["text"].split()) for b in ps + vs),
    }


def main():
    update = "--update" in sys.argv
    mpath = os.path.join(HERE, "manifest.json")
    # Описи в репозитории нет: имена файлов набора называют книги, защищённые
    # авторским правом. Первый прогон создаёт её сам.
    if not os.path.exists(mpath):
        if not update:
            print("описи нет. Соберите набор (build_corpus.py) и создайте её:\n"
                  "  python3 tests/check.py --update\n"
                  "Устройство описи — в manifest.json.example")
            return 1
        man = {"_": json.load(open(mpath + ".example", encoding="utf-8"))["_"],
               "books": {}}
    else:
        man = json.load(open(mpath, encoding="utf-8"))
    books = man["books"]
    bad = 0
    for path in sorted(glob.glob(os.path.join(HERE, "corpus", "*"))):
        name = os.path.basename(path)
        want = books.get(name)
        if want is None and update:
            books[name] = want = {}
        if want is None:
            print(f"  {name:32s} нет в описи — добавьте или удалите файл")
            bad += 1
            continue
        try:
            got = measure(path)
        except E.BadBook as e:
            print(f"  {name:32s} ОТКАЗ: {str(e).splitlines()[0][:52]}")
            bad += 1
            continue
        except Exception as e:                     # noqa: BLE001
            print(f"  {name:32s} ПАДЕНИЕ {type(e).__name__}: {e}")
            bad += 1
            continue
        diff = [(k, want.get(k), got[k]) for k in FIELDS if want.get(k) != got[k]]
        if diff and update:
            want.update(got)
        if not diff:
            print(f"  {name:32s} совпадает")
        else:
            bad += 1
            print(f"  {name:32s} расхождения:")
            for k, a, b in diff:
                print(f"        {k:14s} было {a!r:>8}  стало {b!r:>8}")
    missing = set(books) - {os.path.basename(p)
                            for p in glob.glob(os.path.join(HERE, "corpus", "*"))}
    for name in sorted(missing):
        print(f"  {name:32s} файла нет, а в описи он есть")
        bad += 1
    if update:
        json.dump(man, open(mpath, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("\nопись обновлена")
        return 0
    print(f"\nкниг: {len(books)}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
