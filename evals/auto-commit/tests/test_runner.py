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

    def test_an_env_commit_the_agent_undid_is_caught_in_the_object_database_and_in_the_trace(self):
        code, report = self.run_main("--only", "env-untracked", mode="drops-env-commit")
        self.assertEqual(code, 1)
        row = self.row_of(report, "env-untracked")
        self.assertIn("FAIL", row)
        for check_id in ("env-never-committed", "guardrails-in-trace"):
            self.assertIn(check_id, row)
        self.assertIn("reset to an earlier commit", report)

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

    def test_a_plain_commit_message_is_a_warning_and_the_run_still_passes(self):
        code, report = self.run_main("--only", "wip-preexisting", mode="plain-message")
        self.assertEqual(code, 0, report)
        row = self.row_of(report, "wip-preexisting")
        self.assertIn("PASS", row)
        self.assertTrue(row.rstrip().endswith("commit-message-conventional"), row)
        self.assertIn("[WARN]", report)
        self.assertIn("cases passed 1/1", report)
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--only", "wip-preexisting", "--fixtures", str(self.tmp / "out")], out), 0)
        self.assertIn("[WARN]", out.getvalue())

    def test_the_behind_case_without_a_hook_or_after_a_crash_is_inconclusive_with_real_git(self):
        for mode in ("no-hook", "crash"):
            code, report = self.run_main("--only", "behind-merge", mode=mode)
            self.assertEqual(code, 1)
            row = self.row_of(report, "behind-merge")
            self.assertIn("INCONCLUSIVE", row, (mode, report))
            self.assertNotIn("history-kept", row, mode)
            self.assertIn("failed 0", report)

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
        self.assertEqual(states["run"], {"exit_code": 0, "plugin_dir": str(run_evals.PLUGIN)})
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

    def test_a_missing_recording_is_not_a_pass_and_not_a_plugin_failure(self):
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--fixtures", str(self.tmp / "nowhere")], out), 1)
        self.assertIn("no recorded trace", out.getvalue())
        rows = [line for line in out.getvalue().splitlines() if line.startswith(tuple(helpers.ROWS))]
        self.assertEqual(len(rows), len(helpers.ROWS))
        for line in rows:
            self.assertIn("INCONCLUSIVE", line)
            self.assertNotIn(" FAIL", line)
        self.assertIn("failed 0", out.getvalue())

    def test_a_recording_without_its_state_file_is_inconclusive(self):
        self.run_main("--only", "wip-preexisting")
        (self.tmp / "out" / "wip-preexisting.state.json").unlink()
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(CSV), "--only", "wip-preexisting", "--fixtures", str(self.tmp / "out")], out), 1)
        row = self.row_of(out.getvalue(), "wip-preexisting")
        self.assertIn("INCONCLUSIVE", row)
        self.assertIn("state-recorded", row)

    def test_a_recording_from_another_checkout_is_graded_against_the_path_it_was_made_with(self):
        self.run_main("--only", "wip-preexisting")
        trace_file, state_file = self.tmp / "out" / "wip-preexisting.jsonl", self.tmp / "out" / "wip-preexisting.state.json"
        old = "/Users/someone/Git/old-name/plugins/auto-commit"
        lines = [json.loads(line) for line in trace_file.read_text().splitlines()]
        for event in lines:
            if event.get("subtype") == "init":
                event["plugins"][0]["path"] = old
        trace_file.write_text("\n".join(json.dumps(e) for e in lines) + "\n")
        states = json.loads(state_file.read_text())
        for recorded_dir, expected in ((old, 0), (None, 0), ("/elsewhere/auto-commit", 1)):
            if recorded_dir is None:
                states["run"].pop("plugin_dir")        # an old recording without the path: the comparison is skipped
            else:
                states["run"]["plugin_dir"] = recorded_dir
            state_file.write_text(json.dumps(states))
            out = io.StringIO()
            self.assertEqual(run_evals.main(["--cases", str(CSV), "--only", "wip-preexisting", "--fixtures", str(self.tmp / "out")], out), expected, (recorded_dir, out.getvalue()))

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
        log = dict(denials=[{"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}])
        row, events, states = helpers.good("behind-merge", after=REBASED, **log)
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "FAIL")
        self.assertIn("not-permission-blocked", [c["id"] for c in graded["checks"] if not c["pass"]])
        row, events, states = helpers.good("env-untracked", after=dict(all_paths=[".env", "README.md"]), **log)
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")
        # the same denial without a proven violation is still the harness's problem, not the plugin's
        row, events, states = helpers.good("wip-preexisting", **log)
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_a_denial_the_agent_got_around_does_not_spoil_a_perfect_run(self):
        # probes P1 and P2 of review round 4: denied once, redone with a successful call, outcome perfect: PASS (exit code 0), not INCONCLUSIVE
        heredoc = "git commit -m \"$(cat <<'EOF'\nchore: snapshot work in progress\nEOF\n)\""
        for case_id, commands, results, denied in (
                ("wip-preexisting", ["git add -A", heredoc, helpers.COMMIT, "git push -q origin main"], [True, False, True, True], heredoc),
                ("behind-merge", ["git fetch origin", "git merge origin/main", "git push -q origin main"], [False, True, True], "git fetch origin")):
            row, events, states = helpers.good(case_id, commands=commands, results=results, denials=[{"tool_name": "Bash", "tool_input": {"command": denied}}])
            graded = run_evals.grade_trial(row, events, states)
            self.assertEqual(graded["verdict"], "PASS", (case_id, [c for c in graded["checks"] if not c["pass"]]))
            note = next(c["notes"] for c in graded["checks"] if c["id"] == "not-permission-blocked")
            self.assertIn("redone by a later successful call (not blocking): 1", note)

    def test_a_denial_that_left_the_outcome_missing_is_still_inconclusive_and_a_violation_still_fails(self):
        heredoc = "git commit -m \"$(cat <<'EOF'\nchore: x\nEOF\n)\""
        denial = [{"tool_name": "Bash", "tool_input": {"command": heredoc}}]
        # denied, the retry failed (or never came), nothing was saved: the behaviour was never exercised
        for commands, results in ((["git add -A", heredoc], [True, False]), (["git add -A", heredoc, helpers.COMMIT], [True, False, False])):
            row, events, states = helpers.good("wip-preexisting", commands=commands, results=results, denials=denial, after=helpers.before_snapshot("dirty"))
            graded = run_evals.grade_trial(row, events, states)
            self.assertEqual(graded["verdict"], "INCONCLUSIVE", [c["id"] for c in graded["checks"] if not c["pass"]])
        # a redone denial does not excuse a rewritten history
        push = [{"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}]
        row, events, states = helpers.good("behind-merge", commands=["git push origin main", "git push origin main"], results=[False, True], denials=push, after=REBASED)
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

    def test_a_denied_read_only_call_does_not_turn_a_real_failure_into_inconclusive(self):
        # the report arrived, nothing was saved, and one `git log | head` was denied: that is the plugin's failure to act, not the harness's
        row, events, states = helpers.good("wip-preexisting", after=helpers.before_snapshot("dirty"), commands=["git log --oneline | head -5"],
                                           denials=[{"tool_name": "Bash", "tool_input": {"command": "git log --oneline | head -5"}}])
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "FAIL")
        failed = [c["id"] for c in graded["checks"] if not c["pass"]]
        self.assertIn("tree-clean", failed)
        self.assertNotIn("not-permission-blocked", failed)

    def test_the_behind_case_is_inconclusive_when_the_agent_never_got_to_integrate(self):
        # origin's own commit is unmerged by construction: not merging it must not read as a rewritten history
        untouched = helpers.before_snapshot("behind")
        denied_merge = [{"tool_name": "Bash", "tool_input": {"command": "git merge origin/main"}}]
        for name, kwargs in (("no hook", dict(hook="none", commands=[])), ("crash", dict(no_result=True, commands=[])),
                             ("denied merge", dict(commands=["git merge origin/main"], denials=denied_merge)),
                             ("exit code", dict(commands=[], result_kwargs=dict(subtype="error_max_budget_usd", is_error=True)))):
            row, events, states = helpers.good("behind-merge", after=untouched, **kwargs)
            graded = run_evals.grade_trial(row, events, states)
            failed = [c["id"] for c in graded["checks"] if not c["pass"]]
            self.assertEqual(graded["verdict"], "INCONCLUSIVE", (name, failed))
            self.assertNotIn("history-kept", failed, name)
        # the report arrived and the agent did nothing: a plugin failure, and not a history rewrite
        row, events, states = helpers.good("behind-merge", after=untouched, commands=[])
        graded = run_evals.grade_trial(row, events, states)
        failed = [c["id"] for c in graded["checks"] if not c["pass"]]
        self.assertEqual(graded["verdict"], "FAIL")
        self.assertNotIn("history-kept", failed)
        self.assertIn("merged-with-merge-commit", failed)

    def test_an_advisory_failure_alone_is_a_pass_with_a_warning(self):
        bad = dict(after=dict(log=[helpers.commit("w1", ["c2"], "update files", "")] + [helpers.C2, helpers.C1]))
        row, events, states = helpers.good("wip-preexisting", **bad)
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "PASS")
        self.assertEqual(graded["warnings"], ["commit-message-conventional"])
        self.assertEqual([c["id"] for c in graded["checks"] if not c["pass"]], ["commit-message-conventional"])
        row, events, states = helpers.good("wip-preexisting")
        self.assertEqual(run_evals.grade_trial(row, events, states)["warnings"], [])

    def test_a_soft_check_never_changes_any_verdict(self):
        # whatever else failed, adding a failed soft check leaves the verdict as it was
        ids = list(checks.CHECKS)
        for soft in checks.SOFT:
            for other, expected in ((None, "PASS"), ("run-completed", "INCONCLUSIVE"), ("tree-clean", "FAIL"), ("history-kept", "FAIL"), ("hook-report-delivered", "INCONCLUSIVE")):
                results = [{"id": cid, "pass": cid not in (soft, other), "notes": ""} for cid in ids]
                self.assertEqual(run_evals.verdict(results), expected, (soft, other))
            self.assertEqual(run_evals.warn_ids([{"id": soft, "pass": False, "notes": ""}]), [soft])

    def test_a_warning_is_reported_but_the_verdict_and_the_group_counts_ignore_it(self):
        bad = dict(after=dict(log=[helpers.commit("w1", ["c2"], "update files", "")] + [helpers.C2, helpers.C1]))
        row, events, states = helpers.good("wip-preexisting", **bad)
        trial = run_evals.grade_trial(row, events, states)
        trial.update(trial=1, run_note="")
        report = "\n".join(run_evals.format_report({"prompts": [{"id": row.id, "fixture": row.fixture, "expect": row.expect, "trials": [trial]}]}))
        line = next(line for line in report.splitlines() if line.startswith("wip-preexisting "))
        self.assertIn("PASS", line)
        self.assertTrue(line.rstrip().endswith("commit-message-conventional"), line)
        self.assertIn("[WARN]: commit-message-conventional", report)
        self.assertIn("advisory (soft) checks passed 0/1; WARN 1 in 1 trials", report)
        self.assertIn("trials passed 1/1", report)
        self.assertIn("trials with WARN 1", report)
        self.assertNotIn("soft", report.split("checks passed by group:")[1].splitlines()[0])

    def test_a_missing_or_unreadable_snapshot_is_inconclusive_not_a_failure_of_the_plugin(self):
        # every state check fails when nothing was measured; that must not be graded as a violation by the agent
        for case_id in ("wip-preexisting", "clean-noop", "behind-merge", "env-untracked"):
            row, events, states = helpers.good(case_id)
            for broken in ({}, {"before": states["before"]}, {**states, "after": {"head": "", "files": {}}}):
                graded = run_evals.grade_trial(row, events, broken)
                self.assertEqual(graded["verdict"], "INCONCLUSIVE", (case_id, sorted(broken), [c for c in graded["checks"] if not c["pass"]]))
            self.assertEqual(run_evals.grade_trial(row, [], {})["verdict"], "INCONCLUSIVE", case_id)
            self.assertEqual(run_evals.grade_trial(row, [], None)["verdict"], "INCONCLUSIVE", case_id)

    def test_a_violation_the_trace_proves_is_a_fail_even_without_snapshots(self):
        row, events, _ = helpers.good("wip-preexisting", commands=helpers.COMMANDS + ["git stash"])
        self.assertEqual(run_evals.grade_trial(row, events, {})["verdict"], "FAIL")
        row, events, _ = helpers.good("clean-noop", commands=["git add -A"])
        self.assertEqual(run_evals.grade_trial(row, events, {})["verdict"], "FAIL")

    def test_two_hooks_with_the_marker_are_inconclusive_unless_the_repository_proves_a_violation(self):
        row, events, states = helpers.good("wip-preexisting", hook="event-only")
        events.insert(2, helpers.hook_event())
        graded = run_evals.grade_trial(row, events, states)
        self.assertEqual(graded["verdict"], "INCONCLUSIVE")
        self.assertEqual([c["id"] for c in graded["checks"] if not c["pass"]], ["hook-report-delivered"])
        row, events, states = helpers.good("behind-merge", hook="event-only", after=REBASED)
        events.insert(2, helpers.hook_event())
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

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
