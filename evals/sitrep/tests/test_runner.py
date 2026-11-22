"""End to end, offline: run_evals drives a stub agent in real temporary directories, snapshots them, grades the run."""
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import run_evals
import runner_cmd

HERE = Path(__file__).resolve().parent
CSV = ("id,should_trigger,kind,fixture,expect,prompt,reply\n"
       "r1,true,explicit,open-items,report-clean,make a report,thanks i am done\n"
       "m1,true,milestones,open-items-ms,report-ms,state of things,\n"
       "f1,true,feedback,open-items,feedback,pasted feedback,\n"
       "n1,false,negative,bare,none,list them,\n")
AGENT = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'stub_agent.py'))} {{prompt}}"
# mode -> {case id: (verdict, exactly the checks that fail)}; a case that is not listed passes
MODES = {
    "writes-into-project": {"r1": ("FAIL", {"project-untouched"}), "m1": ("FAIL", {"project-untouched"}), "f1": ("FAIL", {"project-untouched"})},
    "bad-data": {"r1": ("FAIL", {"data-parses", "data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "start-here-set"}),
                 "m1": ("FAIL", {"data-parses", "data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "start-here-set",
                                 "milestone-item", "milestone-strip", "milestone-current"})},
    "edits-index": {"r1": ("FAIL", {"index-untouched"}), "m1": ("FAIL", {"index-untouched"})},
    "misses-items": {"r1": ("FAIL", {"items-cover-planted", "dependency-recorded", "dependency-direction"}),
                     "m1": ("FAIL", {"items-cover-planted", "dependency-recorded", "dependency-direction", "milestone-item"})},
    "rm-instead-of-clean": {"r1": ("FAIL", {"cleaned-by-script"})},
    "leaves-report": {"r1": ("FAIL", {"cleaned-by-script", "report-cleaned"})},
}
NO_REPORT_FAILS = {"created-by-script", "report-outside-project", "index-untouched", "only-data-added", "data-defines-sitrep",
                   "data-parses", "data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "start-here-set", "reply-hands-over"}


class Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.csv = self.tmp / "cases.csv"
        self.csv.write_text(CSV, encoding="utf-8")
        self.out = self.tmp / "out"

    def run_main(self, *extra, mode="good"):
        stream = io.StringIO()
        argv = ["--cases", str(self.csv), "--agent-cmd", AGENT, "--out", str(self.out), *extra]
        with mock.patch.dict(os.environ, {"STUB_MODE": mode}):
            code = run_evals.main(argv, stream)
        return code, stream.getvalue()

    def trials(self):
        """{case id: (verdict, set of failed check ids)} from results.json."""
        results = json.loads((self.out / "results.json").read_text())
        return {p["id"]: (p["trials"][0]["verdict"], {c["id"] for c in p["trials"][0]["checks"] if not c["pass"]}) for p in results["prompts"]}

    def states(self, case="r1"):
        return json.loads((self.out / f"{case}.state.json").read_text())

    def test_a_good_agent_passes_every_check(self):
        code, report = self.run_main()
        self.assertEqual(code, 0, report)
        self.assertIn("pass rate 100%", report)
        self.assertIn("cases passed 4/4", report)
        self.assertIn("precondition 8/8", report)

    def test_each_misbehaviour_is_caught_by_its_own_check_from_the_state(self):
        for mode, expected in MODES.items():
            with self.subTest(mode=mode):
                code, report = self.run_main(mode=mode)
                self.assertEqual(code, 1, report)
                want = {case: expected.get(case, ("PASS", set())) for case in ("r1", "m1", "f1", "n1")}
                self.assertEqual(self.trials(), want, report)

    def test_a_reply_that_pastes_the_list_is_a_warning_not_a_failure(self):
        code, report = self.run_main(mode="pastes-list")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.trials(), {case: ("PASS", {"reply-hands-over"} if case in ("r1", "m1") else set()) for case in ("r1", "m1", "f1", "n1")})
        self.assertIn("[WARN]: reply-hands-over", report)
        self.assertIn("WARN (advisory, not in the verdict) on 2 trial(s)", report)
        self.assertIn("cases passed 4/4", report)
        self.assertIn("soft 5/7", report)
        prompts = {p["id"]: p["trials"][0] for p in json.loads((self.out / "results.json").read_text())["prompts"]}
        self.assertEqual((prompts["r1"]["warnings"], prompts["r1"]["verdict"], prompts["n1"]["warnings"]), (["reply-hands-over"], "PASS", []))
        self.assertEqual({c["group"] for c in prompts["r1"]["checks"] if c["id"] == "reply-hands-over"}, {"soft"})
        stream = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.out)], stream), 0, stream.getvalue())

    def test_an_agent_that_calls_the_skill_but_builds_no_report_fails_the_skill(self):
        code, report = self.run_main(mode="no-report")
        self.assertEqual(code, 1)
        self.assertEqual(self.trials()["r1"], ("FAIL", NO_REPORT_FAILS | {"cleaned-by-script", "report-cleaned"}))
        self.assertEqual(self.trials()["m1"][0], "FAIL")
        self.assertEqual(self.trials()["f1"], ("PASS", set()))
        self.assertIn("failed 2", report)

    def test_an_agent_that_crashes_is_inconclusive_for_every_case_and_never_green(self):
        code, report = self.run_main(mode="crash")
        self.assertEqual(code, 1, report)
        self.assertIn("agent command exited 1", report)
        for case, (verdict, failed) in self.trials().items():
            self.assertEqual(verdict, "INCONCLUSIVE", case)
            self.assertIn("run-completed", failed, case)
        self.assertIn("inconclusive 4", report)
        self.assertNotIn("pass rate 100%", report)

    def test_the_report_is_made_in_the_trial_tmpdir_and_the_state_records_it(self):
        self.run_main()
        states = self.states()
        self.assertEqual(states["plugin"], str(run_evals.PLUGIN.resolve()))
        self.assertEqual(set(states), {"layout", "plugin", "before", "after1", "after2", "tmp_sitrep", "report1", "report2", "run_note"})
        report = states["report1"]
        self.assertEqual(os.path.dirname(report["dir"]), states["layout"]["tmp"])
        self.assertEqual((report["located_by"], report["exists"], sorted(report["files"])), ("trace", True, ["data.js", "index.html"]))
        self.assertEqual(report["files"]["index.html"], report["template_sha256"])
        self.assertEqual(report["parsed"]["next"]["title"], "Decide on the database")
        self.assertEqual(states["tmp_sitrep"], [os.path.basename(report["dir"])])
        self.assertEqual((states["report2"]["dir"], states["report2"]["exists"]), (report["dir"], False))
        self.assertEqual(states["after2"]["files"], states["before"]["files"])

    def test_a_negative_case_records_no_report(self):
        self.run_main()
        states = self.states("n1")
        self.assertEqual((states["report1"]["dir"], states["tmp_sitrep"], states["report2"]), (None, [], None))

    def test_the_milestone_tracker_is_snapshotted_and_the_repo_ignores_it(self):
        self.run_main()
        states = self.states("m1")
        self.assertIn(".cache/milestones.json", states["before"]["files"])
        self.assertEqual(states["before"]["status"], "")

    def test_the_cost_is_the_last_result_events_not_the_sum(self):
        self.run_main()
        trial = json.loads((self.out / "results.json").read_text())["prompts"][0]["trials"][0]
        self.assertEqual(trial["cost"], 0.5)

    def test_the_trial_root_is_deleted_unless_kept(self):
        self.run_main()
        self.assertFalse(Path(self.states()["layout"]["root"]).exists())
        self.run_main("--keep", "--only", "r1")
        kept = Path(self.states()["layout"]["root"])
        self.addCleanup(shutil.rmtree, kept, ignore_errors=True)
        self.assertTrue((kept / "ws" / ".git").is_dir())
        self.assertTrue((kept / "tmp").is_dir())

    def test_recorded_runs_grade_offline(self):
        self.run_main()
        stream = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.out)], stream), 0, stream.getvalue())

    def test_the_runner_note_is_recorded_and_grades_offline(self):
        """A claude process that printed a success result and then exited non-zero: INCONCLUSIVE live and when re-graded from the artifacts."""
        self.run_main(mode="exits-nonzero")
        self.assertIn("exited 3", self.states("n1")["run_note"])
        live = self.trials()
        self.assertEqual({v for v, _ in live.values()}, {"INCONCLUSIVE"})
        stream, results = io.StringIO(), self.tmp / "regraded.json"
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.out), "--results", str(results)], stream), 1, stream.getvalue())
        regraded = {p["id"]: (p["trials"][0]["verdict"], {c["id"] for c in p["trials"][0]["checks"] if not c["pass"]}) for p in json.loads(results.read_text())["prompts"]}
        self.assertEqual(regraded, live)
        self.assertIn("exited 3", stream.getvalue())

    def test_a_good_run_records_an_empty_note(self):
        self.run_main()
        self.assertEqual(self.states()["run_note"], "")

    def test_a_recorded_bad_run_fails_offline(self):
        self.run_main(mode="edits-index")
        stream = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.out)], stream), 1)
        self.assertIn("index-untouched", stream.getvalue())

    def test_a_missing_recording_fails(self):
        stream = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.tmp / "nowhere")], stream), 1)
        self.assertIn("no recorded trace", stream.getvalue())

    def test_unknown_case_id_and_bad_trials(self):
        self.assertEqual(self.run_main("--only", "zzz")[0], 2)
        self.assertEqual(self.run_main("--trials", "0")[0], 2)

    def test_a_missing_agent_program_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_evals.main(["--cases", str(self.csv), "--agent-cmd", "no-such-program-xyz {prompt}", "--out", str(self.out)], io.StringIO())


class DefaultCommand(unittest.TestCase):
    EXTRA = ["--add-dir", "/t/tmp", "--plugin-dir", "/p"]

    def argv(self, extra):
        return runner_cmd.build(run_evals.DEFAULT_AGENT_CMD, "THE PROMPT", extra)

    def test_it_is_isolated_and_reports_hooks_and_plugins(self):
        argv = self.argv(self.EXTRA)
        for needle in ("--verbose", "--include-hook-events", "stream-json", "dontAsk"):
            self.assertIn(needle, argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project,local")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--bare", argv)
        self.assertEqual((argv[0], argv[argv.index("claude") - 1]), ("env", "MILESTONES_STALE_MINUTES"))
        for variable in ("CLAUDE_PROJECT_DIR", "CLAUDE_HOOK_RUNNING", "MILESTONES_FILE", "MILESTONES_NOW"):
            self.assertIn(variable, argv)

    def test_the_prompt_comes_before_the_tool_list_and_no_list_flag_ends_the_command(self):
        for extra in (self.EXTRA, self.EXTRA + ["--resume", "abc"]):
            argv = self.argv(extra)
            self.assertLess(argv.index("THE PROMPT"), argv.index("--allowedTools"))
            self.assertTrue(argv[argv.index("--allowedTools") + 2].startswith("--"))
            after_dir = argv[argv.index("--add-dir") + 2:argv.index("--add-dir") + 3]
            self.assertTrue(after_dir and after_dir[0].startswith("--"), "--add-dir takes a list: a flag must follow its value")

    def test_the_grants_are_one_argument_and_the_tools_match_the_plan(self):
        argv = self.argv(self.EXTRA)
        granted = argv[argv.index("--allowedTools") + 1]
        self.assertEqual(granted, run_evals.ALLOWED)
        for tool in ("Skill", "Read", "Write", "Edit", "Bash(*report.sh *)", "Bash(ls *)", "Bash(cat *)"):
            self.assertIn(tool, granted.split(","))
        for tool in ("open", "xdg-open", "start", "Bash(*)", "WebFetch"):
            self.assertNotIn(tool, granted.split(","))

    def test_the_plugin_under_test_exists(self):
        self.assertTrue((run_evals.PLUGIN / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((run_evals.PLUGIN / "skills" / "sitrep" / "template.html").is_file())


if __name__ == "__main__":
    unittest.main()
