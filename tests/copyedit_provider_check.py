"""Offline CLI integrations: no provider should require a Claude installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from booktrans import cli


class Providers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.book = self.path / "draft.txt"
        self.book.write_text("This are a test.")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("BT_", "OPENAI_", "OPENROUTER_"))}
        self.env.update(PATH=tmp.name, HOME=tmp.name, XDG_CONFIG_HOME=tmp.name, APPDATA=tmp.name)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "booktrans"), str(self.book),
                               "--copyedit", *args], env=self.env, capture_output=True,
                              text=True, timeout=15)

    def test_missing_default_before_scout(self):
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--provider chatgpt", result.stderr)
        self.assertIn("--provider api", result.stderr)
        self.assertNotIn("scout 1/", result.stdout)

    def test_first_interactive_translation_sees_saved_roles(self):
        selections = []

        # Stop at dispatch: exercise real first-run setup and normal translation
        # routing without calling a hosted model or translating the fixture.
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(cli, "Run", autospec=True) as run_cls, \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(sys.stdout, "isatty", return_value=True), \
                patch.object(sys, "argv", ["booktrans", str(self.book)]), \
                patch("builtins.input", side_effect=["chatgpt", "shared", "translator-model",
                                                     "claude:editor-model", ""]):
            for _ in range(2):  # No settings questions on the second run.
                cli.main()
                models = run_cls.call_args.args[3]
                selections.append([models.specs(role)[0][:2]
                                   for role in ("scout", "translator", "editor")])
        expected = [("codex", "shared"), ("codex", "translator-model"), ("claude", "editor-model")]
        self.assertEqual(selections, [expected, expected])

    @unittest.skipIf(os.name == "nt", "POSIX CLI shim")
    def test_chatgpt_dispatches_both_roles_to_codex(self):
        cmd = self.path / "codex"
        cmd.write_text(f"#!{sys.executable}\n" + """
import json, sys
payload = sys.stdin.read()
scout = 'style sheet of recurring' in payload
result = {'facts': []} if scout else {'edits': [{'id':'1','before':'are','after':'is'}],'queries':[]}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}}))
""")
        cmd.chmod(0o755)
        result = self.cli("--provider", "chatgpt", "--model", "test-model")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.path / "draft.edited.txt").read_text(), "This is a test.")

    def test_configured_provider_hits_custom_api(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, body["model"], self.headers["Authorization"], body.get("response_format")))
                if body["model"] == "one-off" and "response_format" in body:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"response_format is unsupported"}}')
                    return
                scout = isinstance(json.loads(body["messages"][-1]["content"]), list)
                reply = {"facts": []} if scout else {"edits": [], "queries": []}
                event = {"choices": [{"delta": {"content": json.dumps(reply)}, "finish_reason": "stop"}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.env.update(OPENAI_API_KEY="fixture", NO_PROXY="127.0.0.1")
                config = subprocess.run([sys.executable, str(ROOT / "booktrans"), "configure"],
                                        env=self.env, text=True, capture_output=True, timeout=10,
                                        input=f"api\nshared\ntranslation-model\neditor-model\n\n"
                                              f"http://127.0.0.1:{server.server_port}/v1\n")
                self.assertEqual(config.returncode, 0, config.stderr)
                result = self.cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.book = self.path / "another.txt"
                self.book.write_text("Another manuscript.")
                result = self.cli("--editor", "one-off")
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [("/v1/chat/completions", model, "Bearer fixture", {"type": "json_object"})
                                 for model in ("shared", "editor-model", "shared", "one-off")] +
                         [("/v1/chat/completions", "one-off", "Bearer fixture", None)])


if __name__ == "__main__":
    unittest.main()
