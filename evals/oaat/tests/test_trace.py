"""The oaat harness and the shared stream-json reader (evals/_common/python): fixtures it reads, and that no local copy is left.

The reader's own tests (every field it uses, the cost rule) live in evals/_common/tests."""
import json
import unittest
from pathlib import Path

import helpers
import agent_trace as trace


class FixtureTests(unittest.TestCase):
    def test_load_reads_a_fixture_file(self):
        path = helpers.TRACES / "after" / "negative-single.jsonl"
        self.assertEqual(trace.load(path)[0]["subtype"], "init")

    def test_a_two_turn_fixture_counts_the_cost_of_its_last_result_event(self):
        events = trace.load(helpers.TRACES / "after" / "followup-skip.jsonl")
        results = [e for e in events if e.get("type") == "result"]
        self.assertEqual(len(results), 2)
        self.assertEqual(trace.stats(events)[1], results[-1]["total_cost_usd"])

    def test_the_fixture_inputs_are_marked_as_invented(self):
        for path in sorted(helpers.TRACES.glob("after/*.jsonl")) + sorted(helpers.TRACES.glob("before/*.jsonl")):
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(first["session_id"].startswith("INVENTED"), path)
            self.assertEqual(first["model"], "invented-model")


class Shared(unittest.TestCase):
    def test_the_parser_and_the_command_runner_are_the_shared_ones(self):
        import agent_trace
        import runner_cmd
        for module in (agent_trace, runner_cmd):
            self.assertEqual(Path(module.__file__).resolve().parent, helpers.UNIT.parent / "_common" / "python", module.__name__)
            self.assertFalse((helpers.UNIT / "python" / f"{module.__name__}.py").exists(), "a local copy: delete it")


if __name__ == "__main__":
    unittest.main()
