"""The runner end to end with a stub agent and a stub judge: traces saved, checks run, table printed, exit codes, trials, --only."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import PROMPTS, ROWS, TRACES, stub_agent, stub_judge
import agent_trace
import run_evals


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "artifacts"

    def run_main(self, *args, env=None):
        stream = io.StringIO()
        base = {"STUB_TRACES": str(TRACES / "after")}
        with mock.patch.dict(os.environ, {**base, **(env or {})}):
            code = run_evals.main(["--prompts", str(PROMPTS), "--agent-cmd", stub_agent(), "--out", str(self.out), *args], stream=stream)
        return code, stream.getvalue()

    def results(self):
        return json.loads((self.out / "results.json").read_text(encoding="utf-8"))


class EndToEndTests(RunnerCase):
    def test_a_full_run_saves_one_trace_per_prompt_and_passes(self):
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.jsonl")), sorted(ROWS))
        self.assertIn("summary: prompts passed 11/11 (every trial), trials passed 11/11, pass rate 100%", text)

    def test_the_saved_trace_is_what_the_agent_printed(self):
        self.run_main("--only", "explicit-name")
        saved = agent_trace.load(self.out / "explicit-name.jsonl")
        self.assertEqual(saved, agent_trace.load(TRACES / "after" / "explicit-name.jsonl"))

    def test_a_failing_trace_gives_exit_code_1_and_names_the_check(self):
        code, text = self.run_main(env={"STUB_TRACES": str(TRACES / "before")})
        self.assertEqual(code, 1)
        self.assertIn("implicit-walk", text)
        self.assertRegex(text, r"followup-stop\s+followup\s+stop\s+yes\s+0/1\s+FAIL\s+wrapup-headings")
        self.assertIn("failures:", text)

    def test_only_runs_the_named_prompts(self):
        code, text = self.run_main("--only", "explicit-name,negative-single")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.jsonl")), ["explicit-name", "negative-single"])
        self.assertIn("summary: prompts passed 2/2", text)

    def test_an_unknown_id_is_refused_before_any_run(self):
        code, _ = self.run_main("--only", "nope")
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_the_plugin_dir_reaches_the_agent_before_the_prompt(self):
        saved = Path(self.tmp.name) / "args.json"
        self.run_main("--only", "explicit-name", "--skill-path", "/some/plugin", env={"STUB_SAVE_ARGS": str(saved)})
        args = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(args, ["--plugin-dir", "/some/plugin", ROWS["explicit-name"].prompt])

    def test_a_relative_plugin_path_reaches_the_agent_as_an_absolute_one(self):
        saved = Path(self.tmp.name) / "args.json"
        self.run_main("--only", "explicit-name", "--skill-path", "relative/plugin", env={"STUB_SAVE_ARGS": str(saved)})
        args = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(args[0], "--plugin-dir")
        self.assertEqual(Path(args[1]), Path("relative/plugin").resolve())

    def test_the_prompt_is_the_last_argument_of_a_plain_command(self):
        saved = Path(self.tmp.name) / "args.json"
        self.run_main("--only", "explicit-name", env={"STUB_SAVE_ARGS": str(saved)})
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))[-1], ROWS["explicit-name"].prompt)

    def test_a_followup_row_runs_two_turns_and_resumes_the_first_session(self):
        saved = Path(self.tmp.name) / "args.json"
        code, text = self.run_main("--only", "followup-skip", env={"STUB_SAVE_ARGS": str(saved)})
        self.assertEqual(code, 0, text)
        args = json.loads(saved.read_text(encoding="utf-8"))   # the second call wrote it last
        self.assertEqual(args, ["--resume", "INVENTED-followup-skip", ROWS["followup-skip"].reply])
        self.assertEqual(agent_trace.load(self.out / "followup-skip.jsonl"), agent_trace.load(TRACES / "after" / "followup-skip.jsonl"))

    def test_a_first_turn_without_a_session_id_stops_before_the_second_turn(self):
        silent = Path(self.tmp.name) / "silent"
        silent.mkdir()
        (silent / "followup-skip.jsonl").write_text('{"type": "result", "subtype": "success", "result": "x"}\n', encoding="utf-8")
        code, text = self.run_main("--only", "followup-skip", env={"STUB_TRACES": str(silent)})
        self.assertEqual(code, 1)
        self.assertIn("printed no session id", text)

    def test_the_agent_runs_in_an_empty_working_directory(self):
        script = Path(self.tmp.name) / "cwd_agent.py"
        script.write_text("import os, sys\nsys.stdout.write(os.getcwd() + chr(10) + str(os.listdir('.')))\n", encoding="utf-8")
        from helpers import command
        stream = io.StringIO()
        work = Path(self.tmp.name) / "work"
        work.mkdir()
        run_evals.main(["--prompts", str(PROMPTS), "--agent-cmd", command(script), "--out", str(self.out), "--only", "negative-single",
                        "--workdir", str(work)], stream=stream)
        saved = (self.out / "negative-single.jsonl").read_text(encoding="utf-8")
        self.assertIn(os.path.realpath(work), os.path.realpath(saved.splitlines()[0]))
        self.assertIn("[]", saved)

    def test_a_missing_agent_program_stops_with_a_message(self):
        with self.assertRaises(SystemExit) as caught:
            run_evals.main(["--prompts", str(PROMPTS), "--agent-cmd", "definitely-not-a-program-xyz", "--out", str(self.out)], stream=io.StringIO())
        self.assertIn("cannot start the agent command", str(caught.exception))

    def test_an_agent_that_exits_nonzero_without_output_fails_the_run(self):
        code, text = self.run_main("--only", "explicit-name", env={"STUB_EXIT": "7", "STUB_TRACES": str(self.tmp.name)})
        self.assertNotEqual(code, 0)


class ResultsFileTests(RunnerCase):
    def test_results_hold_every_check_of_every_trial(self):
        self.run_main("--label", "run-a")
        data = self.results()
        self.assertEqual((data["label"], data["skill"], data["trials"], data["judge"]), ("run-a", "oaat", 1, False))
        first = data["prompts"][0]
        self.assertEqual(first["id"], "explicit-dollar")
        self.assertEqual({"id", "pass", "notes"}, set(first["trials"][0]["checks"][0]))
        self.assertEqual(len(first["trials"][0]["checks"]), 16)

    def test_fixtures_mode_writes_no_traces_and_no_results_unless_asked(self):
        stream = io.StringIO()
        code = run_evals.main(["--prompts", str(PROMPTS), "--fixtures", str(TRACES / "after"), "--out", str(self.out)], stream=stream)
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        target = Path(self.tmp.name) / "r.json"
        run_evals.main(["--prompts", str(PROMPTS), "--fixtures", str(TRACES / "after"), "--results", str(target)], stream=io.StringIO())
        self.assertTrue(target.is_file())

    def test_a_missing_fixture_trace_is_a_failure_with_a_note(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        stream = io.StringIO()
        code = run_evals.main(["--prompts", str(PROMPTS), "--fixtures", str(empty), "--only", "explicit-name"], stream=stream)
        self.assertEqual(code, 1)
        self.assertIn("no recorded trace for explicit-name", stream.getvalue())


class TrialTests(RunnerCase):
    def test_trials_save_numbered_traces_and_report_pass_counts(self):
        env = {"STUB_ALT": str(TRACES / "before")}
        code, text = self.run_main("--only", "followup-stop", "--trials", "2", env=env)
        self.assertEqual(code, 1)
        self.assertTrue((self.out / "followup-stop.1.jsonl").is_file())
        self.assertTrue((self.out / "followup-stop.2.jsonl").is_file())
        self.assertRegex(text, r"followup-stop\s+followup\s+stop\s+yes\s+1/2\s+FAIL")
        self.assertIn("trials passed 1/2, pass rate 50%", text)
        self.assertIn("prompts passed 0/1 (every trial)", text)
        trials = self.results()["prompts"][0]["trials"]
        self.assertEqual([t["passed"] for t in trials], [True, False])

    def test_a_single_trial_uses_the_plain_file_name(self):
        self.run_main("--only", "followup-stop")
        self.assertTrue((self.out / "followup-stop.jsonl").is_file())

    def test_trials_below_one_are_refused(self):
        self.assertEqual(self.run_main("--trials", "0")[0], 2)


class JudgeTests(RunnerCase):
    def run_judged(self, mode, traces="after", *extra):
        env = {"STUB_JUDGE_MODE": mode, "STUB_TRACES": str(TRACES / traces)}
        return self.run_main("--only", "explicit-dollar,followup-stop", "--judge", "--judge-cmd", stub_judge(), *extra, env=env)

    def test_a_passing_judge_keeps_the_pass(self):
        code, text = self.run_judged("pass")
        self.assertEqual(code, 0, text)
        trial = self.results()["prompts"][0]["trials"][0]
        self.assertTrue(trial["judge"]["overall_pass"])

    def test_a_failing_judge_fails_the_trial_even_when_every_check_passes(self):
        code, text = self.run_judged("fail")
        self.assertEqual(code, 1)
        self.assertRegex(text, r"explicit-dollar\s+explicit\s+item\s+yes\s+0/1\s+FAIL\s+judge")
        self.assertIn("judge one-item: stub verdict (invented)", text)

    def test_an_invalid_judge_reply_fails_the_trial_and_says_why(self):
        code, text = self.run_judged("badschema")
        self.assertEqual(code, 1)
        self.assertIn("judge: $.score: 101 is above 100", text)

    def test_without_the_flag_no_judge_runs(self):
        self.run_main("--only", "explicit-dollar", "--judge-cmd", "definitely-not-a-program-xyz")
        self.assertIsNone(self.results()["prompts"][0]["trials"][0]["judge"])

    def test_the_skill_file_is_read_for_the_judge_and_never_copied(self):
        skill = Path(self.tmp.name) / "SKILL.md"
        skill.write_text("INVENTED SKILL TEXT", encoding="utf-8")
        saved = Path(self.tmp.name) / "judge-args.json"
        script = Path(self.tmp.name) / "capture_judge.py"
        script.write_text("import sys, json\nopen(sys.argv[1], 'w').write(sys.argv[-1])\nprint(json.dumps({'overall_pass': True, 'score': 1, 'checks': [{'id': i, 'pass': True, 'notes': ''} for i in ['one-item', 'shape', 'question', 'recommendation', 'plain-words']]}))\n", encoding="utf-8")
        from helpers import command
        self.run_main("--only", "explicit-dollar", "--judge", "--judge-cmd", command(script, str(saved)), "--skill-file", str(skill))
        self.assertIn("INVENTED SKILL TEXT", saved.read_text(encoding="utf-8"))
        self.assertEqual(list(self.out.glob("*SKILL*")), [])


class ReportFormatTests(unittest.TestCase):
    def test_the_report_lists_checks_by_group(self):
        stream = io.StringIO()
        run_evals.main(["--prompts", str(PROMPTS), "--fixtures", str(TRACES / "after")], stream=stream)
        self.assertIn("checks passed by group: outcome 43/43, process 22/22, style 37/37, efficiency 30/30", stream.getvalue())


if __name__ == "__main__":
    unittest.main()


class IsolationTests(unittest.TestCase):
    def applied(self, *argv):
        args = run_evals.parse_args(list(argv))
        run_evals.apply_isolation(args)
        return args

    def test_the_default_runs_isolated_with_the_bundled_skill(self):
        args = self.applied()
        self.assertIn("--setting-sources project,local", args.agent_cmd)
        self.assertEqual(Path(args.skill_path), run_evals.BUNDLED_PLUGIN)

    def test_user_setup_runs_with_the_machines_own_setup(self):
        args = self.applied("--user-setup")
        self.assertNotIn("--setting-sources", args.agent_cmd)
        self.assertIsNone(args.skill_path)

    def test_a_given_skill_path_and_a_custom_command_are_left_alone(self):
        self.assertEqual(self.applied("--skill-path", "/x").skill_path, "/x")
        args = self.applied("--agent-cmd", "my-agent")
        self.assertEqual((args.agent_cmd, args.skill_path), ("my-agent", None))

    def test_another_skill_gets_no_bundled_path(self):
        self.assertIsNone(self.applied("--skill", "other").skill_path)
