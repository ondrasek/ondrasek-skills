"""End-to-end, offline: run_evals drives a stub agent in a real temporary git repository, snapshots it, grades it."""
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import run_evals

HERE = Path(__file__).resolve().parent
CSV = ('id,should_trigger,kind,fixture,expect,prompt,reply\n'
       'p1,true,explicit,defects,proposal,audit,\n'
       'a1,true,apply,defects,apply,audit,approve\n')
AGENT = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'stub_agent.py'))} {{prompt}}"


class Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.csv = self.tmp / "cases.csv"
        self.csv.write_text(CSV, encoding="utf-8")

    def run_main(self, *extra, mode="good"):
        out = io.StringIO()
        argv = ["--cases", str(self.csv), "--agent-cmd", AGENT, "--out", str(self.tmp / "out"), *extra]
        with mock.patch.dict(os.environ, {"STUB_MODE": mode}):
            code = run_evals.main(argv, out)
        return code, out.getvalue()

    def test_a_good_agent_passes_every_check(self):
        code, report = self.run_main()
        self.assertEqual(code, 0, report)
        self.assertIn("pass rate 100%", report)
        self.assertIn("cases passed 2/2", report)

    def test_editing_before_approval_is_caught_from_the_files(self):
        code, report = self.run_main(mode="edit-early")
        self.assertEqual(code, 1)
        self.assertIn("no-edit-before-approval", report)
        self.assertIn("no-mutating-tool-before-approval", report)

    def test_touching_agents_md_is_caught(self):
        code, report = self.run_main("--only", "a1", mode="touch-agents")
        self.assertEqual(code, 1)
        self.assertIn("apply-agents-unchanged", report)
        self.assertNotIn("apply-edited-claude-md:", report)

    def test_artifacts_hold_the_trace_and_three_snapshots(self):
        self.run_main("--only", "a1")
        out = self.tmp / "out"
        self.assertTrue((out / "a1.jsonl").is_file())
        states = json.loads((out / "a1.state.json").read_text())
        self.assertEqual(set(states), {"before", "after1", "after2"})
        self.assertEqual(states["before"]["files"]["CLAUDE.md"]["lines"], 229)
        self.assertEqual(states["after2"]["files"]["CLAUDE.md"]["lines"], 3)
        self.assertEqual(json.loads((out / "results.json").read_text())["prompts"][0]["id"], "a1")

    def test_recorded_runs_grade_offline(self):
        self.run_main()
        out = io.StringIO()
        code = run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.tmp / "out")], out)
        self.assertEqual(code, 0, out.getvalue())

    def test_a_recorded_edit_early_run_fails_offline(self):
        self.run_main(mode="edit-early")
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.tmp / "out")], out), 1)

    def test_a_missing_recording_fails(self):
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.tmp / "nowhere")], out), 1)
        self.assertIn("no recorded trace", out.getvalue())

    def test_unknown_case_id_and_bad_trials(self):
        self.assertEqual(self.run_main("--only", "zzz")[0], 2)
        self.assertEqual(self.run_main("--trials", "0")[0], 2)

    def test_a_missing_agent_program_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_evals.main(["--cases", str(self.csv), "--agent-cmd", "no-such-program-xyz {prompt}", "--out", str(self.tmp / "o")], io.StringIO())

    def test_the_default_agent_command_is_isolated_and_grants_what_the_skill_needs(self):
        cmd = run_evals.DEFAULT_AGENT_CMD
        for needle in ("--setting-sources project,local", "stream-json", "--verbose", "WebFetch", "WebSearch", "Skill", "Agent"):
            self.assertIn(needle, cmd)
        self.assertTrue((run_evals.PLUGIN / ".claude-plugin" / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
