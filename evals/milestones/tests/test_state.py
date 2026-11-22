import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import helpers
import checks
import fixtures
import state

UNIT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".cache"}
HOOK = helpers.PLUGIN_DIR / "hooks" / "milestones-reminder.sh"


def clean_env(**extra):
    """The environment of a child that must act only on its working directory: no MILESTONES_* or CLAUDE_* variable of the caller."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MILESTONES_", "CLAUDE_"))}
    return {**env, **state.GIT_ENV, **extra}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True, env=clean_env()).stdout


class Fixtures(unittest.TestCase):
    def test_every_fixture_builds_text_files_that_ignore_the_cache(self):
        for name in fixtures.FIXTURES:
            built = fixtures.build(name)
            self.assertTrue(all(isinstance(t, str) for t in built.values()), name)
            self.assertEqual(built[".gitignore"], ".cache/\n", name)

    def test_only_the_tracker_fixtures_have_a_tracker(self):
        self.assertNotIn(fixtures.TRACKER, fixtures.build("bare"))
        for name in ("tracker", "stale-task", "stale-idle"):
            self.assertIn(fixtures.TRACKER, fixtures.build(name))

    def test_the_tracker_fixture_has_the_names_the_prompts_use(self):
        items = {i["id"]: i for i in helpers.tracker("tracker")["items"]}
        self.assertEqual([i["state"] for i in items.values()], ["done", "doing", "todo", "todo"])
        self.assertEqual((items[2]["name"], items[3]["name"], items[4]["name"]), ("Storage layer", "Sync API", "Release sign-off"))

    def test_the_stale_task_names_what_the_prompt_builds(self):
        self.assertEqual({i["id"]: i["state"] for i in helpers.tracker("stale-task")["items"]}[2], "doing")
        self.assertEqual(helpers.tracker("stale-task")["items"][1]["name"], "Add slugify to textutil")

    def test_a_fixture_builds_the_same_text_across_a_second_boundary(self):
        first = fixtures.build("stale-task")
        time.sleep(1.1)
        self.assertEqual(fixtures.build("stale-task"), first)

    def test_stale_idle_is_the_tracker_fixture(self):
        self.assertEqual(helpers.tracker("stale-idle")["title"], helpers.tracker("tracker")["title"])

    def test_the_source_has_one_function_for_the_docstring_prompt(self):
        self.assertEqual(fixtures.build("bare")["src/textutil.py"].count("def "), 1)

    def test_no_live_tracker_or_instruction_file_is_in_this_eval_directory(self):
        # a .cache/milestones.json would be read by the plugin's hook and statusline, a CLAUDE.md or .claude/ by Claude Code, for anyone working here
        found = []
        for base, dirs, names in os.walk(UNIT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "artifacts")]
            found += [os.path.join(base, n) for n in dirs + names if n in FORBIDDEN]
        self.assertEqual(found, [])


class Workspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def prepare(self, name):
        (self.root / name).mkdir()
        return state.prepare(name, self.root / name), self.root / name

    def test_prepare_commits_a_tracked_repo_and_leaves_the_tracker_ignored(self):
        ws, _ = self.prepare("tracker")
        snap = state.snapshot(ws)
        self.assertEqual(len(snap["head"]), 40)
        self.assertEqual(snap["tracked"], [".gitignore", "README.md", "src/textutil.py"])
        self.assertEqual(snap["all_paths"], snap["tracked"])
        self.assertEqual(set(snap["files"]), set(fixtures.build("tracker")))
        self.assertEqual(git(ws, "status", "--porcelain"), "")

    def test_the_local_config_makes_the_agents_own_git_safe(self):
        ws, root = self.prepare("bare")
        config = {k: git(ws, "config", "--local", k).strip() for k in state.local_config(root / "hooks")}
        self.assertEqual(config["commit.gpgsign"], "false")
        self.assertEqual(config["tag.gpgsign"], "false")
        self.assertEqual(config["user.email"], "eval@example.invalid")
        hooks_path = Path(config["core.hooksPath"])
        self.assertEqual(hooks_path, root / "hooks")
        self.assertEqual(list(hooks_path.iterdir()), [])
        self.assertNotIn(ws, hooks_path.parents)
        self.assertEqual(git(ws, "symbolic-ref", "--short", "HEAD").strip(), "main")

    def test_a_commit_by_the_agent_needs_no_other_configuration(self):
        ws, _ = self.prepare("bare")
        (ws / "new.txt").write_text("x\n")
        git(ws, "add", "new.txt")
        git(ws, "commit", "-q", "-m", "x")
        self.assertIn("new.txt", state.snapshot(ws)["tracked"])

    def test_the_stale_fixtures_have_an_old_tracker_older_than_the_head_commit(self):
        for name in ("stale-task", "stale-idle"):
            ws, _ = self.prepare(name)
            mtime = (ws / fixtures.TRACKER).stat().st_mtime
            commit = int(git(ws, "log", "-1", "--format=%ct"))
            self.assertGreaterEqual(time.time() - mtime, 90 * 60, name)
            self.assertGreater(commit, mtime, name)

    def test_the_other_fixtures_keep_a_fresh_tracker(self):
        ws, _ = self.prepare("tracker")
        self.assertLess(time.time() - (ws / fixtures.TRACKER).stat().st_mtime, 300)

    def test_snapshot_records_lines_and_text(self):
        ws, _ = self.prepare("bare")
        record = state.snapshot(ws)["files"]["README.md"]
        self.assertEqual(record["lines"], 3)
        self.assertIn("textutil", record["text"])

    def test_snapshot_sees_edit_new_file_delete_commit_and_a_force_added_tracker(self):
        ws, _ = self.prepare("tracker")
        before = state.snapshot(ws)
        (ws / "README.md").write_text("short\n")
        (ws / "new.md").write_text("x\n")
        (ws / "src" / "textutil.py").unlink()
        self.assertEqual(checks.changed_paths(before, state.snapshot(ws)), ["README.md", "new.md", "src/textutil.py"])
        git(ws, "add", "-f", fixtures.TRACKER)
        git(ws, "commit", "-qm", "x")
        after = state.snapshot(ws)
        self.assertNotEqual(after["head"], before["head"])
        self.assertIn(fixtures.TRACKER, after["tracked"])
        self.assertIn(fixtures.TRACKER, after["all_paths"])

    def test_all_paths_keeps_a_path_that_a_later_commit_removed(self):
        ws, _ = self.prepare("tracker")
        git(ws, "add", "-f", fixtures.TRACKER)
        git(ws, "commit", "-qm", "add")
        git(ws, "rm", "-q", "--cached", fixtures.TRACKER)
        git(ws, "commit", "-qm", "remove")
        snap = state.snapshot(ws)
        self.assertNotIn(fixtures.TRACKER, snap["tracked"])
        self.assertIn(fixtures.TRACKER, snap["all_paths"])

    def test_only_the_cc_writes_marker_is_ignored(self):
        ws, _ = self.prepare("bare")
        (ws / ".claude" / ".cc-writes").mkdir(parents=True)
        (ws / ".claude" / ".cc-writes" / "marker").write_text("x")
        (ws / ".claude" / "settings.json").write_text("{}")
        files = state.snapshot(ws)["files"]
        self.assertNotIn(".claude/.cc-writes/marker", files)
        self.assertIn(".claude/settings.json", files)

    def test_a_workspace_that_lost_its_git_directory_still_snapshots(self):
        ws, _ = self.prepare("bare")
        shutil.rmtree(ws / ".git")
        snap = state.snapshot(ws)
        self.assertEqual((snap["head"], snap["tracked"], snap["all_paths"]), ("", [], []))
        self.assertIn("README.md", snap["files"])

    def test_snapshot_from_matches_snapshot(self):
        ws, _ = self.prepare("tracker")
        real = state.snapshot(ws)
        built = state.snapshot_from(fixtures.build("tracker"))
        self.assertEqual({p: r["sha256"] for p, r in built["files"].items()}, {p: r["sha256"] for p, r in real["files"].items()})
        self.assertEqual((built["tracked"], built["all_paths"]), (real["tracked"], real["all_paths"]))


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"), "needs jq and git")
class HookFiresOnTheFixtures(unittest.TestCase):
    """Fixture sanity, not a re-test of the hook (tests/milestones/test_reminder.py does that): the hook fires where the cases need it to."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def hook(self, name):
        root = Path(self._tmp.name).resolve() / name
        root.mkdir()
        ws = state.prepare(name, root)
        done = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True, cwd=ws, timeout=60, env=clean_env(CLAUDE_PROJECT_DIR=str(ws)))
        return done, ws

    def test_it_fires_on_both_stale_fixtures_and_touches_the_marker(self):
        for name in ("stale-task", "stale-idle"):
            done, ws = self.hook(name)
            self.assertEqual(done.returncode, 2, f"{name}: {done.stderr}")
            self.assertIn("MILESTONES HOOK", done.stderr)
            self.assertTrue((ws / fixtures.MARKER).is_file(), name)

    def test_it_stays_quiet_on_a_fresh_tracker(self):
        done, ws = self.hook("tracker")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((ws / fixtures.MARKER).exists())


if __name__ == "__main__":
    unittest.main()
