"""Offline integration check: exercise the actual CLI with a deterministic agent."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from xml.dom import minidom
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from booktrans import copyedit, output
from booktrans import agent as transport


def fake_agent():
    task = json.load(sys.stdin)
    if os.environ.get("BT_FIXTURE_DIR"):
        directory = Path(os.environ["BT_FIXTURE_DIR"])
        stage = "scout" if isinstance(task, list) else "editor"
        key = (task if isinstance(task, list) else task["fragments"])[0]["id"]
        with (directory / "calls").open("a") as f:
            print(stage, key, file=f)
        if stage == "editor" and key == "2" and not (directory / "resume").exists():
            print("No JSON this time")
            return
        print("Here is the result:\n```JSON")
    if isinstance(task, list):
        print(json.dumps({"facts": [{"name": "Sam", "quote": b["text"], "id": b["id"]}
                                    for b in task if "Sam is her name." in b["text"]]}))
        if os.environ.get("BT_FIXTURE_DIR"):
            print("```")
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
    if os.environ.get("BT_FIXTURE_DIR"):
        print("```")


class Workflow(unittest.TestCase):
    def test_timed_out_chunk_retries_then_resumes_from_cache(self):
        class Gateway:
            def run(self, system, user):
                transport.openrouter_post({}, "test", 1, "http://localhost/v1/chat/completions")

        with tempfile.TemporaryDirectory() as d, \
                patch.object(transport.urllib.request, "urlopen", side_effect=TimeoutError("read timed out")), \
                patch.object(copyedit.time, "sleep") as pause:
            client = Gateway()
            replies = iter((client.run, lambda *_: ('{"facts":[]}', {})))
            client.run_json = lambda *args: next(replies)(*args)
            args = (client, "task", [], lambda obj: obj["facts"], Path(d), ["scout"], 2)
            self.assertEqual(copyedit.request(*args), [])
            self.assertEqual(copyedit.request(*args), [])  # exhausted transport, must hit cache
            pause.assert_called_once_with(2)

    def cli(self, path, *extra, env=None):
        return subprocess.run([sys.executable, str(ROOT / "booktrans"), str(path),
                               "--copyedit", "--agent", "cmd", "--agent-cmd",
                               f'"{sys.executable}" "{Path(__file__).resolve()}" fake {{system_file}}',
                               "--chunk-words", "4", *extra], capture_output=True, text=True,
                               env={**os.environ, "BT_TO": "unused-translation-target", **(env or {})})

    def test_resume_after_non_json_during_edit(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            path = directory / "draft.txt"
            path.write_text("Он ушол.\n\nОн ушол.")
            work = directory / "draft.txt.copyedit.work"
            args = ("--chunk-words", "1", "--retries", "1")
            env = {"BT_FIXTURE_DIR": d}
            fail = self.cli(path, *args, env=env)
            self.assertNotEqual(fail.returncode, 0)
            state = json.loads((work / "progress.json").read_text())
            self.assertEqual(state["completed"], {"scout": 2, "editor": 1})
            self.assertEqual((state["state"], state["stage"], state["chunk"]), ("failed", "editor", 2))
            self.assertEqual(json.loads((work / "last-invalid-response.json").read_text())["response"].strip(),
                             "No JSON this time")
            (directory / "resume").touch()
            ok = self.cli(path, *args, env=env)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual((directory / "calls").read_text().splitlines(),
                             ["scout 1", "scout 2", "editor 1", "editor 2", "editor 2"])
            self.assertEqual(json.loads((work / "progress.json").read_text())["state"], "finished")
            self.assertEqual((directory / "draft.edited.txt").read_text(), "Он ушёл.\n\nОн ушёл.")

    def test_truncated_and_ambiguous_objects_are_not_recovered(self):
        for raw in ('', 'refusal', '{"facts":[{}', '{"facts":[]} and {"facts":[]}', '[{"facts":[]}]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                copyedit.decode_reply(raw)

    def test_invalid_edit_never_publishes_partial_book(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "draft.txt")
            path.write_text("Он ушол.\n\nbad anchor test")
            ret = self.cli(path, "--chunk-words", "1", "--retries", "1")
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
            ret = self.cli(path)
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
            ret = self.cli(path)
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
            ret = self.cli(path, "--encoding", "cp1251")
            self.assertEqual(ret.returncode, 0, ret.stderr)
            self.assertEqual(Path(d, "story.edited.txt").read_bytes(),
                             before.replace("ушол".encode("cp1251"), "ушёл".encode("cp1251")))
            report = json.loads(Path(d, "story.edited.txt.changes.json").read_text())
            self.assertEqual(report["edits"][0]["before"], "Он ушол.")
            self.assertNotEqual(self.cli(path, "-o", str(path)).returncode, 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    if sys.argv[1:2] == ["fake"]:
        fake_agent()
    else:
        unittest.main()
