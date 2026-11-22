"""The plugin under test still has the properties the evals rely on, and the copied modules are still copies."""
import json
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401
import checks

UNIT = Path(__file__).resolve().parents[1]
REPO = UNIT.parents[1]
PLUGIN = REPO / "plugins" / "auto-commit"
HOOK = PLUGIN / "hooks" / "auto-commit.sh"
REFERENCE = REPO / "evals" / "agent-md-audit" / "python"


class Copies(unittest.TestCase):
    @unittest.skipUnless(REFERENCE.is_dir(), "evals/agent-md-audit is not here")
    def test_the_copied_modules_are_byte_identical_to_the_reference(self):
        for name in ("agent_trace.py", "runner_cmd.py"):
            self.assertEqual((UNIT / "python" / name).read_bytes(), (REFERENCE / name).read_bytes(), f"{name} drifted: fix both copies")


class Plugin(unittest.TestCase):
    def test_names_agree(self):
        self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"], checks.PLUGIN)

    def test_the_phrases_the_checks_depend_on_are_still_in_the_hook(self):
        text = HOOK.read_text(encoding="utf-8")
        for needle in (checks.HOOK_MARK, "Conventional Commits", "Co-Authored-By", "Never rebase", "Never force-push", "no --no-verify"):
            self.assertIn(needle, text, f"the hook no longer says {needle!r}: update the checks or the hook")

    def test_the_env_patterns_are_the_hooks(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertEqual(re.search(r"^ENV_RE='([^']*)'", text, re.M).group(1), checks.ENV_RE.pattern)
        self.assertEqual(re.search(r"^ENV_OK_RE='([^']*)'", text, re.M).group(1), checks.ENV_OK_RE.pattern)

    def test_it_is_a_stop_hook_that_blocks_with_exit_2(self):
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["hooks"]), ["Stop"])
        self.assertIn("exit 2", HOOK.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
