"""End-to-end, offline: run_evals drives a stub agent that runs real git in a real temporary repository and bare remote, snapshots both, grades them."""
import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from test_checks import REBASED
import checks
import runner_cmd
import run_evals
import state

HERE = Path(__file__).resolve().parent
CSV = helpers.UNIT / "cases" / "cases.csv"
AGENT = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'stub_agent.py'))} {{prompt}}"


class Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def run_main(self, *extra, mode="good"):
        out = io.StringIO()
        argv = ["--cases", str(CSV), "--agent-cmd", AGENT, "--out", str(self.tmp / "out"), *extra]
        with mock.patch.dict(os.environ, {"STUB_MODE": mode}), contextlib.redirect_stderr(io.StringIO()):
            code = run_evals.main(argv, out)
        return code, out.getvalue()

    def row_of(self, report, case_id):
        return next(line for line in report.splitlines() if line.startswith(case_id + " "))

    def test_a_good_agent_passes_every_case(self):
        code, report = self.run_main()
        self.assertEqual(code, 0, report)
        self.assertIn("cases passed 6/6", report)
        self.assertIn("pass rate 100%", report)

    def test_a_rebase_is_caught_from_the_history_and_the_trace(self):
        code, report = self.run_main("--only", "behind-merge", mode="rebase")
        self.assertEqual(code, 1)
        row = self.row_of(report, "behind-merge")
        self.assertIn("FAIL", row)
        for check_id in ("history-kept", "merged-with-merge-commit", "guardrails-in-trace"):
            self.assertIn(check_id, row)

    def test_committing_env_is_caught_in_the_workspace_and_on_origin(self):
        code, report = self.run_main("--only", "env-untracked", mode="commits-env")
        self.assertEqual(code, 1)
        self.assertIn("env-never-committed", self.row_of(report, "env-untracked"))
        self.assertIn("['.env']", report)

    def test_a_rewritten_remote_is_caught_whatever_command_did_it(self):
        code, report = self.run_main("--only", "wip-after-edit", mode="rewrites-remote")
        self.assertEqual(code, 1)
        row = self.row_of(report, "wip-after-edit")
        self.assertIn("remote-history-kept", row)
        self.assertNotIn("guardrails-in-trace", row)      # no git command in the trace did it

    def test_an_empty_commit_on_a_clean_repository_is_caught(self):
        code, report = self.run_main("--only", "clean-noop", mode="touches-clean")
        self.assertEqual(code, 1)
        self.assertIn("untouched-when-clean", self.row_of(report, "clean-noop"))

    def test_a_report_without_an_outcome_fails(self):
        code, report = self.run_main("--only", "wip-preexisting", mode="no-commit")
        self.assertEqual(code, 1)
        row = self.row_of(report, "wip-preexisting")
        self.assertIn("FAIL", row)
        self.assertIn("tree-clean", row)
        self.assertNotIn("hook-report-delivered", row)

    def test_no_report_and_no_outcome_is_inconclusive_not_a_failure(self):
        code, report = self.run_main("--only", "wip-preexisting", mode="no-hook")
        self.assertEqual(code, 1)
        row = self.row_of(report, "wip-preexisting")
        self.assertIn("INCONCLUSIVE", row)
        self.assertNotIn("FAIL", row)
        self.assertIn("hook-report-delivered", row)
        self.assertIn("inconclusive 1", report)

    def test_a_crashed_agent_is_inconclusive_even_on_the_negative_control(self):
        code, report = self.run_main("--only", "clean-noop", mode="crash")
        self.assertEqual(code, 1)
        row = self.row_of(report, "clean-noop")
        self.assertIn("INCONCLUSIVE", row)
        self.assertIn("run-completed", row)
        self.assertIn("agent command exited 1", report)

    def test_a_nonzero_exit_after_a_complete_trace_is_inconclusive_and_survives_a_recording(self):
        code, report = self.run_main("--only", "wip-preexisting", mode="exit-3")
        self.assertEqual(code, 1)
        self.assertIn("exit code 3", report)
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--only", "wip-preexisting", "--fixtures", str(self.tmp / "out")], out), 1)
        self.assertIn("INCONCLUSIVE", self.row_of(out.getvalue(), "wip-preexisting"))
        self.assertIn("exit code 3", out.getvalue())

    def test_artifacts_hold_the_trace_and_both_snapshots(self):
        self.run_main("--only", "behind-merge")
        out = self.tmp / "out"
        self.assertTrue((out / "behind-merge.jsonl").is_file())
        states = json.loads((out / "behind-merge.state.json").read_text())
        self.assertEqual(set(states), {"before", "after", "run"})
        self.assertEqual(states["run"], {"exit_code": 0})
        self.assertEqual(len(states["before"]["log"]), 3)
        self.assertEqual(states["before"]["status"], "?? notes/todo.md\n")
        self.assertEqual(states["after"]["status"], "")
        self.assertEqual(states["after"]["origin"][checks.MAIN], states["after"]["head"])
        self.assertTrue(any(len(c["parents"]) == 2 for c in states["after"]["log"]))
        results = json.loads((out / "results.json").read_text())
        self.assertEqual((results["prompts"][0]["id"], results["prompts"][0]["trials"][0]["verdict"]), ("behind-merge", "PASS"))

    def test_the_trial_directory_is_removed_unless_kept(self):
        seen = []
        real = tempfile.mkdtemp
        with mock.patch.object(tempfile, "mkdtemp", side_effect=lambda **kw: seen.append(real(dir=self.tmp, **kw)) or seen[-1]):
            self.run_main("--only", "clean-noop")
            self.run_main("--only", "clean-noop", "--keep")
        self.assertEqual(len(seen), 2)
        self.assertFalse(Path(seen[0]).exists())
        self.assertTrue((Path(seen[1]) / "ws" / ".git").is_dir())

    def test_recorded_runs_grade_offline(self):
        self.run_main("--only", "behind-merge,env-untracked")
        out = io.StringIO()
        code = run_evals.main(["--cases", str(CSV), "--only", "behind-merge,env-untracked", "--fixtures", str(self.tmp / "out")], out)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("cases passed 2/2", out.getvalue())

    def test_a_recorded_rebase_run_fails_offline(self):
        self.run_main("--only", "behind-merge", mode="rebase")
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--only", "behind-merge", "--fixtures", str(self.tmp / "out")], out), 1)
        self.assertIn("history-kept", out.getvalue())

    def test_a_missing_recording_is_not_a_pass(self):
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--fixtures", str(self.tmp / "nowhere")], out), 1)
        self.assertIn("no recorded trace", out.getvalue())

    def test_unknown_case_id_and_bad_trials(self):
        self.assertEqual(self.run_main("--only", "zzz")[0], 2)
        self.assertEqual(self.run_main("--trials", "0")[0], 2)

    def test_a_missing_agent_program_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_evals.main(["--cases", str(CSV), "--only", "clean-noop", "--agent-cmd", "no-such-program-xyz {prompt}", "--out", str(self.tmp / "o")], io.StringIO())

    def test_two_trials_are_saved_separately(self):
        self.run_main("--only", "clean-noop", "--trials", "2")
        out = self.tmp / "out"
        self.assertTrue((out / "clean-noop.1.jsonl").is_file() and (out / "clean-noop.2.jsonl").is_file())


class AgentCommand(unittest.TestCase):
    def test_the_default_command_isolates_the_run_and_keeps_the_hooks_on(self):
        cmd = run_evals.DEFAULT_AGENT_CMD
        tokens = runner_cmd.split(cmd)
        self.assertEqual(tokens[:2], ["env", "-u"])
        for variable in ("CLAUDE_PROJECT_DIR", "CLAUDE_HOOK_RUNNING"):
            self.assertIn(variable, tokens[:tokens.index("claude")])
        for needle in ("--include-hook-events", "--output-format", "stream-json", "--verbose", "--max-budget-usd", "--setting-sources project,local"):
            self.assertIn(needle, cmd)
        self.assertNotIn("--bare", tokens)
        self.assertLess(tokens.index("{prompt}"), tokens.index("--allowedTools"))
        self.assertEqual(tokens[tokens.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(tokens[tokens.index("--allowedTools") + 1], run_evals.ALLOWED)
        self.assertTrue((run_evals.PLUGIN / ".claude-plugin" / "plugin.json").is_file())

    def test_the_agent_gets_the_plugin_the_remote_and_a_git_that_ignores_global_config(self):
        row = helpers.ROWS["behind-merge"]
        args = run_evals.parse_args(["--agent-cmd", run_evals.DEFAULT_AGENT_CMD])
        with mock.patch.object(runner_cmd, "run", return_value=(0, "", "")) as run:
            run_evals.run_agent(row, 1, args)
        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv[argv.index("-p") + 1], row.prompt)
        self.assertLess(argv.index(row.prompt), argv.index("--allowedTools"))
        self.assertEqual(argv[argv.index("--plugin-dir") + 1], str(run_evals.PLUGIN))
        origin = argv[argv.index("--add-dir") + 1]
        self.assertTrue(origin.endswith("origin.git"))
        # --add-dir takes a list: whatever follows its value must be a flag
        self.assertTrue(argv[argv.index("--add-dir") + 2].startswith("--"))
        self.assertEqual(kwargs["cwd"].name, "ws")
        self.assertEqual(kwargs["cwd"].parent, Path(origin).parent)
        for key, value in state.GIT_ISOLATION.items():
            self.assertEqual(kwargs["env"][key], value)
        self.assertEqual(kwargs["env"]["EVAL_PROMPT_ID"], "behind-merge")

    def test_the_callers_git_variables_do_not_reach_the_agent_and_come_back_afterwards(self):
        probe = f"{shlex.quote(sys.executable)} -c \"import os, json; print(json.dumps(sorted(k for k in os.environ if k.startswith('GIT_'))))\" {{prompt}}"
        args = run_evals.parse_args(["--agent-cmd", probe])
        leaked = {"GIT_DIR": "/nowhere", "GIT_WORK_TREE": "/nowhere", "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "leak",
                  "GIT_CONFIG_PARAMETERS": "'core.hookspath=/tmp/elsewhere'", "GIT_AUTHOR_NAME": "leak", "GIT_INDEX_FILE": "/nowhere"}
        with mock.patch.dict(os.environ, leaked):
            out, states, note = run_evals.run_agent(helpers.ROWS["clean-noop"], 1, args)
            self.assertEqual({k: os.environ[k] for k in leaked}, leaked)
        self.assertEqual(json.loads(out), sorted(state.GIT_ISOLATION))
        self.assertEqual(note, "")

    def test_a_case_without_a_remote_gets_no_add_dir(self):
        args = run_evals.parse_args([])
        with mock.patch.object(runner_cmd, "run", return_value=(0, "", "")) as run:
            run_evals.run_agent(helpers.ROWS["no-remote-local-only"], 1, args)
        self.assertNotIn("--add-dir", run.call_args.args[0])


class Grading(unittest.TestCase):
    def test_a_precondition_failure_wins_over_outcome_failures(self):
        row, events, states = helpers.good("wip-preexisting", hook="none", after=helpers.before_snapshot("dirty"))
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "INCONCLUSIVE")
        self.assertTrue(any(not c["pass"] and checks.GROUPS[c["id"]] == "outcome" for c in graded["checks"]))

    def test_verdicts(self):
        row, events, states = helpers.good("wip-preexisting")
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "PASS")
        row, events, states = helpers.good("wip-preexisting", plugins=())
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

    def test_a_run_that_never_got_past_init_is_not_a_pass(self):
        row, events, states = helpers.good("clean-noop")
        for truncated in ([events[0]], [events[0], helpers.result(subtype="error_max_budget_usd", is_error=True)]):
            graded = run_evals.grade_trial(row, truncated, states)
            self.assertEqual(graded["verdict"], "INCONCLUSIVE", truncated)
        self.assertEqual(run_evals.grade_trial(row, events, {**states, "run": {"exit_code": 1}})["verdict"], "INCONCLUSIVE")
        self.assertEqual(run_evals.grade_trial(row, events, {**states, "run": {"exit_code": 0}})["verdict"], "PASS")

    def test_a_violation_the_repository_proves_is_a_fail_whatever_was_denied(self):
        log = dict(denials=[{"tool_name": "Bash", "tool_input": {"command": "git log --oneline | head -5"}}])
        row, events, states = helpers.good("behind-merge", after=REBASED, **log)
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "FAIL")
        self.assertIn("not-permission-blocked", [c["id"] for c in graded["checks"] if not c["pass"]])
        row, events, states = helpers.good("env-untracked", after=dict(all_paths=[".env", "README.md"]), **log)
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")
        # the same denial without a proven violation is still the harness's problem, not the plugin's
        row, events, states = helpers.good("wip-preexisting", **log)
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_a_denial_that_only_mentions_git_does_not_spoil_a_good_run(self):
        row, events, states = helpers.good("wip-preexisting", denials=[{"tool_name": "Bash", "tool_input": {"command": "ls -la .git"}}])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "PASS")

    def test_a_foreign_stop_hook_without_the_marker_is_inconclusive_not_a_failure(self):
        row, events, states = helpers.good("wip-preexisting", hook="none", after=helpers.before_snapshot("dirty"))
        events.insert(1, {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": "another hook"})
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_an_installed_copy_of_the_plugin_is_not_graded_in_place_of_the_one_under_test(self):
        row, events, states = helpers.good("wip-preexisting", init_kwargs=dict(source="{}@ondrasek-skills"))
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")
        row, events, states = helpers.good("wip-preexisting")
        self.assertEqual(run_evals.grade_trial(row, events, states, plugin_path=helpers.PLUGIN_DIR)["verdict"], "PASS")
        self.assertEqual(run_evals.grade_trial(row, events, states, plugin_path="/elsewhere/auto-commit")["verdict"], "FAIL")

    def test_the_cost_is_that_of_the_last_result_event(self):
        # a resumed session's result already holds the whole session; summing every result event would double-count it
        row, events, states = helpers.good("wip-preexisting")
        events.insert(-1, helpers.result(cost=0.25))
        self.assertEqual(run_evals.grade_trial(row, events, states)["cost"], 0.5)

    def test_a_case_is_as_bad_as_its_worst_trial(self):
        trial = lambda verdict: {"verdict": verdict}
        for verdicts, expected in ((["PASS", "PASS"], "PASS"), (["PASS", "INCONCLUSIVE"], "INCONCLUSIVE"), (["INCONCLUSIVE", "FAIL"], "FAIL")):
            self.assertEqual(run_evals.case_verdict({"trials": [trial(v) for v in verdicts]}), expected)


if __name__ == "__main__":
    unittest.main()
