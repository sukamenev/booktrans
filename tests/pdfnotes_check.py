#!/usr/bin/env python3
"""Two author notes numbered 1 survive a legacy rebuild in every exporter."""
import copy
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from booktrans import build, output, pdfnotes  # noqa: E402


def test():
    pages = [
        (1, 2, "A first example shows why this matters.", "Alpha press records the first study"),
        (3, 4, "A second example has a different source.", "Beta press records the second study"),
    ]
    blocks, anchors, text = [], [], {}
    for p, q, paragraph, citation in pages:
        first, second = f"p{p}", f"p{q}"
        blocks += [dict(id=first, kind="p", text=paragraph+"<a1>[1]</a1>", _page=p, links=["#n1"]),
                   dict(id=second, kind="note", text=citation, _page=q, name="1", note_id="n1")]
        text.update({first: f"Перевод {p}.<a1>[1]</a1>", second: citation})
        anchors += [pdfnotes.Anchor(p,q,"1",tuple(paragraph.lower().rstrip(".").split()),(),False),
                    pdfnotes.Anchor(q,p,"1",(),tuple(citation.lower().split()),True)]
    stored = copy.deepcopy(blocks)
    # A conflicting number and a missing match retain their prose.
    blocks += [dict(id="unknown", kind="p", text="An unrelated value.<a1>[1]</a1>", _page=5, links=["#n1"])]
    text["unknown"] = blocks[-1]["text"]
    copied = copy.deepcopy(blocks)
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        (folder / "tr").mkdir()
        save = folder / "tr" / "0001.json"
        save.write_text(json.dumps({"index": 1, "tr": text, "footnotes": [
            {"block": "p1", "kind": "term", "term": "Перевод", "text": "Пояснение."}]}), encoding="utf-8")
        before = save.read_bytes()
        with patch.object(pdfnotes, "read_anchors", return_value=anchors):
            for ext in ("md", "fb2", "epub", "tex"):
                p = folder / ("book." + ext)
                build.build_book(td, {"title": "Book", "target_lang": "ru"}, blocks,
                                 None, str(p), lambda _: None, source_pdf="source.pdf")
                if ext == "epub":
                    with zipfile.ZipFile(p) as zip_:
                        roots = [zip_.read(n) for n in zip_.namelist() if n.endswith(".xhtml")]
                        for r in roots:
                            ElementTree.fromstring(r)
                        result = b"\n".join(roots).decode()
                else:
                    result = p.read_text(encoding="utf-8")
                if ext == "tex":
                    for bid in ("p2", "p4"):
                        dst = output._tex_destination(bid)
                        assert r"\hyperlink{"+dst+"}{1}" in result
                        assert r"\hypertarget{"+dst+"}{}" in result
                    assert r"\footnote[1]" in result and r"[+\arabic{footnote}]" in result
                else:
                    assert "#p2" in result and "#p4" in result, ext
                    assert 'id="p2"' in result and 'id="p4"' in result, ext
                    assert "[+1]" in result, ext
                assert "#n1" not in result or ext in ("fb2", "epub"), ext
        assert blocks == copied and before == save.read_bytes()
    assert pdfnotes.bind(anchors[:1], stored)[0] == 0
    assert stored[0]["links"] == [""]
    dollars = output._tex(r"35 \$3 coffees or 60 \$5 drinks")
    assert dollars == r"35 \$3 coffees or 60 \$5 drinks"
    image = output._inline('$x+<imgmath name="math_test.png"/>+y$', output.HTML_INLINE)
    assert 'src="img/math_test.png"' in image
    ElementTree.fromstring(f"<p>{image}</p>")
    print("  pdfnotes_check: legacy PDF markers, destinations, collision, and four exporters passed")


if __name__ == "__main__":
    test()
