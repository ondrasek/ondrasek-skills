"""End-to-end, offline: run_evals drives a stub agent in real temporary git repositories, snapshots them, grades them."""
import contextlib
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

import helpers
import checks
import run_evals

HERE = Path(__file__).resolve().parent
CSV = ("id,expect,fixture,want,prompt\n"
       "show,show,tracker,,where are we\n"
       'update,update,tracker,"2=done,3=doing",update it\n'
       'create,create,bare,"title=Importer,count=3,names=schema|importer|migration",create it\n'
       "none,none,bare,,what is a milestone\n"
       "reminded-update,reminded-update,stale-task,2=done,add slugify\n"
       "reminded-quiet,reminded-quiet,stale-idle,,what does it do\n")
AGENT = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'stub_agent.py'))} {{prompt}}"
ENV_REPORT = ("import json, os, sys; print(json.dumps({'type': 'x', 'git': os.environ.get('GIT_CONFIG_GLOBAL'), 'system': os.environ.get('GIT_CONFIG_SYSTEM'), "
              "'id': os.environ.get('EVAL_PROMPT_ID'), 'trial': os.environ.get('EVAL_TRIAL'), 'cwd': os.getcwd(), 'args': sys.argv[1:]}))")
# STUB_MODE -> (the case it breaks, its verdict, the checks that fail); every other case passes
MODES = {
    "hand-edit": ("update", "FAIL", {"no-direct-tracker-write", "written-by-script", "timestamps-by-script"}),
    "wrong-item": ("update", "FAIL", {"wanted-state", "others-untouched"}),
    "commits-tracker": ("update", "FAIL", {"tracker-not-committed"}),
    "invents-update": ("reminded-quiet", "FAIL", {"quiet-when-nothing-changed", "no-write-attempt"}),
    "ignores-reminder": ("reminded-update", "FAIL", {"wanted-state", "timestamps-by-script"}),
    "no-marker": ("reminded-update", "INCONCLUSIVE", {"reminder-fired", "reminder-delivered", "wanted-state", "timestamps-by-script"}),
    # a negative run that started and then did nothing passes every other check of a none case: only agent-completed can tell
    "init-only": ("none", "INCONCLUSIVE", {"agent-completed"}),
    "budget-stop": ("none", "INCONCLUSIVE", {"agent-completed"}),
    "crash": ("none", "INCONCLUSIVE", {"agent-completed"}),
}


class Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.csv = self.tmp / "cases.csv"
        self.csv.write_text(CSV, encoding="utf-8")
        self.out = self.tmp / "out"

    def run_main(self, *extra, mode="good", agent=AGENT, env=None):
        out = io.StringIO()
        argv = ["--cases", str(self.csv), "--agent-cmd", agent, "--out", str(self.out), *extra]
        with mock.patch.dict(os.environ, {"STUB_MODE": mode, **(env or {})}):
            code = run_evals.main(argv, out)
        return code, out.getvalue()

    def results(self):
        return {p["id"]: p for p in json.loads((self.out / "results.json").read_text())["prompts"]}

    def failed(self, case):
        return {c["id"] for t in self.results()[case]["trials"] for c in t["checks"] if not c["pass"]}

    def test_a_good_agent_passes_every_check(self):
        code, report = self.run_main()
        self.assertEqual(code, 0, report)
        self.assertIn("pass rate 100%", report)
        self.assertIn("cases passed 6/6", report)

    def test_each_misbehaviour_is_caught_from_the_files(self):
        for mode, (case, verdict, failed) in MODES.items():
            with self.subTest(mode=mode):
                code, report = self.run_main("--only", case, mode=mode)
                self.assertEqual(code, 1, report)
                self.assertEqual(self.results()[case]["trials"][0]["verdict"], verdict, report)
                self.assertEqual(self.failed(case), failed, report)

    def test_a_wrong_reply_is_a_warning_not_a_failure(self):
        code, report = self.run_main("--only", "show", mode="wrong-reply")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.results()["show"]["trials"][0]["verdict"], "PASS")
        self.assertEqual(self.results()["show"]["trials"][0]["warnings"], ["reply-names-current"])
        self.assertRegex(report, r"show\s+show\s+1/1\s+PASS\s+-\s+reply-names-current")
        self.assertIn("warnings (advisory", report)
        self.assertIn("WARN 1 advisory checks in 1 trials", report)
        self.assertIn("soft 0/1", report)
        self.assertIn("cases passed 1/1", report)

    def test_a_mode_breaks_only_its_own_case(self):
        code, report = self.run_main(mode="hand-edit")
        self.assertEqual(code, 1, report)
        self.assertEqual({i: p["trials"][0]["verdict"] for i, p in self.results().items()},
                         {"show": "PASS", "update": "FAIL", "create": "PASS", "none": "PASS", "reminded-update": "PASS", "reminded-quiet": "PASS"})

    def test_the_report_prints_inconclusive_apart_from_fail(self):
        _, report = self.run_main("--only", "reminded-update", mode="no-marker")
        self.assertRegex(report, r"reminded-update\s+reminded-update\s+0/1\s+INCONCLUSIVE")
        self.assertIn("inconclusive 1", report)
        _, report = self.run_main("--only", "update", mode="wrong-item")
        self.assertRegex(report, r"update\s+update\s+0/1\s+FAIL")
        self.assertIn("failed 1", report)

    def test_artifacts_hold_the_trace_and_both_snapshots(self):
        self.run_main("--only", "update")
        self.assertTrue((self.out / "update.jsonl").is_file())
        states = json.loads((self.out / "update.state.json").read_text())
        self.assertEqual(set(states), {"before", "after"})
        before, after = (json.loads(states[k]["files"][".cache/milestones.json"]["text"]) for k in ("before", "after"))
        self.assertEqual([i["state"] for i in before["items"]], ["done", "doing", "todo", "todo"])
        self.assertEqual([i["state"] for i in after["items"]], ["done", "done", "doing", "todo"])
        self.assertEqual(len(states["after"]["head"]), 40)
        self.assertEqual(list(self.results()), ["update"])

    def test_several_trials_are_numbered(self):
        self.run_main("--only", "none", "--trials", "2")
        self.assertTrue((self.out / "none.1.jsonl").is_file() and (self.out / "none.2.jsonl").is_file())

    def test_recorded_runs_grade_offline_to_the_same_verdicts(self):
        for mode in ("good", "no-marker", "hand-edit"):
            with self.subTest(mode=mode):
                live_code, live = self.run_main("--only", "update,reminded-update", mode=mode)
                out = io.StringIO()
                code = run_evals.main(["--cases", str(self.csv), "--only", "update,reminded-update", "--fixtures", str(self.out)], out)
                self.assertEqual(code, live_code, out.getvalue())
                self.assertEqual(out.getvalue().split("cost")[0], live.split("cost")[0])

    def test_a_recording_without_its_state_file_is_inconclusive_not_a_pass(self):
        # M2: states = {} would compare None with None in every state check
        self.run_main("--only", "update")
        (self.out / "update.state.json").unlink()
        out = io.StringIO()
        code = run_evals.main(["--cases", str(self.csv), "--only", "update", "--fixtures", str(self.out)], out)
        self.assertEqual(code, 1)
        self.assertIn("INCONCLUSIVE", out.getvalue())
        self.assertIn("update.state.json next to the trace", out.getvalue())
        (self.out / "update.state.json").write_text("{}", encoding="utf-8")
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--only", "update", "--fixtures", str(self.out)], out), 1)
        self.assertIn("INCONCLUSIVE", out.getvalue())

    def test_a_trace_fact_still_fails_when_the_state_file_is_missing(self):
        # the Write of the tracker is in the trace whatever the snapshots say: not explained by the missing state
        self.run_main("--only", "update", mode="hand-edit")
        (self.out / "update.state.json").unlink()
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--only", "update", "--fixtures", str(self.out)], out), 1)
        self.assertRegex(out.getvalue(), r"update\s+update\s+0/1\s+FAIL")

    def test_a_failed_run_stays_inconclusive_when_regraded_offline(self):
        self.run_main("--only", "none", mode="crash")
        self.assertIn("run_note", json.loads((self.out / "none.state.json").read_text()))
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--only", "none", "--fixtures", str(self.out)], out), 1)
        self.assertIn("INCONCLUSIVE", out.getvalue())
        self.assertIn("agent command exited 3", out.getvalue())

    def test_a_missing_recording_fails(self):
        out = io.StringIO()
        self.assertEqual(run_evals.main(["--cases", str(self.csv), "--fixtures", str(self.tmp / "nowhere")], out), 1)
        self.assertIn("no recorded trace", out.getvalue())

    def test_unknown_case_id_bad_trials_and_a_bad_table(self):
        self.assertEqual(self.run_main("--only", "zzz")[0], 2)
        self.assertEqual(self.run_main("--trials", "0")[0], 2)
        self.csv.write_text("id\nx\n", encoding="utf-8")
        self.assertEqual(self.run_main()[0], 2)

    def test_a_missing_agent_program_stops_the_run_and_leaves_no_trial_directory(self):
        base = self.tmp / "trials"
        base.mkdir()
        real = tempfile.mkdtemp
        with mock.patch.object(tempfile, "mkdtemp", lambda prefix: real(prefix=prefix, dir=base)), self.assertRaises(SystemExit):
            self.run_main("--only", "none", agent="no-such-program-xyz {prompt}")
        self.assertEqual(list(base.iterdir()), [])

    def test_the_trial_directory_is_removed_unless_kept(self):
        agent = shlex.join([sys.executable, "-c", ENV_REPORT, "{prompt}"])
        self.run_main("--only", "none", agent=agent)
        cwd = Path(json.loads((self.out / "none.jsonl").read_text())["cwd"])
        self.assertFalse(cwd.parent.exists())
        with contextlib.redirect_stderr(io.StringIO()) as notice:
            self.run_main("--only", "none", "--keep", agent=agent)
        self.assertIn("kept", notice.getvalue())
        kept = Path(json.loads((self.out / "none.jsonl").read_text())["cwd"])
        self.addCleanup(shutil.rmtree, kept.parent, True)
        self.assertTrue((kept.parent / "hooks").is_dir())
        self.assertTrue((kept / ".git").is_dir())

    def test_the_agent_runs_in_the_workspace_with_the_git_config_nulled_and_the_plugin_dir_appended(self):
        agent = shlex.join([sys.executable, "-c", ENV_REPORT, "{prompt}"])
        self.run_main("--only", "none", agent=agent)
        seen = json.loads((self.out / "none.jsonl").read_text())
        self.assertEqual((seen["git"], seen["system"], seen["id"], seen["trial"]), (os.devnull, os.devnull, "none", "1"))
        self.assertEqual(Path(seen["cwd"]).name, "ws")
        self.assertEqual(seen["args"], ["what is a milestone", "--plugin-dir", str(helpers.PLUGIN_DIR)])

    def test_the_caller_environment_cannot_redirect_the_script_or_the_hook_stand_in(self):
        decoy_project, decoy_file = self.tmp / "decoy-project", self.tmp / "decoy.json"
        code, report = self.run_main("--only", "update,create", env={"CLAUDE_PROJECT_DIR": str(decoy_project), "MILESTONES_FILE": str(decoy_file)})
        self.assertEqual(code, 0, report)
        self.assertFalse(decoy_project.exists())
        self.assertFalse(decoy_file.exists())


class Verdict(unittest.TestCase):
    def results(self, *failed):
        return [{"id": cid, "pass": cid not in failed, "notes": ""} for cid in checks.GROUPS]

    def test_pass_fail_inconclusive(self):
        self.assertEqual(run_evals.verdict(self.results()), "PASS")
        self.assertEqual(run_evals.verdict(self.results("wanted-state", "show-reads-only")), "FAIL")
        self.assertEqual(run_evals.verdict(self.results("reminder-fired")), "INCONCLUSIVE")

    def test_a_failed_precondition_masks_only_what_it_explains(self):
        # the denial explains the missing outcome, not the agent's own direct write or a changed file
        self.assertEqual(run_evals.verdict(self.results("not-permission-blocked", "wanted-state", "shown-by-script")), "INCONCLUSIVE")
        # a denial leaves changes out: it never explains an item that changed, a bad tracker or a write by other means (R4-1)
        for explained_by in ("not-permission-blocked", "reminder-fired", "reminder-delivered", "plugin-under-test-isolated"):
            for wrong in ("others-untouched", "tracker-valid", "tracker-created", "timestamps-by-script", "written-by-script", "quiet-when-nothing-changed"):
                self.assertEqual(run_evals.verdict(self.results(explained_by, "wanted-state", wrong)), "FAIL", (explained_by, wrong))
        self.assertEqual(run_evals.verdict(self.results("not-permission-blocked", "no-direct-tracker-write", "tracker-valid")), "FAIL")
        self.assertEqual(run_evals.verdict(self.results("reminder-fired", "no-direct-tracker-write", "written-by-script", "timestamps-by-script")), "FAIL")
        self.assertEqual(run_evals.verdict(self.results("agent-completed", "nothing-else-changed")), "FAIL")
        self.assertEqual(run_evals.verdict(self.results("agent-completed", "skill-trigger", "wanted-state")), "INCONCLUSIVE")

    def test_every_check_alone_gives_its_group_verdict(self):
        for cid, group in checks.GROUPS.items():
            want = {"precondition": "INCONCLUSIVE", "soft": "PASS"}.get(group, "FAIL")
            self.assertEqual(run_evals.verdict(self.results(cid)), want, cid)

    def test_a_warn_only_trial_is_a_pass_and_never_changes_a_verdict(self):
        soft = [cid for cid, group in checks.GROUPS.items() if group == "soft"]
        self.assertEqual(run_evals.verdict(self.results(*soft)), "PASS")
        self.assertEqual(run_evals.verdict(self.results(*soft, "wanted-state")), "FAIL")
        self.assertEqual(run_evals.verdict(self.results(*soft, "reminder-fired")), "INCONCLUSIVE")
        self.assertEqual(run_evals.warnings_of(self.results(*soft, "wanted-state")), soft)

    def test_a_case_is_as_bad_as_its_worst_trial(self):
        trial = lambda v: {"verdict": v}  # noqa: E731
        self.assertEqual(run_evals.prompt_verdict({"trials": [trial("PASS"), trial("INCONCLUSIVE")]}), "INCONCLUSIVE")
        self.assertEqual(run_evals.prompt_verdict({"trials": [trial("INCONCLUSIVE"), trial("FAIL")]}), "FAIL")
        self.assertEqual(run_evals.prompt_verdict({"trials": [trial("PASS")]}), "PASS")


class Grading(unittest.TestCase):
    def test_the_cost_is_that_of_the_last_result_event_not_the_sum(self):
        row, events, states = helpers.good("show")
        events.insert(-1, {**helpers.result("first"), "total_cost_usd": 1.0})
        events[-1]["total_cost_usd"] = 3.0
        self.assertEqual(run_evals.grade_trial(row, events, states)["cost"], 3.0)

    def test_the_default_agent_command_is_isolated_and_grants_what_the_cases_need(self):
        tokens = shlex.split(run_evals.DEFAULT_AGENT_CMD)
        self.assertEqual(tokens[:2], ["env", "-u"])
        unset = {tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t == "-u"}
        self.assertEqual(unset, {"CLAUDE_PROJECT_DIR", "CLAUDE_HOOK_RUNNING", "MILESTONES_FILE", "MILESTONES_NOW", "MILESTONES_STALE_MINUTES",
                                 "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX"})
        self.assertIn("--include-hook-events", tokens)
        self.assertIn("--verbose", tokens)
        self.assertNotIn("--bare", tokens)
        self.assertNotIn("--add-dir", tokens)
        self.assertEqual(tokens[tokens.index("--setting-sources") + 1], "project,local")
        self.assertEqual(tokens[tokens.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(tokens[tokens.index("--max-budget-usd") + 1], "2")
        self.assertLess(tokens.index("{prompt}"), tokens.index("--allowedTools"))
        allowed = tokens[tokens.index("--allowedTools") + 1]
        # the narrow script spellings (a quoted absolute path puts the closing quote before the verb), plus the whole of python3 and the
        # variable assignment: the real runs called `S="/abs/milestones.py"; python3 "$S" set ... && python3 "$S" show`, which no
        # path-shaped pattern matches; and the read-only helpers an agent chains
        grants = allowed.split(",")
        for grant in ("Skill", "Read", "Edit", "Write", "Bash(python3 *milestones.py *)", 'Bash(python3 *milestones.py" *)', "Bash(python3 *)", "Bash(S=*)",
                      "Bash(git log *)", "Bash(git diff *)", "Bash(git check-ignore *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(grep *)",
                      "Bash(git add *)", "Bash(git commit *)"):
            self.assertIn(grant, grants)
        # nothing broader than that: no bare Bash, no shell, no network or file-deleting tool
        for broad in ("Bash", "Bash(*)", "Bash(bash *)", "Bash(sh *)", "Bash(curl *)", "Bash(rm *)", "Bash(git push *)"):
            self.assertNotIn(broad, grants)
        # --allowedTools takes a list: the next token must be a flag, or it would swallow the appended --plugin-dir
        self.assertTrue(tokens[tokens.index("--allowedTools") + 2].startswith("--"))
        built = run_evals.runner_cmd.build(run_evals.DEFAULT_AGENT_CMD, "the prompt", ["--plugin-dir", "P"])
        self.assertEqual(built[-2:], ["--plugin-dir", "P"])
        self.assertIn("the prompt", built)
        self.assertTrue((run_evals.PLUGIN / ".claude-plugin" / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
