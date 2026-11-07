"""Tests for the PreToolUse hook of the block-force-push plugin: hooks/block-force-push.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "block-force-push"
HOOK = PLUGIN / "hooks" / "block-force-push.sh"

BLOCKED = [
    "git push --force",
    "git push -f",
    "git push --force-with-lease",
    "git push --force-with-lease=main:abc123 origin main",
    "git push -fu origin main",
    "git push -uf origin main",
    "git push origin main --force",
    "cd /tmp/x && git push -f",
    "git add . && git commit -m x; git push --force origin main",
    "(git push -f)",
]
ALLOWED = [
    "git push",
    "git push origin main",
    "git push -u origin main",
    "git push origin feature-f",
    "git push origin --follow-tags",
    "git push origin --delete old-branch",
    "git push --dry-run",
    "git log -f",
    "git status",
    "ls -f",
    "echo hello",
]


@unittest.skipUnless(shutil.which("jq"), "needs jq")
class BlockForcePushTest(unittest.TestCase):
    def run_hook(self, command: str):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True, timeout=30)

    def test_force_pushes_are_blocked_with_a_message(self):
        for command in BLOCKED:
            with self.subTest(command=command):
                r = self.run_hook(command)
                self.assertEqual(r.returncode, 2, command)
                self.assertIn("Blocked: git force push", r.stderr)

    def test_ordinary_commands_pass(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                r = self.run_hook(command)
                self.assertEqual((r.returncode, r.stderr), (0, ""), command)

    def test_a_call_without_a_command_passes(self):
        r = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)

    def test_the_script_is_executable_and_registered_on_bash_pretooluse(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual([g["matcher"] for g in groups], ["Bash"])
        self.assertIn("hooks/block-force-push.sh", groups[0]["hooks"][0]["command"])
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", groups[0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
