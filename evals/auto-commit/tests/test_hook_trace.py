"""hook_trace reads plugins, hook events, Bash calls and denials leniently: each reader has a passing and a failing input."""
import unittest

import helpers
import hook_trace

MARK = "AUTO-COMMIT HOOK"


def assistant(command, parent=None, name="Bash"):
    event = helpers.bash(command)
    event["message"]["content"][0]["name"] = name
    event["parent_tool_use_id"] = parent
    return event


class Plugins(unittest.TestCase):
    def test_names_lose_the_source_suffix_and_every_encoding_is_read(self):
        init = {"type": "system", "subtype": "init", "plugins": [{"name": "auto-commit", "source": "auto-commit@inline"}, {"source": "other@builtin"}, "third@inline", {}]}
        self.assertEqual(hook_trace.plugins_loaded([init]), (["auto-commit", "other", "third"], []))

    def test_entries_keep_their_source_and_path_and_a_string_entry_has_a_source(self):
        init = {"type": "system", "subtype": "init", "plugins": [{"name": "auto-commit", "source": "auto-commit@inline", "path": "/p"}, "x@builtin"]}
        self.assertEqual(hook_trace.plugin_entries([init]), [{"name": "auto-commit", "source": "auto-commit@inline", "path": "/p"}, {"name": "x", "source": "x@builtin"}])

    def test_errors_are_returned_raw_and_missing_fields_are_tolerated(self):
        self.assertEqual(hook_trace.plugins_loaded([helpers.init(errors=["boom"])])[1], ["boom"])
        self.assertEqual(hook_trace.plugins_loaded([{"type": "system", "subtype": "init"}]), ([], []))
        self.assertEqual(hook_trace.plugins_loaded([]), ([], []))


class Hooks(unittest.TestCase):
    def test_hook_responses_filter_by_hook_event_case_insensitively(self):
        stop, pre = helpers.hook_event(), {**helpers.hook_event(), "hook_event": "PreToolUse"}
        self.assertEqual(hook_trace.hook_responses([stop, pre, helpers.init()], "stop"), [stop])
        self.assertEqual(hook_trace.hook_responses([{"type": "system", "subtype": "hook_response"}], "Stop"), [])

    def test_the_report_in_the_conversation_is_fed_and_system_events_do_not_count(self):
        self.assertEqual(hook_trace.continuation([helpers.init(), helpers.hook_report(), helpers.result()], MARK), "fed")
        self.assertFalse(hook_trace.fed_to_model([helpers.hook_event()], MARK))

    def test_the_report_in_a_stop_hook_event_only(self):
        self.assertEqual(hook_trace.continuation([helpers.init(), helpers.hook_event()], MARK), "hook-event-only")
        self.assertEqual(hook_trace.continuation([{"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": MARK}], MARK), "hook-event-only")
        self.assertEqual(hook_trace.continuation([{"type": "system", "subtype": "hook_response", "hook_event": "Stop", "stderr": MARK}], MARK), "hook-event-only")

    def test_a_foreign_stop_hook_that_exits_2_without_the_marker_is_not_evidence(self):
        foreign = {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": "milestones: a reminder"}
        self.assertEqual(hook_trace.continuation([helpers.init(), foreign, helpers.result()], MARK), "none")

    def test_nothing_is_none(self):
        quiet = {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 0}
        pre = {"type": "system", "subtype": "hook_response", "hook_event": "PreToolUse", "exit_code": 2}
        self.assertEqual(hook_trace.continuation([helpers.init(), quiet, pre, helpers.result()], MARK), "none")


class Calls(unittest.TestCase):
    def test_bash_commands_are_the_main_conversations_in_order(self):
        events = [assistant("git status"), assistant("git add -A", parent="toolu_sub"), assistant("ls", name="Read"), assistant("git commit -m x")]
        self.assertEqual([c for _, c in hook_trace.bash_commands(events)], ["git status", "git commit -m x"])

    def test_denials_come_from_the_result_and_from_system_events(self):
        denied = {"tool_name": "Bash", "tool_input": {"command": "git push"}}
        system = {"type": "system", "subtype": "permission_denied", "tool_name": "Edit"}
        self.assertEqual(hook_trace.permission_denials([system, helpers.result(denials=[denied])]), [denied, system])
        self.assertEqual(hook_trace.permission_denials([helpers.init()]), [])


if __name__ == "__main__":
    unittest.main()
