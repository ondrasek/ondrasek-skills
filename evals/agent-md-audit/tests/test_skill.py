"""The skill under test still has the properties the evals rely on."""
import json
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401
import checks

REPO = Path(__file__).resolve().parents[3]
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


if __name__ == "__main__":
    unittest.main()
