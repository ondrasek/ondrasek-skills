"""Comparing two runs: better, worse and same per prompt, and the exit code."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from helpers import PROMPTS, TRACES
import report
import run_evals


def results_file(directory, name, traces, label, trials=1):
    path = Path(directory) / f"{name}.json"
    run_evals.main(["--prompts", str(PROMPTS), "--fixtures", str(traces), "--label", label, "--results", str(path), "--trials", str(trials)],
                   stream=io.StringIO())
    return path


def synthetic(trials_by_id):
    """A results dict: {id: [(passed, [failing check ids])]}."""
    prompts = []
    for pid, trials in trials_by_id.items():
        prompts.append({"id": pid, "trials": [{"passed": ok, "checks": [{"id": c, "pass": False, "notes": ""} for c in failing]}
                                              for ok, failing in trials]})
    return {"label": "x", "prompts": prompts}


class CompareTests(unittest.TestCase):
    def verdicts(self, before, after):
        index = lambda data: {p["id"]: p for p in data["prompts"]}  # noqa: E731
        return {r[0]: r[1] for r in report.compare(index(before), index(after))}

    def test_a_larger_share_is_better_a_smaller_share_is_worse(self):
        before = synthetic({"a": [(False, ["x"])], "b": [(True, [])], "c": [(True, []), (False, ["y"])], "d": [(True, [])]})
        after = synthetic({"a": [(True, [])], "b": [(False, ["z"])], "c": [(True, []), (True, [])], "d": [(True, [])]})
        self.assertEqual(self.verdicts(before, after), {"a": "better", "b": "worse", "c": "better", "d": "same"})

    def test_shares_are_compared_not_counts(self):
        before = synthetic({"a": [(True, []), (False, ["x"])]})            # 1/2
        after = synthetic({"a": [(True, [])] * 2 + [(False, ["x"])] * 2})   # 2/4
        self.assertEqual(self.verdicts(before, after), {"a": "same"})

    def test_new_and_gone_prompts(self):
        before = synthetic({"old": [(True, [])]})
        after = synthetic({"fresh": [(True, [])]})
        self.assertEqual(self.verdicts(before, after), {"old": "gone", "fresh": "new"})


class FixtureRunsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def compare(self, first, second):
        stream = io.StringIO()
        code = report.main([str(first), str(second)], stream=stream)
        return code, stream.getvalue()

    def test_before_to_after_is_an_improvement_with_exit_code_0(self):
        before = results_file(self.tmp.name, "before", TRACES / "before", "before")
        after = results_file(self.tmp.name, "after", TRACES / "after", "after")
        code, text = self.compare(before, after)
        self.assertEqual(code, 0)
        self.assertIn("summary: 5 better, 0 worse, 6 same", text)
        self.assertRegex(text, r"followup-stop\s+0/1\s+1/1\s+better\s+fixed: wrapup-headings")

    def test_after_to_before_is_a_regression_with_exit_code_1(self):
        before = results_file(self.tmp.name, "before", TRACES / "before", "before")
        after = results_file(self.tmp.name, "after", TRACES / "after", "after")
        code, text = self.compare(after, before)
        self.assertEqual(code, 1)
        self.assertIn("5 worse", text)
        self.assertRegex(text, r"followup-stop\s+1/1\s+0/1\s+worse\s+now failing: wrapup-headings")

    def test_the_run_labels_head_the_columns(self):
        a = results_file(self.tmp.name, "a", TRACES / "after", "skill-v1")
        b = results_file(self.tmp.name, "b", TRACES / "after", "skill-v2")
        self.assertIn("skill-v1", self.compare(a, b)[1].splitlines()[0])

    def test_a_missing_file_is_exit_code_2(self):
        self.assertEqual(self.compare(Path(self.tmp.name) / "none.json", Path(self.tmp.name) / "none2.json")[0], 2)

    def test_results_from_trials_compare_by_share(self):
        a = results_file(self.tmp.name, "a", TRACES / "after", "a", trials=2)
        data = json.loads(a.read_text(encoding="utf-8"))
        self.assertEqual({len(p["trials"]) for p in data["prompts"]}, {2})
        self.assertEqual(self.compare(a, a)[0], 0)


if __name__ == "__main__":
    unittest.main()
