"""Tests for the Stop hook of the milestones plugin: hooks/milestones-reminder.sh and hooks/hooks.json."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "milestones"
HOOK = PLUGIN / "hooks" / "milestones-reminder.sh"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

TRACKER = {"title": "To X", "items": [
    {"id": 1, "name": "one", "state": "doing", "started": "2026-11-01T10:00:00+00:00"}]}


class RegistrationTest(unittest.TestCase):
    def test_hooks_json_registers_the_script_on_stop(self):
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        handlers = [h for group in data["hooks"]["Stop"] for h in group["hooks"]]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]["type"], "command")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", handlers[0]["command"])
        self.assertIn("hooks/milestones-reminder.sh", handlers[0]["command"])

    def test_the_script_is_executable_and_names_no_vault_path(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        self.assertNotIn(".claude/harness", HOOK.read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"), "needs jq and git")
class ReminderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reminder-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.org")
        self.git("config", "user.name", "T")
        (self.tmp / "a.txt").write_text("a")
        self.git("add", "a.txt")
        # the first commit is older than every tracker of these tests: a newer commit counts as activity
        long_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3 * 3600)) + " +0000"
        self.git("commit", "-q", "-m", "init", GIT_AUTHOR_DATE=long_ago, GIT_COMMITTER_DATE=long_ago)
        self.file = self.tmp / ".cache" / "milestones.json"
        self.file.parent.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("MILESTONES_", "CLAUDE_"))}
        self.env.update(CLAUDE_PROJECT_DIR=str(self.tmp))

    def git(self, *args, **env):
        subprocess.run(["git", "-C", str(self.tmp), *args], check=True, capture_output=True, env=dict(os.environ, **env))

    def tracker(self, data=TRACKER, age_minutes=60):
        self.file.write_text(json.dumps(data))
        old = time.time() - age_minutes * 60
        os.utime(self.file, (old, old))

    def run_hook(self, stdin="{}", **env):
        return subprocess.run(["bash", str(HOOK)], input=stdin, capture_output=True, text=True,
                              env=dict(self.env, **env), timeout=60)

    def dirty(self):
        (self.tmp / "b.txt").write_text("b")

    def test_a_stale_tracker_with_repository_activity_blocks_with_a_report(self):
        self.tracker()
        self.dirty()
        r = self.run_hook()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("MILESTONES HOOK", r.stderr)
        self.assertIn("doing: #1 one", r.stderr)
        self.assertIn("/milestones", r.stderr)
        self.assertNotIn("harness", r.stderr)

    def test_it_reminds_once_per_stale_window(self):
        self.tracker()
        self.dirty()
        self.assertEqual(self.run_hook().returncode, 2)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_a_fresh_tracker_is_left_alone(self):
        self.tracker(age_minutes=1)
        self.dirty()
        self.assertEqual(self.run_hook().returncode, 0)

    def test_no_activity_no_reminder(self):
        self.tracker()
        self.assertEqual(self.run_hook().returncode, 0)

    def test_nothing_doing_no_reminder(self):
        self.tracker({"title": "To X", "items": [{"id": 1, "name": "one", "state": "todo"}]})
        self.dirty()
        self.assertEqual(self.run_hook().returncode, 0)

    def test_no_tracker_no_reminder(self):
        self.dirty()
        self.assertEqual(self.run_hook().returncode, 0)

    def test_stop_hook_active_is_skipped(self):
        self.tracker()
        self.dirty()
        self.assertEqual(self.run_hook(json.dumps({"stop_hook_active": True})).returncode, 0)

    def test_the_stale_window_is_configurable(self):
        self.tracker(age_minutes=10)
        self.dirty()
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertEqual(self.run_hook(MILESTONES_STALE_MINUTES="5").returncode, 2)

    def test_cache_changes_do_not_count_as_activity(self):
        self.tracker()
        (self.tmp / ".cache" / "other.txt").write_text("x")   # only .cache changed
        self.assertEqual(self.run_hook().returncode, 0)


if __name__ == "__main__":
    unittest.main()
