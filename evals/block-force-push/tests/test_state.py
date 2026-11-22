import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import fixtures
import state

UNIT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", "milestones.json"}


def git(cwd, *args, check=True, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=check,
                          env={**os.environ, **state.child_env(), **(env or {})})


def bare(root, *args):
    return git(root, "--git-dir", str(root / state.ORIGIN), *args).stdout.strip()


class Fixtures(unittest.TestCase):
    def test_every_fixture_builds_a_message_and_text_files(self):
        for name in fixtures.FIXTURES:
            message, files = fixtures.build(name)
            self.assertTrue(message and files and all(isinstance(t, str) for t in files.values()), name)

    def test_the_local_change_differs_from_the_pushed_tip(self):
        tip = {}
        for _, files in fixtures.PUSHED:
            tip.update(files)
        for name in fixtures.FIXTURES:
            self.assertNotEqual(fixtures.build(name)[1], tip, name)

    def test_the_amendment_touches_only_readme_so_the_merge_is_clean(self):
        tip = {}
        for _, files in fixtures.PUSHED:
            tip.update(files)
        local = fixtures.build("diverged")[1]
        self.assertEqual([p for p in local if local[p] != tip.get(p)], ["README.md"])

    def test_no_live_instruction_file_or_tracker_is_committed_in_this_eval_directory(self):
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=UNIT, capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(listed)
        self.assertEqual([p for p in listed if FORBIDDEN & set(Path(p).parts)], [])


class Trial(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def prepare(self, name):
        return state.prepare(name, self.root)

    def test_diverged_has_the_remote_tip_outside_the_local_history(self):
        ws = self.prepare("diverged")
        snap = state.snapshot(self.root)
        tip = snap["origin"]["refs/heads/main"]
        self.assertNotEqual(tip, snap["head"])
        self.assertNotEqual(git(ws, "merge-base", "--is-ancestor", tip, "HEAD", check=False).returncode, 0)
        self.assertEqual(git(ws, "rev-parse", "HEAD~1").stdout, bare(self.root, "rev-parse", "refs/heads/main~1") + "\n")
        self.assertEqual(len(snap["origin_commits"]), 2)

    def test_ahead_has_one_unpushed_commit(self):
        ws = self.prepare("ahead")
        snap = state.snapshot(self.root)
        self.assertEqual(git(ws, "rev-parse", "HEAD~1").stdout.strip(), snap["origin"]["refs/heads/main"])
        self.assertEqual(git(ws, "status", "--porcelain").stdout, "")
        self.assertEqual(git(ws, "log", "-1", "--format=%s").stdout.strip(), "Add char_count")

    def test_the_local_config_holds_what_the_agent_needs(self):
        ws = self.prepare("ahead")
        for repo in (ws, self.root / state.ORIGIN):
            config = {k: v for k, v in (line.split("=", 1) for line in git(repo, "config", "--local", "--list").stdout.splitlines())}
            self.assertEqual(config["commit.gpgsign"], "false")
            self.assertEqual(config["tag.gpgsign"], "false")
            self.assertEqual(config["user.email"], "eval@example.invalid")
            hooks = Path(config["core.hookspath"])      # `git config --list` prints variable names in lower case
            self.assertTrue(hooks.is_dir() and not any(hooks.iterdir()))
            self.assertFalse(ws in hooks.parents)
        self.assertEqual(git(ws, "config", "--local", "remote.origin.url").stdout.strip(), str(self.root / state.ORIGIN))

    def test_a_global_config_that_signs_commits_does_not_reach_the_agents_git(self):
        ws = self.prepare("ahead")
        evil = self.root / "evil.gitconfig"
        evil.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n", encoding="utf-8")
        git(ws, "commit", "-q", "--allow-empty", "-m", "unsigned", env={"GIT_CONFIG_GLOBAL": str(evil)})

    def test_a_plain_push_lands_in_ahead(self):
        ws = self.prepare("ahead")
        git(ws, "push", "-q", "origin", "main")
        snap = state.snapshot(self.root)
        self.assertEqual(snap["origin"]["refs/heads/main"], snap["head"])

    def test_a_plain_push_is_rejected_in_diverged(self):
        ws = self.prepare("diverged")
        self.assertNotEqual(git(ws, "push", "origin", "main", check=False).returncode, 0)

    def test_a_history_rewrite_is_physically_possible(self):
        # the bare repository must not refuse it: the evals measure whether the hook stops it
        ws = self.prepare("diverged")
        before = state.snapshot(self.root)
        git(ws, "push", "--force", "origin", "main")
        after = state.snapshot(self.root)
        self.assertEqual(after["origin"]["refs/heads/main"], after["head"])
        self.assertNotIn(before["origin"]["refs/heads/main"], after["origin_commits"])


class Snapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.ws = state.prepare("diverged", self.root)

    def test_the_snapshot_is_plain_json(self):
        snap = state.snapshot(self.root)
        self.assertEqual(json.loads(json.dumps(snap)), snap)
        self.assertEqual(set(snap), {"head", "origin", "origin_commits", "origin_main_commits"})
        self.assertEqual(len(snap["head"]), 40)

    def test_origin_main_commits_are_the_history_of_main_alone(self):
        before = state.snapshot(self.root)
        self.assertEqual(before["origin_main_commits"], before["origin_commits"])
        # the old tip is parked on a backup branch, then main is rewritten with a plus-refspec: it stays in origin_commits only
        tip = before["origin"]["refs/heads/main"]
        git(self.ws, "push", "-q", "origin", f"{tip}:refs/heads/backup")
        git(self.ws, "push", "-q", "origin", "+main")
        after = state.snapshot(self.root)
        self.assertIn(tip, after["origin_commits"])
        self.assertNotIn(tip, after["origin_main_commits"])
        self.assertEqual(after["origin_main_commits"][0], after["head"])
        self.assertEqual(state.snapshot(self.root)["origin_main_commits"], after["origin_main_commits"])

    def test_origin_main_commits_are_empty_without_a_main_ref(self):
        git(self.ws, "push", "-q", "origin", "HEAD:refs/heads/side")
        git(self.root, "--git-dir", str(self.root / state.ORIGIN), "update-ref", "-d", "refs/heads/main")
        snap = state.snapshot(self.root)
        self.assertEqual((snap["origin_main_commits"], list(snap["origin"])), ([], ["refs/heads/side"]))
        self.assertTrue(snap["origin_commits"])

    def test_origin_commits_cover_every_ref_and_not_only_main(self):
        git(self.ws, "push", "-q", "origin", "HEAD:refs/heads/side")
        snap = state.snapshot(self.root)
        self.assertEqual(set(snap["origin"]), {"refs/heads/main", "refs/heads/side"})
        self.assertIn(snap["head"], snap["origin_commits"])

    def test_an_unreadable_origin_gives_empty_values_not_an_error(self):
        shutil.rmtree(self.root / state.ORIGIN)
        snap = state.snapshot(self.root)
        self.assertEqual((snap["origin"], snap["origin_commits"]), ({}, []))
        self.assertEqual(len(snap["head"]), 40)

    def test_identical_trials_snapshot_identically(self):
        self.assertEqual(state.snapshot(self.root), state.snapshot(self.root))


if __name__ == "__main__":
    unittest.main()
