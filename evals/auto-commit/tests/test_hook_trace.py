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

    def test_model_prose_and_command_output_are_not_the_report(self):
        # an agent that quotes the marker in its reply, or reads it out of a file, must not make itself the evidence
        quoted = {"type": "assistant", "message": {"content": [{"type": "text", "text": f"{MARK}: I saw this"}]}}
        output = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": f"{MARK} in a file"}]}}
        self.assertEqual(hook_trace.continuation([helpers.init(), quoted, output, helpers.result(text=f"{MARK} again")], MARK), "none")
        self.assertEqual(hook_trace.continuation([helpers.init(), quoted, helpers.hook_report()], MARK), "fed")
        plain = {"type": "user", "message": {"role": "user", "content": f"Stop hook feedback: {MARK}: unsaved"}}
        self.assertEqual(hook_trace.continuation([plain], MARK), "fed")

    def test_the_report_in_a_stop_hook_event_only(self):
        self.assertEqual(hook_trace.continuation([helpers.init(), helpers.hook_event()], MARK), "hook-event-only")
        self.assertEqual(hook_trace.continuation([{"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": MARK}], MARK), "hook-event-only")
        self.assertEqual(hook_trace.continuation([{"type": "system", "subtype": "hook_response", "hook_event": "Stop", "stderr": MARK}], MARK), "hook-event-only")

    def test_a_foreign_stop_hook_that_exits_2_without_the_marker_is_not_evidence(self):
        foreign = {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": "milestones: a reminder"}
        self.assertEqual(hook_trace.continuation([helpers.init(), foreign, helpers.result()], MARK), "none")

    def test_only_an_exit_code_other_than_0_and_2_is_an_infrastructure_error(self):
        quiet = {**helpers.hook_event(), "exit_code": 0, "outcome": "success"}
        other = {**helpers.hook_infra_error(), "hook_event": "PreToolUse"}
        odd = {**helpers.hook_infra_error(), "exit_code": True}
        broken = helpers.hook_infra_error()
        # the plugin's own report is exit_code 2 with outcome 'error': not an infrastructure error
        self.assertEqual(hook_trace.hook_errors([helpers.hook_event(), quiet, other, odd, {"type": "system", "subtype": "hook_response", "hook_event": "Stop"}], "Stop"), [])
        self.assertEqual(hook_trace.hook_errors([helpers.hook_event(), broken, {**broken, "exit_code": 127}], "Stop"), [broken, {**broken, "exit_code": 127}])

    def test_the_error_text_is_trimmed_and_prefers_output(self):
        self.assertEqual(hook_trace.error_text(helpers.hook_infra_error()), helpers.EPERM)
        self.assertEqual(hook_trace.error_text({"stderr": "a\n\n  b", "stdout": "c"}), "a b")
        self.assertEqual(hook_trace.error_text({"stdout": "only stdout"}), "only stdout")
        self.assertEqual(hook_trace.error_text({"output": "x" * 500}), "x" * 200 + "...")
        self.assertEqual(hook_trace.error_text({}), "")

    def test_the_recorded_event_sequence_is_report_then_a_silent_second_stop(self):
        # real shape (2.1.330): started, response exit 2 with the report, started, response exit 0 and empty
        quiet = {**helpers.hook_event(), "output": "", "stderr": "", "exit_code": 0, "outcome": "success"}
        events = [helpers.init(), helpers.hook_started(), helpers.hook_event(), helpers.hook_started(), quiet, helpers.result()]
        self.assertEqual(hook_trace.continuation(events, MARK), "hook-event-only")
        self.assertEqual(len(hook_trace.marked_responses(events, "Stop", MARK)), 1)
        self.assertEqual(hook_trace.hook_errors(events, "Stop"), [])

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
