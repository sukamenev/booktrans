"""Offline integration check: exercise the actual CLI with a deterministic agent."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.dom import minidom
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from booktrans import output


def fake_agent():
    task = json.load(sys.stdin)
    if isinstance(task, list):
        print(json.dumps({"facts": [{"name": "Sam", "quote": b["text"], "id": b["id"]}
                                    for b in task if "Sam is her name." in b["text"]]}))
        return
    edits = []
    for b in task["fragments"]:
        if "bad anchor test" in b["text"]:
            edits.append({"id": b["id"], "before": "missing!", "after": "new"})
        if "Sam have" in b["text"]:
            assert any("Sam is her name." in f["quote"] for f in task["evidence"])
            edits.append({"id": b["id"], "before": "Sam have", "after": "Sam has"})
        if "ушол" in b["text"]:
            edits.append({"id": b["id"], "before": "ушол", "after": "ушёл"})
        if "Hallo - Привет" in b["text"]:
            edits.append({"id": b["id"], "before": "Hallo - Привет", "after": "Hallo\n\n— Привет"})
    print(json.dumps({"edits": edits, "queries": []}))


class Workflow(unittest.TestCase):
    def cli(self, path, language, *extra):
        return subprocess.run([sys.executable, str(ROOT / "booktrans"), str(path),
                               "--copyedit", "--to", language, "--agent", "cmd", "--agent-cmd",
                               f'"{sys.executable}" "{Path(__file__).resolve()}" fake {{system_file}}',
                               "--chunk-words", "4", *extra], capture_output=True, text=True)

    def test_invalid_edit_never_publishes_partial_book(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "draft.txt")
            path.write_text("Он ушол.\n\nbad anchor test")
            ret = self.cli(path, "ru", "--chunk-words", "1", "--retries", "1")
            self.assertNotEqual(ret.returncode, 0)
            self.assertIn("anchor", ret.stderr)
            self.assertFalse(Path(d, "draft.edited.txt").exists())
            self.assertFalse(Path(d, "draft.edited.txt.changes.json").exists())

    def test_epub_future_evidence_and_assets(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "story.epub")
            output.write_epub(str(path), {"title": "Story", "target_lang": "en"},
                              [("title", "One", "c1", None), ("p", "Sam have it.", "p1", None),
                               ("title", "Two", "c2", None), ("p", "Sam is her name.", "p2", None)],
                              {}, {}, "")
            before = path.read_bytes()
            ret = self.cli(path, "en")
            self.assertEqual(ret.returncode, 0, ret.stderr)
            self.assertEqual(path.read_bytes(), before)
            with ZipFile(path) as a, ZipFile(Path(d, "story.edited.epub")) as b:
                self.assertEqual(a.namelist(), b.namelist())
                different = [k for k in a.namelist() if a.read(k) != b.read(k)]
                self.assertEqual(len(different), 1)
                self.assertIn(b"Sam has it.", b.read(different[0]))

    def test_fb2_split_and_preserve_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "story.fb2")
            path.write_text('<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
                            '<body><section><p id="line">Hallo - Привет</p><p><emphasis>ушол'
                            '</emphasis></p></section></body></FictionBook>', encoding="utf-8")
            ret = self.cli(path, "ru")
            self.assertEqual(ret.returncode, 0, ret.stderr)
            doc = minidom.parse(str(Path(d, "story.edited.fb2")))
            ps = doc.getElementsByTagName("p")
            self.assertEqual(len(ps), 3)
            self.assertEqual([p.getAttribute("id") for p in ps], ["line", "", ""])
            self.assertEqual(ps[1].firstChild.data, "— Привет")
            self.assertEqual(ps[2].firstChild.firstChild.data, "ушёл")

    def test_txt_report_encoding_and_source_guard(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "story.txt")
            before = "Он ушол.\r\n\r\nГлава 2\r\n".encode("cp1251")
            path.write_bytes(before)
            ret = self.cli(path, "ru", "--encoding", "cp1251")
            self.assertEqual(ret.returncode, 0, ret.stderr)
            self.assertEqual(Path(d, "story.edited.txt").read_bytes(),
                             before.replace("ушол".encode("cp1251"), "ушёл".encode("cp1251")))
            report = json.loads(Path(d, "story.edited.txt.changes.json").read_text())
            self.assertEqual(report["edits"][0]["before"], "Он ушол.")
            self.assertNotEqual(self.cli(path, "ru", "-o", str(path)).returncode, 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    if sys.argv[1:2] == ["fake"]:
        fake_agent()
    else:
        unittest.main()
