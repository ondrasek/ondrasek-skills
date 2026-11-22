"""The fixtures are built for real, in temporary repositories, and have the topology the checks assume."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import checks
import fixtures
import state

UNIT = Path(__file__).resolve().parents[1]
HOOK = helpers.UNIT.parents[1] / "plugins" / "auto-commit" / "hooks" / "auto-commit.sh"
FORBIDDEN = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".cache"}
SECRETISH = re.compile(r"secret|token|passw|api[_-]?key|credential|private|BEGIN [A-Z ]*KEY|AKIA", re.IGNORECASE)


def sh(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))} | state.GIT_ISOLATION
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout


class Fixtures(unittest.TestCase):
    def test_every_fixture_builds_text_files(self):
        for name in fixtures.FIXTURES:
            built = fixtures.build(name)
            self.assertTrue(built and all(isinstance(t, str) for t in built.values()), name)

    def test_no_fixture_text_looks_like_a_secret(self):
        # gitleaks is installed on some machines only: it must never change what the hook reports
        for name in fixtures.FIXTURES:
            for path, text in fixtures.build(name).items():
                self.assertIsNone(SECRETISH.search(text), f"{name}:{path}")

    def test_no_instruction_file_or_tracker_is_a_fixture(self):
        for name in fixtures.FIXTURES:
            self.assertFalse(FORBIDDEN & {part for path in fixtures.build(name) for part in Path(path).parts}, name)

    def test_no_live_instruction_file_is_committed_in_this_eval_directory(self):
        # a CLAUDE.md or AGENTS.md here would load for anyone working in the repo
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=UNIT, capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(listed)
        self.assertEqual([p for p in listed if FORBIDDEN & set(Path(p).parts)], [])


class Topology(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def build(self, name):
        self.ws, self.origin = state.prepare(name, self.root)
        return state.snapshot(self.ws, self.origin)

    def test_synced_is_clean_and_equal_to_origin(self):
        snap = self.build("synced")
        self.assertEqual((snap["status"], len(snap["log"]), snap["remotes"]), ("", 2, ["origin"]))
        self.assertEqual(snap["origin"], {checks.MAIN: snap["head"]})
        self.assertEqual(sh(self.ws, "rev-list", "--left-right", "--count", "HEAD...origin/main").split(), ["0", "0"])
        self.assertEqual(sh(self.ws, "rev-parse", "--abbrev-ref", "main@{upstream}").strip(), "origin/main")

    def test_dirty_has_one_unstaged_edit_and_one_untracked_file(self):
        snap = self.build("dirty")
        self.assertEqual(sorted(snap["status"].splitlines()), [" M src/textutil.py", "?? notes/todo.md"])

    def test_behind_is_one_ahead_one_behind_after_the_fetch(self):
        snap = self.build("behind")
        self.assertEqual(sh(self.ws, "rev-list", "--left-right", "--count", "HEAD...origin/main").split(), ["1", "1"])
        self.assertEqual(snap["status"], "?? notes/todo.md\n")
        self.assertNotIn(snap["origin"][checks.MAIN], {c["sha"] for c in snap["log"]})
        sh(self.ws, "merge-tree", "--write-tree", "HEAD", "origin/main")     # exits 1 on a conflict (git 2.38 or newer)
        self.assertFalse((self.root / "clone").exists())

    def test_env_has_an_untracked_env_file_that_is_not_ignored(self):
        snap = self.build("env")
        self.assertIn("?? .env", snap["status"].splitlines())
        self.assertIn(".env", sh(self.ws, "ls-files", "--others", "--exclude-standard").split())

    def test_no_remote_has_no_remote_and_no_origin_snapshot(self):
        snap = self.build("no-remote")
        self.assertEqual((snap["remotes"], snap["origin"], self.origin), ([], {}, None))
        self.assertFalse((self.root / "origin.git").exists())

    def test_the_local_config_makes_the_agent_able_to_commit_without_any_global_config(self):
        self.build("dirty")
        sh(self.ws, "add", "-A")
        sh(self.ws, "commit", "-q", "-m", "x")     # identity, signing and hooks all come from the local config
        self.assertEqual(sh(self.ws, "config", "--local", "core.excludesFile"), str(self.root / "excludes") + "\n")
        self.assertEqual(list((self.root / "hooks").iterdir()), [])

    def test_the_invented_before_snapshots_match_the_real_ones(self):
        # the good runs of every check test are built from these; a drift here would make them test a fiction
        for name in fixtures.FIXTURES:
            with self.subTest(fixture=name), tempfile.TemporaryDirectory() as tmp:
                ws, origin = state.prepare(name, Path(tmp).resolve())
                real, invented = state.snapshot(ws, origin), helpers.before_snapshot(name)
                self.assertEqual(sorted(real["status"].splitlines()), sorted(invented["status"].splitlines()))
                self.assertEqual(real["remotes"], invented["remotes"])
                self.assertEqual(real["tracked"].keys(), invented["tracked"].keys())
                self.assertEqual([len(c["parents"]) for c in real["log"]], [len(c["parents"]) for c in invented["log"]])
                self.assertEqual((len(real["origin_commits"]), sorted(real["all_paths"]), sorted(real["origin_paths"])),
                                 (len(invented["origin_commits"]), invented["all_paths"], invented["origin_paths"]))
                self.assertEqual({p: r["sha256"] for p, r in real["files"].items()}, {p: r["sha256"] for p, r in invented["files"].items()})


class Snapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.ws, self.origin = state.prepare("synced", self.root)

    def test_snapshot_records_history_tree_and_remote(self):
        snap = state.snapshot(self.ws, self.origin)
        self.assertEqual(len(snap["head"]), 40)
        self.assertEqual([c["subject"] for c in snap["log"]], ["feat: add shout", "docs: add readme"])
        self.assertEqual(snap["log"][0]["parents"], [snap["log"][1]["sha"]])
        self.assertEqual(snap["tracked"].keys(), set(fixtures.BASE))
        self.assertEqual(snap["all_paths"], sorted(fixtures.BASE))
        self.assertEqual(snap["origin_paths"], sorted(fixtures.BASE))
        self.assertEqual(snap["origin_commits"], [c["sha"] for c in snap["log"]])
        self.assertFalse([p for p in snap["files"] if p.startswith(".git/")])

    def test_tracked_reads_the_head_tree_not_the_index(self):
        before = state.snapshot(self.ws)
        (self.ws / "new.txt").write_text("x\n")
        sh(self.ws, "add", "new.txt")
        staged = state.snapshot(self.ws)
        self.assertEqual((staged["tracked"], staged["status"]), (before["tracked"], "A  new.txt\n"))

    def test_all_paths_keeps_a_deleted_file_and_the_snapshot_lists_untracked_files_one_by_one(self):
        (self.ws / "gone.txt").write_text("x\n")
        sh(self.ws, "add", "gone.txt")
        sh(self.ws, "commit", "-q", "-m", "x")
        sh(self.ws, "rm", "-q", "gone.txt")
        sh(self.ws, "commit", "-q", "-m", "y")
        (self.ws / "dir").mkdir()
        (self.ws / "dir" / "a.txt").write_text("a\n")
        snap = state.snapshot(self.ws)
        self.assertIn("gone.txt", snap["all_paths"])
        self.assertNotIn("gone.txt", snap["tracked"])
        self.assertEqual(snap["status"], "?? dir/a.txt\n")

    def test_claude_code_bookkeeping_and_the_cache_are_invisible(self):
        (self.ws / ".claude" / ".cc-writes").mkdir(parents=True)
        (self.ws / ".claude" / ".cc-writes" / "marker").write_text("x")
        (self.ws / ".cache").mkdir()
        (self.ws / ".cache" / "milestones.json").write_text("{}")
        self.assertFalse([p for p in state.snapshot(self.ws)["files"] if p.startswith((".claude", ".cache"))])
        self.assertEqual(checks.status_paths("?? .claude/.cc-writes/marker\n?? .cache/milestones.json\n?? a.txt\n"), ["a.txt"])

    def test_a_missing_repository_gives_an_empty_snapshot_with_the_files(self):
        plain = self.root / "plain"
        plain.mkdir()
        (plain / "a.txt").write_text("a\n")
        snap = state.snapshot(plain)
        self.assertEqual((snap["head"], snap["log"], list(snap["files"])), ("", [], ["a.txt"]))

    def test_snapshot_from_has_the_shape_of_snapshot(self):
        real = state.snapshot(self.ws, self.origin)
        invented = state.snapshot_from(fixtures.BASE)
        self.assertEqual(set(real), set(invented))
        self.assertEqual(real["tracked"], invented["tracked"])
        self.assertEqual({p: r["sha256"] for p, r in real["files"].items()}, {p: r["sha256"] for p, r in invented["files"].items()})
        with self.assertRaises(KeyError):
            state.snapshot_from({}, nonsense=1)

    def test_git_variables_of_the_caller_are_ignored(self):
        # e.g. the suite is run from inside a git hook, which sets GIT_DIR
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "nowhere"), "GIT_WORK_TREE": str(self.root / "nowhere")}):
            self.assertEqual(len(state.snapshot(self.ws, self.origin)["head"]), 40)

    def test_a_branch_a_tag_and_a_config_change_show_in_the_snapshot_although_head_does_not_move(self):
        before = state.snapshot(self.ws, self.origin)
        self.assertIn("refs/heads/main", before["refs"])
        self.assertIn("hooksPath", before["config"])
        self.assertFalse(any(checks.diff_states(before, state.snapshot(self.ws, self.origin))))      # nothing changed: no difference
        for change in (("branch", "scratch"), ("tag", "v-eval"), ("config", "--local", "user.name", "other")):
            with self.subTest(change=change):
                fresh = tempfile.TemporaryDirectory()
                self.addCleanup(fresh.cleanup)
                ws, origin = state.prepare("synced", Path(fresh.name).resolve())
                start = state.snapshot(ws, origin)
                sh(ws, *change)
                after = state.snapshot(ws, origin)
                self.assertEqual(after["head"], start["head"])
                self.assertTrue(any(checks.diff_states(start, after)), change)

    def test_the_snapshot_does_not_run_a_program_named_in_the_config_the_agent_could_write(self):
        marker = self.root / "ran-on-the-host"
        sh(self.ws, "config", "--local", "core.fsmonitor", f"touch {marker}; false")
        sh(self.ws, "config", "--local", "core.hooksPath", str(self.root / "hooks"))
        # the control: git itself runs the program, so a snapshot that did not switch it off would too
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | state.GIT_ISOLATION
        subprocess.run(["git", "status", "--porcelain"], cwd=self.ws, capture_output=True, env=env)
        if not marker.exists():
            self.skipTest("this git does not run core.fsmonitor on status: nothing to guard against")
        marker.unlink()
        state.snapshot(self.ws, self.origin)
        self.assertFalse(marker.exists(), "state.snapshot ran core.fsmonitor on the host")

    def test_the_snapshot_does_not_run_a_clean_filter_the_agent_could_write(self):
        # a clean filter named in .git/config and selected by .gitattributes: git runs it when it re-reads a stat-dirty tracked file,
        # on `git status` (and diff, ls-files --modified). The snapshot hashes the files itself.
        for variant in ("newer mtime", "same size, other content"):
            with self.subTest(variant=variant):
                fresh = tempfile.TemporaryDirectory()
                self.addCleanup(fresh.cleanup)
                root = Path(fresh.name).resolve()
                ws, origin = state.prepare("synced", root)
                marker = root / "ran-on-the-host"
                sh(ws, "config", "--local", "filter.pwn.clean", f"touch {marker}; cat")
                (ws / ".gitattributes").write_text("*.py filter=pwn\n")
                target = ws / "src" / "textutil.py"
                text = target.read_text()
                if variant == "newer mtime":
                    stat = target.stat()
                    os.utime(target, (stat.st_atime + 5, stat.st_mtime + 5))
                else:
                    target.write_text(text[:-1] + ("#" if text[-1] != "#" else "%"))
                # the control: git itself runs the filter, so a snapshot that went through git status would too
                env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | state.GIT_ISOLATION
                subprocess.run(["git", "status", "--porcelain"], cwd=ws, capture_output=True, env=env)
                if not marker.exists():
                    self.skipTest("this git does not run the clean filter on status here: nothing to guard against")
                marker.unlink()
                state.snapshot(ws, origin)
                self.assertFalse(marker.exists(), "state.snapshot ran a clean filter on the host")

    def test_the_status_text_is_what_git_status_prints(self):
        # git status with renames off is the reference: every shape the checks read (M, A, D, AM, MM, untracked, ignored) in one repository
        (self.ws / ".gitignore").write_text("ignored.txt\n")
        (self.ws / "extra.txt").write_text("x\n")
        sh(self.ws, "add", ".gitignore", "extra.txt")
        sh(self.ws, "commit", "-q", "-m", "ignore")
        (self.ws / "ignored.txt").write_text("x\n")
        (self.ws / "README.md").write_text("changed in the worktree only\n")              # " M"
        (self.ws / "staged.txt").write_text("a\n")
        sh(self.ws, "add", "staged.txt")                                                   # "A "
        (self.ws / "both.txt").write_text("a\n")
        sh(self.ws, "add", "both.txt")
        (self.ws / "both.txt").write_text("b\n")                                           # "AM"
        sh(self.ws, "rm", "-q", "src/textutil.py")                                         # "D "
        (self.ws / "sub" / "deeper").mkdir(parents=True)
        (self.ws / "sub" / "deeper" / "new.txt").write_text("n\n")                         # "??", listed per file
        gone = "extra.txt"
        (self.ws / gone).unlink()                                                          # " D"
        snap = state.snapshot(self.ws)
        reference = sh(self.ws, "status", "--porcelain", "--untracked-files=all", "--no-renames")
        self.assertEqual(snap["status"].splitlines(), reference.splitlines())
        for shape in (" M README.md", "A  staged.txt", "AM both.txt", "D  src/textutil.py", f" D {gone}", "?? sub/deeper/new.txt"):
            self.assertIn(shape, snap["status"].splitlines())
        self.assertNotIn("ignored.txt", snap["status"])

    def test_the_status_text_shows_a_conflict_and_a_staged_modification(self):
        (self.ws / "README.md").write_text("staged\n")
        sh(self.ws, "add", "README.md")
        self.assertEqual(state.snapshot(self.ws)["status"], "M  README.md\n")
        sh(self.ws, "reset", "-q", "--hard")
        sh(self.ws, "checkout", "-q", "-b", "other")
        (self.ws / "README.md").write_text("other\n")
        sh(self.ws, "commit", "-q", "-am", "other")
        sh(self.ws, "checkout", "-q", "main")
        (self.ws / "README.md").write_text("main\n")
        sh(self.ws, "commit", "-q", "-am", "main")
        subprocess.run(["git", "merge", "other"], cwd=self.ws, capture_output=True, env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | state.GIT_ISOLATION)
        self.assertEqual(state.snapshot(self.ws)["status"], "UU README.md\n")

    def test_an_env_commit_the_agent_dropped_is_still_in_all_paths(self):
        # commit .env, undo the commit, unstage .env, commit again: no ref reaches the first commit any more (git log --all misses it)
        (self.ws / ".env").write_text("MODE=development\n")
        sh(self.ws, "add", "-A")
        sh(self.ws, "commit", "-q", "-m", "chore: wip")
        sh(self.ws, "reset", "-q", "--soft", "HEAD~1")
        sh(self.ws, "reset", "-q", "--", ".env")
        sh(self.ws, "commit", "-q", "-m", "chore: wip again", "--allow-empty")
        self.assertNotIn(".env", sh(self.ws, "log", "--all", "--name-only", "--format=").split())
        self.assertIn(".env", state.snapshot(self.ws, self.origin)["all_paths"])

    def test_an_env_file_pushed_to_a_branch_that_was_deleted_is_still_in_origin_paths(self):
        (self.ws / ".env").write_text("MODE=development\n")
        sh(self.ws, "add", "-A")
        sh(self.ws, "commit", "-q", "-m", "chore: wip")
        sh(self.ws, "push", "-q", "origin", "HEAD:refs/heads/scratch")
        sh(self.ws, "push", "-q", "origin", ":refs/heads/scratch")
        snap = state.snapshot(self.ws, self.origin)
        self.assertNotIn("refs/heads/scratch", snap["origin"])
        self.assertIn(".env", snap["origin_paths"])

    def test_a_symlink_to_a_file_outside_the_workspace_is_not_read(self):
        outside = self.root / "outside.txt"
        outside.write_text("private\n")
        (self.ws / "link.txt").symlink_to(outside)
        (self.ws / "linkdir").symlink_to(self.root / "hooks", target_is_directory=True)
        (self.root / "hooks" / "inner.txt").write_text("x\n")
        files = state.snapshot(self.ws, self.origin)["files"]
        self.assertNotIn("link.txt", files)
        self.assertFalse([p for p in files if p.startswith("linkdir")])
        self.assertNotIn("private", str(files))

    def test_blob_id_is_gits(self):
        done = subprocess.run(["git", "hash-object", "--stdin"], input=b"hello\n", capture_output=True, check=True, cwd=self.ws)
        self.assertEqual(state.blob_id(b"hello\n"), done.stdout.decode().strip())


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
class RealHook(unittest.TestCase):
    """A fixture sanity check, not a re-test of the script (tests/auto-commit does that): the hook finds what each fixture plants."""

    @classmethod
    def setUpClass(cls):
        cls.done = {}
        for name in fixtures.FIXTURES:
            with tempfile.TemporaryDirectory() as tmp:
                ws, _ = state.prepare(name, Path(tmp).resolve())
                env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))} | state.GIT_ISOLATION
                cls.done[name] = subprocess.run(["bash", str(HOOK)], input="{}", cwd=ws, env=env, capture_output=True, text=True, timeout=60)

    def test_the_hook_is_silent_on_synced_and_blocks_on_every_other_fixture(self):
        self.assertEqual((self.done["synced"].returncode, self.done["synced"].stderr), (0, ""))
        for name in ("dirty", "behind", "env", "no-remote"):
            with self.subTest(fixture=name):
                self.assertEqual(self.done[name].returncode, 2, self.done[name].stderr)
                self.assertIn(checks.HOOK_MARK, self.done[name].stderr)
                self.assertNotIn("gitleaks", self.done[name].stderr)

    def test_the_hook_reports_what_the_fixtures_plant(self):
        self.assertIn("1 commit(s) on origin/main not merged locally", self.done["behind"].stderr)
        self.assertIn("1 commit(s) not pushed to origin/main", self.done["behind"].stderr)
        self.assertIn(".env file(s) tracked or not gitignored:", self.done["env"].stderr)
        self.assertIn("no remote branch origin/main", self.done["no-remote"].stderr)
        self.assertIn("unstaged changes:", self.done["dirty"].stderr)


if __name__ == "__main__":
    unittest.main()
