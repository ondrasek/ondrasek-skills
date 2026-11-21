import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import checks
import fixtures
import state

UNIT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude"}


class Fixtures(unittest.TestCase):
    def test_every_fixture_builds_text_files(self):
        for name in fixtures.FIXTURES:
            built = fixtures.build(name)
            self.assertTrue(built and all(isinstance(t, str) for t in built.values()), name)

    def test_defects_has_every_planted_defect(self):
        built = fixtures.build("defects")
        claude = built["CLAUDE.md"]
        self.assertEqual(claude.count("\n"), 229)
        for needle in ("@docs/missing-guide.md", "npm run typecheck", "NEVER EVER", "src/legacy/engine.ts", "Use tabs", "Use 2-space"):
            self.assertIn(needle, claude)
        self.assertIn("4-space", built["AGENTS.md"])
        self.assertNotIn("typecheck", built["package.json"])
        self.assertNotIn("docs/missing-guide.md", built)
        self.assertNotIn("src/legacy/engine.ts", built)

    def test_clean_fixture_claims_hold(self):
        built = fixtures.build("clean")
        for needle in ("src/gen/client.ts", "src/index.test.ts"):
            self.assertIn(needle, built)
        self.assertIn('"codegen"', built["package.json"])
        self.assertIn('"lint"', built["package.json"])

    def test_bare_has_no_instruction_file(self):
        self.assertFalse(FORBIDDEN & set(fixtures.build("bare")))

    def test_no_live_instruction_file_is_committed_in_this_eval_directory(self):
        # a CLAUDE.md or AGENTS.md here would load for anyone working in the repo and be audited by the skill
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=UNIT, capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(listed)
        self.assertEqual([p for p in listed if FORBIDDEN & set(Path(p).parts)], [])


class Workspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        state.prepare("defects", self.root)

    def test_prepare_commits_a_clean_tracked_repo(self):
        snap = state.snapshot(self.root)
        self.assertEqual(snap["status"], "")
        self.assertEqual(len(snap["head"]), 40)
        self.assertEqual(set(snap["files"]), set(fixtures.build("defects")))
        self.assertNotIn(".git", " ".join(snap["files"]))

    def test_snapshot_records_lines_and_text(self):
        record = state.snapshot(self.root)["files"]["AGENTS.md"]
        self.assertEqual(record["lines"], 4)
        self.assertIn("4-space", record["text"])

    def test_snapshot_sees_edit_new_file_delete_and_commit(self):
        before = state.snapshot(self.root)
        (self.root / "CLAUDE.md").write_text("short\n")
        (self.root / "new.md").write_text("x\n")
        (self.root / "AGENTS.md").unlink()
        self.assertEqual(checks.diff_states(before, state.snapshot(self.root))[:3], (["new.md"], ["AGENTS.md"], ["CLAUDE.md"]))
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qam", "x"], cwd=self.root, check=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
        self.assertTrue(checks.diff_states(before, state.snapshot(self.root))[3])

    def test_identical_snapshots_have_no_diff(self):
        self.assertEqual(checks.diff_states(state.snapshot(self.root), state.snapshot(self.root)), ([], [], [], False))

    def test_snapshot_from_matches_snapshot(self):
        built = state.snapshot_from(fixtures.build("defects"))
        real = state.snapshot(self.root)
        self.assertEqual({p: r["sha256"] for p, r in built["files"].items()}, {p: r["sha256"] for p, r in real["files"].items()})


if __name__ == "__main__":
    unittest.main()
