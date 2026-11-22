"""The skill under test still has the properties the evals rely on."""
import json
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401
import agent_trace
import checks
import runner_cmd

REPO = Path(__file__).resolve().parents[3]
UNIT = Path(__file__).resolve().parents[1]
COMMON = REPO / "evals" / "_common" / "python"
PLUGIN = REPO / "plugins" / "agent-md-audit"
SKILL = PLUGIN / "skills" / "agent-md-audit" / "SKILL.md"


class Skill(unittest.TestCase):
    def test_names_agree(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertEqual(re.search(r"^name:\s*(\S+)", text, re.M).group(1), SKILL.parent.name)
        self.assertEqual(checks.SKILL, SKILL.parent.name)
        self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["name"], checks.SKILL)

    def test_the_rules_the_checks_grade_are_still_in_the_skill(self):
        text = SKILL.read_text(encoding="utf-8")
        for needle in ("code.claude.com/docs/en/memory", "code.claude.com/docs/en/best-practices", "documented", "inferred",
                       "No approval, no edit", "Do not commit unless asked"):
            self.assertIn(needle, text, f"SKILL.md no longer says {needle!r}: update the checks or the skill")


class Shared(unittest.TestCase):
    """agent_trace and runner_cmd are the shared modules of evals/_common/python; this harness keeps no copy of them."""

    def test_no_local_copy_and_the_harness_imports_the_shared_modules(self):
        for name in ("agent_trace", "runner_cmd"):
            for directory in ("python", "tests"):
                self.assertFalse((UNIT / directory / f"{name}.py").exists(), f"delete {directory}/{name}.py: it lives in evals/_common/python")
        for module in (agent_trace, runner_cmd):
            self.assertEqual(Path(module.__file__).resolve().parent, COMMON.resolve(), module.__name__)


if __name__ == "__main__":
    unittest.main()
