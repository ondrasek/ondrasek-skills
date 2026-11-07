"""Tests for the Stop hook of the auto-commit plugin: hooks/auto-commit.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "auto-commit"
HOOK = PLUGIN / "hooks" / "auto-commit.sh"


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"), "needs jq and git")
class AutoCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-commit-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remote = self.tmp / "remote.git"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
        self.env.update(HOME=str(self.tmp), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        CLAUDE_PROJECT_DIR=str(self.repo))
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True, env=self.env)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.org")
        self.git("config", "user.name", "T")
        self.git("remote", "add", "origin", str(self.remote))
        (self.repo / "a.txt").write_text("a")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "init")
        self.git("push", "-q", "-u", "origin", "main")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, env=self.env)

    def run_hook(self, stdin="{}", cwd=None, **env):
        return subprocess.run(["bash", str(HOOK)], input=stdin, capture_output=True, text=True,
                              cwd=cwd or self.repo, env=dict(self.env, **env), timeout=60)

    def test_clean_and_in_sync_is_silent(self):
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_an_untracked_file_blocks_with_state_goal_and_guardrails(self):
        (self.repo / "new.txt").write_text("n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)
        for piece in ("AUTO-COMMIT HOOK", "untracked files (not gitignored):", "new.txt",
                      "Goal: working tree clean and main in sync with origin/main.",
                      "Never force-push", "Never rebase", "Co-Authored-By"):
            self.assertIn(piece, r.stderr)

    def test_an_unstaged_change_is_reported(self):
        (self.repo / "a.txt").write_text("changed")
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)
        self.assertIn("unstaged changes:", r.stderr)

    def test_a_staged_change_is_reported(self):
        (self.repo / "a.txt").write_text("changed")
        self.git("add", "a.txt")
        r = self.run_hook()
        self.assertIn("staged but uncommitted changes:", r.stderr)

    def test_an_unpushed_commit_is_reported(self):
        (self.repo / "b.txt").write_text("b")
        self.git("add", "b.txt")
        self.git("commit", "-q", "-m", "local")
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)
        self.assertIn("1 commit(s) not pushed to origin/main", r.stderr)

    def test_remote_changes_not_merged_are_reported(self):
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True, env=self.env)
        for cfg in (("user.email", "o@example.org"), ("user.name", "O")):
            subprocess.run(["git", "-C", str(other), "config", *cfg], check=True, env=self.env)
        (other / "c.txt").write_text("c")
        for args in (("add", "c.txt"), ("commit", "-q", "-m", "remote"), ("push", "-q", "origin", "main")):
            subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True, env=self.env)
        self.git("fetch", "-q")
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)
        self.assertIn("1 commit(s) on origin/main not merged locally", r.stderr)

    def test_an_env_file_is_reported_and_push_is_forbidden(self):
        (self.repo / ".env").write_text("SECRET=1")
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)
        self.assertIn(".env file(s) tracked or not gitignored:", r.stderr)
        self.assertIn("Do not push while a .env file", r.stderr)

    def test_env_example_is_allowed_but_still_an_untracked_file(self):
        (self.repo / ".env.example").write_text("SECRET=")
        r = self.run_hook()
        self.assertNotIn(".env file(s) tracked", r.stderr)
        self.assertIn("untracked files", r.stderr)

    def test_a_gitignored_env_file_is_fine(self):
        (self.repo / ".gitignore").write_text(".env\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore env")
        self.git("push", "-q")
        (self.repo / ".env").write_text("SECRET=1")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_cache_changes_are_never_reported(self):
        (self.repo / ".cache").mkdir()
        (self.repo / ".cache" / "milestones.json").write_text("{}")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_stop_hook_active_lets_the_turn_end(self):
        (self.repo / "new.txt").write_text("n")
        r = self.run_hook(json.dumps({"stop_hook_active": True}))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_outside_a_repository_is_silent(self):
        outside = self.tmp / "plain"
        outside.mkdir()
        r = self.run_hook(cwd=outside, CLAUDE_PROJECT_DIR=str(outside))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_without_a_remote_branch_the_goal_is_a_local_commit(self):
        solo = self.tmp / "solo"
        solo.mkdir()
        subprocess.run(["git", "-C", str(solo), "init", "-q", "-b", "main"], check=True, env=self.env)
        (solo / "x.txt").write_text("x")
        r = self.run_hook(cwd=solo, CLAUDE_PROJECT_DIR=str(solo))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no remote branch origin/main", r.stderr)
        self.assertIn("do not push or create one", r.stderr)

    def test_registration_and_script_are_sound(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        handlers = [h for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(handlers), 1)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", handlers[0]["command"])
        self.assertIn("hooks/auto-commit.sh", handlers[0]["command"])
        text = HOOK.read_text(encoding="utf-8")
        for personal in ("Ondra", "~/.gitconfig", "~/.claude"):
            self.assertNotIn(personal, text)


if __name__ == "__main__":
    unittest.main()
