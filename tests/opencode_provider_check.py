"""Run both editorial passes through the real OpenCode CLI and a loopback model."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("opencode"), "requires OpenCode V2 CLI")
class OpenCodeTest(unittest.TestCase):
    def test_manuscript_roundtrip(self):
        roles = []

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt = str(req["messages"])
                if "style sheet of recurring" in prompt:
                    roles.append("scout")
                    answer = {"facts": []}
                elif "Copyedit each passage" in prompt:
                    roles.append("editor")
                    answer = {"edits": [{"id": "1", "before": "are", "after": "is"}], "queries": []}
                else:
                    answer = {}
                event = {"choices": [{"index": 0, "delta": {"role": "assistant",
                          "content": json.dumps(answer)}, "finish_reason": "stop"}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory, HTTPServer(("127.0.0.1", 0), Model) as server:
            d = Path(directory)
            config = d / "config" / "opencode"
            config.mkdir(parents=True)
            (config / "opencode.json").write_text(json.dumps({
                "model": "fixture/small",
                "providers": {"fixture": {"package": "@opencode/ai/providers/openai-compatible",
                              "models": {"small": {}}, "settings": {"apiKey": "fixture",
                              "baseURL": f"http://127.0.0.1:{server.server_port}/v1"}}}}))
            source = d / "draft.txt"
            source.write_text("This are a test.")
            env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP") if k in os.environ}
            env.update(HOME=directory, XDG_CONFIG_HOME=str(config.parent), XDG_DATA_HOME=str(d / "data"),
                       XDG_CACHE_HOME=str(d / "cache"), XDG_STATE_HOME=str(d / "state"), NO_PROXY="127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                r = subprocess.run([sys.executable, str(ROOT / "booktrans"), str(source), "--copyedit",
                                    "--provider", "opencode", "--model", "fixture/small", "--retries", "1"],
                                   env=env, capture_output=True, text=True, timeout=60)
            finally:
                server.shutdown()
                thread.join()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("scout", roles)
            self.assertIn("editor", roles)
            self.assertEqual((d / "draft.edited.txt").read_text(), "This is a test.")


if __name__ == "__main__":
    unittest.main()
