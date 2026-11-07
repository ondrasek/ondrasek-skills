"""The stream-json reader: the fields it uses, and what it does with a trace that lacks them."""
import json
import unittest

import helpers  # noqa: F401  (puts python/ on the path)
import agent_trace as trace
from helpers import make_events


class ParseTests(unittest.TestCase):
    def test_every_line_is_an_event_and_a_broken_line_is_counted(self):
        events, bad = trace.parse('{"type": "system"}\n\nnot json\n[1, 2]\n{"type": "result"}\n')
        self.assertEqual([e["type"] for e in events], ["system", "result"])
        self.assertEqual(bad, 2)

    def test_load_reads_a_file(self):
        path = helpers.TRACES / "after" / "negative-single.jsonl"
        self.assertEqual(trace.load(path)[0]["subtype"], "init")


class TwoTurnTests(unittest.TestCase):
    def two(self):
        return make_events("first reply", cost=0.01) + make_events("second reply", called=(), cost=0.02)

    def test_the_reply_is_the_last_turns_and_the_earlier_reply_is_the_first(self):
        events = self.two()
        self.assertEqual(trace.final_reply(events), "second reply")
        self.assertEqual(trace.earlier_reply(events), "first reply")
        self.assertEqual(trace.earlier_reply(make_events("only")), "")

    def test_the_cost_adds_up_over_both_turns(self):
        self.assertAlmostEqual(trace.stats(self.two())[1], 0.03)

    def test_the_session_id_is_the_first_one_in_the_trace(self):
        self.assertEqual(trace.session_id(self.two()), "INVENTED-unit-test")
        self.assertEqual(trace.session_id([]), "")


class QuestionTests(unittest.TestCase):
    def test_skill_calls_are_read_from_the_tool_use_blocks(self):
        events = make_events("x", called=("oaat:oaat", "sitrep"))
        self.assertEqual(trace.skills_invoked(events), ["oaat:oaat", "sitrep"])

    def test_a_plugin_skill_matches_by_the_name_after_the_colon(self):
        self.assertTrue(trace.skill_matches("oaat:oaat", "oaat"))
        self.assertTrue(trace.skill_matches("oaat", "oaat"))
        self.assertFalse(trace.skill_matches("oaat-lite", "oaat"))
        self.assertFalse(trace.skill_matches("sitrep:sitrep", "oaat"))

    def test_a_subagent_message_is_not_the_main_conversation(self):
        events = make_events("x", called=())
        events.insert(1, {"type": "assistant", "parent_tool_use_id": "toolu_1",
                          "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "oaat"}}]}})
        self.assertEqual(trace.skills_invoked(events), [])

    def test_the_reply_is_the_result_text(self):
        self.assertEqual(trace.final_reply(make_events("the reply")), "the reply")

    def test_without_a_success_result_the_reply_is_the_last_assistant_text(self):
        events = make_events("the reply", subtype="error_max_turns")
        del events[-1]["result"]
        self.assertEqual(trace.final_reply(events), "the reply")

    def test_an_empty_trace_has_no_reply_no_init_and_no_stats(self):
        self.assertEqual(trace.final_reply([]), "")
        self.assertIsNone(trace.init_skills([]))
        self.assertEqual(trace.stats([]), (None, None))

    def test_stats_come_from_the_result_event(self):
        self.assertEqual(trace.stats(make_events("x", turns=3, cost=0.5)), (3, 0.5))

    def test_tool_calls_list_every_tool_in_order(self):
        events = make_events("x", others=("Read", "Bash"))
        self.assertEqual([name for name, _ in trace.tool_calls(events)], ["Skill", "Read", "Bash"])

    def test_the_fixture_inputs_are_marked_as_invented(self):
        for path in sorted(helpers.TRACES.glob("after/*.jsonl")) + sorted(helpers.TRACES.glob("before/*.jsonl")):
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(first["session_id"].startswith("INVENTED"), path)
            self.assertEqual(first["model"], "invented-model")


if __name__ == "__main__":
    unittest.main()
