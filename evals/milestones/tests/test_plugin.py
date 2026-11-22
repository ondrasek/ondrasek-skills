"""The plugin under test still has the properties the evals rely on, and the harness uses the shared trace reader and command runner."""
import json
import re
import unittest
from pathlib import Path

import helpers
import checks
import fixtures

UNIT = Path(__file__).resolve().parents[1]
PLUGIN = helpers.PLUGIN_DIR
SKILL = PLUGIN / "skills" / "milestones" / "SKILL.md"
HOOK = PLUGIN / "hooks" / "milestones-reminder.sh"


class Shared(unittest.TestCase):
    def test_the_parser_and_the_command_runner_are_the_shared_ones(self):
        import agent_trace
        import runner_cmd
        for module in (agent_trace, runner_cmd):
            self.assertEqual(Path(module.__file__).resolve().parent, UNIT.parent / "_common" / "python", module.__name__)
            self.assertFalse((UNIT / "python" / f"{module.__name__}.py").exists(), "a local copy: delete it")


class Skill(unittest.TestCase):
    def test_names_agree(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertEqual(re.search(r"^name:\s*(\S+)", text, re.M).group(1), SKILL.parent.name)
        self.assertEqual(checks.SKILL, SKILL.parent.name)
        self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["name"], checks.SKILL)

    def test_the_description_still_names_the_phrases_the_trigger_cases_use(self):
        description = re.search(r"^description:\s*(.+)$", SKILL.read_text(encoding="utf-8"), re.M).group(1).lower()
        for phrase in ("where are we", "what is left before", "show the progress bar", "starts, finishes, gets blocked or needs them"):
            self.assertIn(phrase, description, f"the description no longer says {phrase!r}: update cases.csv or the skill")

    def test_the_skill_still_shows_and_writes_through_the_script(self):
        text = SKILL.read_text(encoding="utf-8")
        for needle in ("milestones.py\" show", "set 2 --state done", "init --empty --title", "Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/milestones.py *)"):
            self.assertIn(needle, text, f"SKILL.md no longer says {needle!r}: update the checks or the skill")

    def test_every_script_subcommand_the_checks_know_exists(self):
        script = helpers.SCRIPT.read_text(encoding="utf-8")
        for sub in ("show", *checks.WRITE_SUBCOMMANDS):
            self.assertIn(f'sub.add_parser("{sub}"', script, sub)


class Hook(unittest.TestCase):
    def test_the_texts_the_checks_look_for_are_still_in_the_hook(self):
        text = HOOK.read_text(encoding="utf-8")
        for needle in (checks.HOOK_NEEDLE, fixtures.MARKER, "stop_hook_active", "exit 2"):
            self.assertIn(needle, text, f"the hook no longer says {needle!r}: update the checks or the hook")

    def test_the_hook_is_registered_on_stop(self):
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("hooks/milestones-reminder.sh", json.dumps(data["hooks"]["Stop"]))


if __name__ == "__main__":
    unittest.main()
