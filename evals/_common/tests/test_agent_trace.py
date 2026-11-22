"""The stream-json reader: the fields it uses, and what it does with a trace that lacks them. Invented events, no model call."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import agent_trace as trace  # noqa: E402


def make_events(reply, called=("oaat:oaat",), turns=2, cost=0.01, others=(), subtype="success", session="INVENTED-unit-test"):
    """One invented turn: init, one tool_use per called skill and other tool, the reply, the result event."""
    events = [{"type": "system", "subtype": "init", "skills": ["oaat:oaat"], "model": "invented-model", "session_id": session}]
    uses = [{"type": "tool_use", "id": f"toolu_{i}", "name": "Skill", "input": {"skill": skill}} for i, skill in enumerate(called)]
    uses += [{"type": "tool_use", "id": f"toolu_o{i}", "name": name, "input": {}} for i, name in enumerate(others)]
    if uses:
        events.append({"type": "assistant", "parent_tool_use_id": None, "message": {"content": uses}})
    events.append({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": reply}]}})
    events.append({"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": reply, "num_turns": turns,
                   "total_cost_usd": cost, "session_id": session})
    return events


def two():
    """A resumed session as the runners record it: both streams, one after the other. The second cost is the session's so far."""
    return make_events("first reply", turns=3, cost=0.01) + make_events("second reply", called=(), turns=1, cost=0.03)


class ParseTests(unittest.TestCase):
    def test_every_line_is_an_event_and_a_broken_line_is_counted(self):
        events, bad = trace.parse('{"type": "system"}\n\nnot json\n[1, 2]\n{"type": "result"}\n')
        self.assertEqual([e["type"] for e in events], ["system", "result"])
        self.assertEqual(bad, 2)

    def test_load_reads_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in make_events("x")), encoding="utf-8")
            self.assertEqual(trace.load(path), make_events("x"))


class TwoTurnTests(unittest.TestCase):
    def test_the_reply_is_the_last_turns_and_the_earlier_reply_is_the_first(self):
        self.assertEqual(trace.final_reply(two()), "second reply")
        self.assertEqual(trace.earlier_reply(two()), "first reply")
        self.assertEqual(trace.result_texts(two()), ["first reply", "second reply"])
        self.assertEqual(trace.earlier_reply(make_events("only")), "")

    def test_the_session_id_is_the_first_one_in_the_trace(self):
        self.assertEqual(trace.session_id(two()), "INVENTED-unit-test")
        self.assertEqual(trace.session_id([]), "")


class StatsTests(unittest.TestCase):
    def test_stats_come_from_the_result_event(self):
        self.assertEqual(trace.stats(make_events("x", turns=3, cost=0.5)), (3, 0.5))

    def test_the_cost_of_a_resumed_session_is_the_last_result_events_not_the_sum(self):
        # measured on real two-turn traces: the second result event's total_cost_usd already holds the first turn's
        self.assertEqual(trace.stats(two()), (1, 0.03))

    def test_the_turns_are_the_last_result_events(self):
        self.assertEqual(trace.stats(two())[0], 1)

    def test_a_last_result_event_without_a_cost_leaves_the_cost_of_the_one_before(self):
        events = two()
        del events[-1]["total_cost_usd"]
        self.assertEqual(trace.stats(events), (1, 0.01))
        events[-1]["total_cost_usd"] = "0.03"       # not a number: not a cost
        self.assertEqual(trace.stats(events), (1, 0.01))

    def test_values_that_are_missing_or_of_the_wrong_type_are_none(self):
        self.assertEqual(trace.stats([]), (None, None))
        self.assertEqual(trace.stats(make_events("x")[:-1]), (None, None))
        self.assertEqual(trace.stats([{"type": "result", "num_turns": "3", "total_cost_usd": None}]), (None, None))

    def test_a_zero_cost_is_a_cost(self):
        self.assertEqual(trace.stats(make_events("x", turns=1, cost=0)), (1, 0))


class QuestionTests(unittest.TestCase):
    def test_skill_calls_are_read_from_the_tool_use_blocks(self):
        self.assertEqual(trace.skills_invoked(make_events("x", called=("oaat:oaat", "sitrep"))), ["oaat:oaat", "sitrep"])

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

    def test_an_empty_trace_has_no_reply_and_no_init(self):
        self.assertEqual(trace.final_reply([]), "")
        self.assertIsNone(trace.init_event([]))
        self.assertIsNone(trace.init_skills([]))
        self.assertIsNone(trace.result_event([]))

    def test_the_init_skills_are_listed_and_a_missing_list_is_empty(self):
        self.assertEqual(trace.init_skills(make_events("x")), ["oaat:oaat"])
        self.assertEqual(trace.init_skills([{"type": "system", "subtype": "init"}]), [])

    def test_tool_calls_list_every_tool_in_order(self):
        events = make_events("x", others=("Read", "Bash"))
        self.assertEqual([name for name, _ in trace.tool_calls(events)], ["Skill", "Read", "Bash"])

    def test_a_string_content_is_one_text_block(self):
        self.assertEqual(trace.blocks({"message": {"content": "plain"}}), [{"type": "text", "text": "plain"}])
        self.assertEqual(trace.blocks({"message": None}), [])


if __name__ == "__main__":
    unittest.main()
