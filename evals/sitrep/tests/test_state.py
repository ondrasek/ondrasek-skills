import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers
import checks
import fixtures
import state

UNIT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).resolve().parents[3] / "plugins" / "sitrep" / "skills" / "sitrep" / "template.html"
INSTRUCTION_FILES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"}


def live_files(root):
    """Paths under `root` that would load or be read as live instructions or a live tracker. Claude Code's own `.claude/.cc-writes`
    marker (it appears in a directory a session wrote in, and is git-ignored), `artifacts` and `__pycache__` are not looked at."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "artifacts", ".cc-writes")]
        here = Path(dirpath)
        for name in filenames + dirnames:
            path = here / name
            live = name in INSTRUCTION_FILES or (name == "milestones.json" and here.name == ".cache")
            live = live or (name == ".claude" and (path.is_file() or any(c.name != ".cc-writes" for c in path.iterdir())))
            if live:
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **state.GIT_ENV}).stdout.strip()


class Fixtures(unittest.TestCase):
    def test_every_fixture_builds_text_files(self):
        for name in fixtures.FIXTURES:
            built = fixtures.build(name)
            self.assertTrue(built and all(isinstance(t, str) for t in built.values()), name)

    def test_every_planted_item_is_in_the_open_questions(self):
        lines = fixtures.build("open-items")["docs/open-questions.md"].splitlines()
        for key, pattern in fixtures.PLANTED.items():
            hits = [line for line in lines if re.search(pattern, line.lower())]
            self.assertTrue(hits, key)
        self.assertEqual(sum(1 for line in lines if re.match(r"\d\. ", line)), 5)

    def test_the_planted_dependency_is_written_down(self):
        text = fixtures.build("open-items")["docs/open-questions.md"]
        self.assertRegex(text, r"(?i)migration cannot start until the database is chosen")

    def test_the_readme_points_to_the_open_questions_and_bare_has_none(self):
        self.assertIn("docs/open-questions.md", fixtures.build("open-items")["README.md"])
        bare = fixtures.build("bare")
        self.assertEqual(sorted(bare), ["README.md", "src/textutil.py"])
        for text in bare.values():
            for pattern in fixtures.PLANTED.values():
                self.assertIsNone(re.search(pattern, text.lower()))

    def test_the_milestone_fixture_has_the_tracker_the_checks_expect(self):
        built = fixtures.build("open-items-ms")
        self.assertEqual(set(built) - set(fixtures.build("open-items")), {".gitignore", ".cache/milestones.json"})
        self.assertIn(".cache/", built[".gitignore"].splitlines())
        tracker = json.loads(built[".cache/milestones.json"])
        self.assertEqual([(i["id"], i["state"]) for i in tracker["items"]], [(1, "done"), (2, "doing"), (3, "blocked"), (4, "todo")])
        self.assertEqual((tracker["items"][2]["needs"], tracker["items"][1]["name"]), ("you", "Storage layer"))

    def test_no_live_instruction_file_or_tracker_is_kept_in_this_eval_directory(self):
        # a CLAUDE.md or AGENTS.md here would load for anyone working in the repo; fixtures are built in code, in a temporary directory
        self.assertEqual(live_files(UNIT), [])

    def test_the_search_for_live_files_finds_them_and_ignores_runtime_leftovers(self):
        def tree(*paths):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            for rel in paths:
                (Path(tmp.name) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp.name) / rel).write_text("x")
            return live_files(tmp.name)

        self.assertEqual(tree("CLAUDE.md", "docs/AGENTS.md", ".claude/settings.json", ".cache/milestones.json", "src/milestones.json"),
                         [".cache/milestones.json", ".claude", "CLAUDE.md", "docs/AGENTS.md"])
        self.assertEqual(tree(".claude/.cc-writes/marker", "artifacts/CLAUDE.md", "tests/__pycache__/CLAUDE.md", "src/milestones.json"), [])


class Workspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.layout = state.prepare("open-items-ms", self.root)
        self.ws = Path(self.layout["ws"])

    def test_prepare_lays_out_ws_tmp_and_hooks_under_the_resolved_root(self):
        self.assertEqual(self.layout, {"root": str(self.root.resolve()), "ws": str(self.root.resolve() / "ws"), "tmp": str(self.root.resolve() / "tmp")})
        self.assertEqual(os.listdir(self.layout["tmp"]), [])
        self.assertEqual(os.listdir(self.root / "hooks"), [])

    def test_prepare_commits_a_clean_repo_that_is_not_the_one_the_harness_lives_in(self):
        snap = state.snapshot(self.ws)
        self.assertEqual(snap["status"], "")
        self.assertEqual(len(snap["head"]), 40)
        self.assertEqual(git(self.ws, "rev-parse", "--show-toplevel"), str(self.ws.resolve()))
        self.assertEqual(git(self.ws, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(git(self.ws, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_the_tracker_is_snapshotted_but_ignored_by_git(self):
        self.assertIn(".cache/milestones.json", state.snapshot(self.ws)["files"])
        self.assertNotIn(".cache/milestones.json", git(self.ws, "ls-files").splitlines())

    def test_the_repos_own_config_carries_identity_signing_and_an_empty_hooks_directory(self):
        local = {k: git(self.ws, "config", "--local", k) for k in ("user.name", "user.email", "commit.gpgsign", "tag.gpgsign", "core.hooksPath")}
        self.assertEqual(local, {"user.name": "eval", "user.email": "eval@example.invalid", "commit.gpgsign": "false", "tag.gpgsign": "false",
                                 "core.hooksPath": str(self.root.resolve() / "hooks")})

    def test_snapshot_records_lines_and_text(self):
        record = state.snapshot(self.ws)["files"]["README.md"]
        self.assertEqual(record["lines"], 3)
        self.assertIn("docs/open-questions.md", record["text"])

    def test_snapshot_sees_edit_new_file_delete_and_commit(self):
        before = state.snapshot(self.ws)
        (self.ws / "README.md").write_text("short\n")
        (self.ws / "new.md").write_text("x\n")
        (self.ws / "src" / "textutil.py").unlink()
        after = state.snapshot(self.ws)
        self.assertEqual(checks.diff_states(before, after)[:3], (["new.md"], ["src/textutil.py"], ["README.md"]))
        git(self.ws, "commit", "-qam", "x")
        self.assertTrue(checks.diff_states(before, state.snapshot(self.ws))[3])

    def test_only_the_cc_writes_marker_is_ignored(self):
        (self.ws / ".claude" / ".cc-writes").mkdir(parents=True)
        (self.ws / ".claude" / ".cc-writes" / "marker").write_text("x")
        (self.ws / ".claude" / "settings.json").write_text("{}")
        files = state.snapshot(self.ws)["files"]
        self.assertNotIn(".claude/.cc-writes/marker", files)
        self.assertIn(".claude/settings.json", files)

    def test_identical_snapshots_have_no_diff(self):
        self.assertEqual(checks.diff_states(state.snapshot(self.ws), state.snapshot(self.ws)), ([], [], [], False))

    def test_snapshot_from_matches_snapshot(self):
        built = state.snapshot_from(fixtures.build("open-items-ms"))
        real = state.snapshot(self.ws)
        self.assertEqual({p: r["sha256"] for p, r in built["files"].items()}, {p: r["sha256"] for p, r in real["files"].items()})


class FindReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = str(Path(self._tmp.name).resolve())

    def made(self, name="sitrep.Ab12Cd"):
        path = os.path.join(self.tmp, name)
        os.mkdir(path)
        return path

    def events(self, command, output):
        return helpers.calls([("Skill", {"skill": helpers.SKILL}), ("Bash", {"command": command}, output)])

    def test_the_printed_directory_is_found_from_the_quoted_spelling(self):
        path = self.made()
        self.assertEqual(state.find_report(self.events(helpers.NEW, path + "\n"), self.tmp), (path, "trace"))

    def test_a_list_of_text_blocks_is_read_too(self):
        path = self.made()
        events = self.events(helpers.NEW, [{"type": "text", "text": f"{path}\n"}])
        self.assertEqual(state.find_report(events, self.tmp), (path, "trace"))

    def test_the_trace_wins_over_another_directory_in_tmpdir(self):
        self.made("sitrep.Old111")
        path = self.made("sitrep.New222")
        self.assertEqual(state.find_report(self.events(helpers.NEW, path), self.tmp), (path, "trace"))

    def test_a_path_printed_by_another_command_is_not_the_report(self):
        path = self.made()
        found, how = state.find_report(self.events("ls /somewhere", "/elsewhere/sitrep.Zz9Zz9\n"), self.tmp)
        self.assertEqual((found, how), (path, "glob"))

    def test_the_glob_is_the_fallback_and_ignores_files_and_other_names(self):
        path = self.made()
        self.made("other.Ab12Cd")
        Path(self.tmp, "sitrep.file").write_text("x")
        self.assertEqual(state.find_report([], self.tmp), (path, "glob"))
        self.assertEqual(state.sitrep_dirs(self.tmp), ["sitrep.Ab12Cd"])

    def test_a_printed_directory_that_is_gone_is_the_last_resort(self):
        gone = os.path.join(self.tmp, "sitrep.Gone11")
        self.assertEqual(state.find_report(self.events(helpers.NEW, gone), self.tmp), (gone, "trace, but the directory is gone"))

    def test_nothing_found(self):
        self.assertEqual(state.find_report([], self.tmp), (None, "not found"))

    def test_a_result_with_no_sitrep_path_in_it_is_not_a_report(self):
        for output in ("mktemp: failed to create directory\n", "/r/tmp/report\n", "/r/tmp/sitrep.\n", "refusing to delete: /r/tmp/other\n"):
            self.assertEqual(state.find_report(self.events(helpers.NEW, output), self.tmp), (None, "not found"), output)


class ReportSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "sitrep.Ab12Cd"
        self.dir.mkdir()
        shutil.copy(TEMPLATE, self.dir / "index.html")
        (self.dir / "data.js").write_text(helpers.data_js(helpers.good_data()), encoding="utf-8")

    def test_a_report_directory(self):
        record = state.report_snapshot(self.dir, TEMPLATE, "trace")
        self.assertEqual((record["dir"], record["exists"], record["located_by"]), (str(self.dir), True, "trace"))
        self.assertEqual(sorted(record["files"]), ["data.js", "index.html"])
        self.assertEqual(record["files"]["index.html"], record["template_sha256"])
        self.assertEqual(record["template_sha256"], hashlib.sha256(TEMPLATE.read_bytes()).hexdigest())
        self.assertEqual((record["parsed"]["title"], record["parse_error"]), ("Sync service: open items", ""))
        self.assertTrue(record["data_text"].startswith("// written by the agent"))

    def test_unparseable_data_is_recorded_with_the_reason(self):
        (self.dir / "data.js").write_text("window.SITREP = {title: 'a', items: [", encoding="utf-8")
        record = state.report_snapshot(self.dir, TEMPLATE)
        self.assertIsNone(record["parsed"])
        self.assertIn("the data ends", record["parse_error"])

    def test_extra_files_and_subdirectories_are_listed(self):
        (self.dir / "notes.md").write_text("x")
        (self.dir / "assets").mkdir()
        self.assertEqual(state.report_snapshot(self.dir, TEMPLATE)["files"]["assets"], "<directory>")
        self.assertIn("notes.md", state.report_snapshot(self.dir, TEMPLATE)["files"])

    def test_a_report_without_data_js(self):
        (self.dir / "data.js").unlink()
        record = state.report_snapshot(self.dir, TEMPLATE)
        self.assertEqual((record["data_text"], record["parsed"], record["parse_error"]), (None, None, "the report has no data.js"))

    def test_a_directory_that_is_gone_or_never_found(self):
        shutil.rmtree(self.dir)
        gone = state.report_snapshot(self.dir, TEMPLATE)
        self.assertEqual((gone["exists"], gone["files"], gone["dir"]), (False, {}, str(self.dir)))
        none = state.report_snapshot(None, TEMPLATE, "not found")
        self.assertEqual((none["dir"], none["exists"], none["located_by"]), (None, False, "not found"))


if __name__ == "__main__":
    unittest.main()
