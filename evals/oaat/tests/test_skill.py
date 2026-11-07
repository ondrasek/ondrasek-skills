"""The live oaat plugin of this repository is the skill under test: the runner and the checks point at it."""
import json
import re
import unittest

import run_evals

PLUGIN = run_evals.BUNDLED_PLUGIN
SKILL = run_evals.BUNDLED_SKILL
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
MARKETPLACE = PLUGIN.parents[1] / ".claude-plugin" / "marketplace.json"


class LiveSkillTests(unittest.TestCase):
    def test_the_runner_points_at_the_live_plugin(self):
        self.assertEqual(PLUGIN.name, "oaat")
        self.assertTrue(SKILL.is_file(), SKILL)
        self.assertTrue(MANIFEST.is_file(), MANIFEST)

    def test_the_manifest_names_the_plugin_oaat(self):
        self.assertEqual(json.loads(MANIFEST.read_text(encoding="utf-8"))["name"], "oaat")

    def test_the_skill_text_names_itself_oaat(self):
        self.assertRegex(SKILL.read_text(encoding="utf-8"), re.compile(r"^name: oaat$", re.M))

    def test_the_marketplace_lists_the_plugin_at_the_manifest_version(self):
        version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
        entry = next(p for p in json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"] if p["name"] == "oaat")
        self.assertEqual(entry["version"], version)


if __name__ == "__main__":
    unittest.main()
