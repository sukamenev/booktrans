#!/usr/bin/env python3
"""Agent pipes use UTF-8 even when Windows defaults to CP1251.

Replace only the CLI executable with a Python child; keep real subprocess
encoding and pipes so both sending and receiving Unicode are exercised.
"""
import json
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
from booktrans import agent as A


TEXT = "Русский × → 日本語 😀"
CHILD = r"""
import sys
got = sys.stdin.buffer.read().decode('utf-8').replace('\r\n', '\n')
assert got == sys.argv[1], repr(got)
sys.stdout.buffer.write(sys.argv[2].encode('utf-8'))
sys.stderr.buffer.write(sys.argv[3].encode('utf-8'))
"""


def main():
    real_run = subprocess.run
    cases = bad = 0
    expected, reply, stderr = "", "", TEXT

    def cli(cmd, **kw):
        kw.pop("shell", None)
        return real_run([sys.executable, "-c", CHILD, expected, reply, stderr], **kw)

    def check(name, action, want):
        nonlocal cases, bad
        cases += 1
        try:
            got = action()
            assert got == want, repr(got)
            print(f"  {name}: OK")
        except Exception as e:
            bad += 1
            print(f"  {name}: FAIL {e}")

    with patch.object(subprocess, "_text_encoding", return_value="cp1251"), \
            patch.object(A.subprocess, "run", side_effect=cli), \
            patch.object(A, "agy_home", return_value=None), \
            patch.dict(A.Agent._default_cache, {}, clear=True):
        expected, reply = TEXT, json.dumps({"result": TEXT}, ensure_ascii=False)
        check("claude Unicode request/reply",
              lambda: A.ClaudeAgent(model="test").run(TEXT, TEXT)[0], TEXT)

        expected = TEXT + "\n\n---\n\n" + TEXT
        reply = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": TEXT}}, ensure_ascii=False)
        check("codex Unicode request/reply",
              lambda: A.CodexAgent(model="test").run(TEXT, TEXT)[0], TEXT)

        expected, reply = TEXT + "\n\n" + TEXT, TEXT
        check("custom command Unicode request/reply",
              lambda: A.CommandAgent("test").run(TEXT, TEXT)[0], TEXT)

        expected = ""
        reply = json.dumps({"modelUsage": {"claude-test": {}}}) + "\n"
        check("claude model discovery",
              lambda: A.ClaudeAgent(model="test").default_model(), "claude-test")

        expected = "hi"
        reply = json.dumps({"model": "gemini-test", "response": TEXT}, ensure_ascii=False)
        check("agy model discovery",
              lambda: A.AgyAgent(model="test").default_model(), "gemini-test")

        reply, stderr = TEXT, "model: codex-test\n" + TEXT
        check("codex model discovery",
              lambda: A.CodexAgent(model="test").default_model(), "codex-test")

    print(f"\ncases: {cases}, failures: {bad}")
    return bool(bad)


if __name__ == "__main__":
    sys.exit(main())
