"""The plugin under test still has the properties the evals rely on, and report.sh (which has no tests of its own) behaves."""
import fnmatch
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
import checks
import run_evals
import state

UNIT = Path(__file__).resolve().parents[1]
PLUGIN = run_evals.PLUGIN
SKILL_DIR = PLUGIN / "skills" / "sitrep"
SCRIPT = SKILL_DIR / "scripts" / "report.sh"
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
SIBLING = UNIT.parent / "agent-md-audit" / "python"


class Copies(unittest.TestCase):
    def test_the_parser_and_the_command_runner_are_byte_identical_to_the_agent_md_audit_copies(self):
        if not SIBLING.is_dir():
            self.skipTest("evals/agent-md-audit is not here")
        for name in ("agent_trace.py", "runner_cmd.py"):
            self.assertEqual((UNIT / "python" / name).read_bytes(), (SIBLING / name).read_bytes(), f"{name} drifted from the copy it must equal")


class Skill(unittest.TestCase):
    def test_names_agree(self):
        self.assertEqual(re.search(r"^name:\s*(\S+)", SKILL_MD, re.M).group(1), SKILL_DIR.name)
        self.assertEqual(checks.SKILL, SKILL_DIR.name)
        self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["name"], checks.SKILL)

    def test_the_skill_declares_no_allowed_tools(self):
        # if it ever does, run_evals.ALLOWED must be revisited: the grants here are the whole permission story of a run
        self.assertNotRegex(SKILL_MD.split("---")[1], r"(?m)^allowed-tools")

    def test_the_rules_the_checks_grade_are_still_in_the_skill(self):
        for needle in ("scripts/report.sh", "window.SITREP", "do not edit `index.html`", "never into the project", "Copy JSON", "`dependsOn`",
                       "`.cache/milestones.json`", "`ms-<id>`", "Do not paste the item list into chat", 'report.sh" clean DIR'):
            self.assertIn(needle, SKILL_MD, f"SKILL.md no longer says {needle!r}: update the checks or the skill")

    def test_the_commands_skill_md_prescribes_match_the_harness_regexes_and_the_grants(self):
        spelled = re.findall(r'"\$\{CLAUDE_SKILL_DIR\}/scripts/report\.sh" (?:new|clean DIR)', SKILL_MD)
        self.assertEqual(len(spelled), 2)
        patterns = re.findall(r"Bash\(([^)]*)\)", run_evals.ALLOWED)
        for raw in spelled:
            command = raw.replace("${CLAUDE_SKILL_DIR}", "/p/skills/sitrep").replace("DIR", "/r/tmp/sitrep.Ab12Cd")
            self.assertTrue(state.REPORT_NEW_RE.search(command) or state.REPORT_CLEAN_RE.search(command), command)
            # the closing quote of the script path comes before the verb, so a grant of `report.sh *` alone would miss it;
            # fnmatch's * (anything) is a model of the permission pattern, not a verified one
            self.assertTrue(any(fnmatch.fnmatchcase(command, p) for p in patterns), f"no Bash grant matches {command!r}")


class Script(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = str(Path(self._tmp.name).resolve())

    def run_script(self, *args):
        # always an explicit throwaway TMPDIR: `new` sweeps sitrep.* directories older than a day in it
        return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, "TMPDIR": self.tmp})

    def test_it_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_new_makes_a_sitrep_directory_with_the_template_as_index(self):
        done = self.run_script("new")
        directory = Path(done.stdout.strip())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((directory.parent, directory.name.startswith("sitrep.")), (Path(self.tmp), True))
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["index.html"])
        self.assertEqual((directory / "index.html").read_bytes(), (SKILL_DIR / "template.html").read_bytes())

    def test_clean_removes_it(self):
        directory = self.run_script("new").stdout.strip()
        done = self.run_script("clean", directory)
        self.assertEqual((done.returncode, os.path.exists(directory)), (0, False), done.stderr)

    def test_clean_refuses_anything_else(self):
        outside = tempfile.mkdtemp(prefix="sitrep.")
        self.addCleanup(os.rmdir, outside)
        other = Path(self.tmp, "keep.Ab12Cd")
        other.mkdir()
        real = self.run_script("new").stdout.strip()
        link = Path(self.tmp, "sitrep.link")
        link.symlink_to(real)
        for refused in (outside, str(other), str(link), str(Path(self.tmp, "sitrep.missing"))):
            with self.subTest(path=refused):
                self.assertEqual(self.run_script("clean", refused).returncode, 1)
        self.assertTrue(os.path.isdir(outside) and other.is_dir() and os.path.isdir(real))

    def test_new_sweeps_only_old_sitrep_directories(self):
        old_report, old_other, young = (Path(self.tmp, n) for n in ("sitrep.Old111", "keep.Old111", "sitrep.Young1"))
        for directory in (old_report, old_other, young):
            directory.mkdir()
        three_days_ago = time.time() - 3 * 86400
        for directory in (old_report, old_other):
            os.utime(directory, (three_days_ago, three_days_ago))
        self.assertEqual(self.run_script("new").returncode, 0)
        self.assertEqual((old_report.exists(), old_other.exists(), young.exists()), (False, True, True))

    def test_no_verb_is_a_usage_error(self):
        self.assertEqual(self.run_script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
