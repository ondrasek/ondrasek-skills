import unittest

import helpers
import hook_trace as hooks

NEEDLE = "MILESTONES HOOK"


class Plugins(unittest.TestCase):
    def test_names_come_from_the_name_field(self):
        self.assertEqual(hooks.plugins_loaded([helpers.init(plugins=("milestones", "oaat"))]), (["milestones", "oaat"], []))

    def test_a_missing_name_falls_back_to_the_source_before_the_at_sign(self):
        init = {"type": "system", "subtype": "init", "plugins": [{"source": "milestones@inline"}, "other@inline"], "plugin_errors": ["boom"]}
        self.assertEqual(hooks.plugins_loaded([init]), (["milestones", "other"], ["boom"]))

    def test_no_init_event_means_nothing_loaded(self):
        self.assertEqual(hooks.plugins_loaded([]), ([], []))

    def test_an_init_without_plugins(self):
        self.assertEqual(hooks.plugins_loaded([{"type": "system", "subtype": "init"}]), ([], []))


class PluginEntries(unittest.TestCase):
    def test_name_source_and_path(self):
        init = {"type": "system", "subtype": "init", "plugins": [{"name": "milestones", "source": "milestones@inline", "path": "/p"}, "other@inline",
                                                                   {"source": "x@market"}]}
        self.assertEqual(hooks.plugin_entries([init]), [{"name": "milestones", "source": "milestones@inline", "path": "/p"},
                                                        {"name": "other", "source": "other@inline", "path": ""},
                                                        {"name": "x", "source": "x@market", "path": ""}])

    def test_no_init(self):
        self.assertEqual(hooks.plugin_entries([]), [])


class ToolResults(unittest.TestCase):
    def test_string_and_block_content_and_the_error_flag(self):
        events = [helpers.tool_result("a", "out"), helpers.tool_result("b", "boom", is_error=True),
                  {"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_result", "tool_use_id": "c",
                                                                                         "content": [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]}]}}]
        self.assertEqual(hooks.tool_results(events), {"a": ("out", False), "b": ("boom", True), "c": ("xy", False)})

    def test_a_subagent_result_and_other_user_events_are_ignored(self):
        sub = helpers.tool_result("s", "sub")
        sub["parent_tool_use_id"] = "toolu_parent"
        self.assertEqual(hooks.tool_results([sub, helpers.user_text("hi")]), {})


class HookResponses(unittest.TestCase):
    EVENTS = [{"type": "system", "subtype": "hook_response", "hook_event": "Stop"}, {"type": "system", "subtype": "hook_response", "hook_event": "PreToolUse"},
              {"type": "system", "subtype": "hook_response"}, {"type": "system", "subtype": "hook_started", "hook_event": "Stop"}, {"type": "assistant"}]

    def test_all_responses(self):
        self.assertEqual(len(hooks.hook_responses(self.EVENTS)), 3)

    def test_one_hook_event_compared_case_insensitively(self):
        self.assertEqual(hooks.hook_responses(self.EVENTS, "stop"), [self.EVENTS[0]])


class BashCommands(unittest.TestCase):
    def test_main_conversation_bash_calls_in_order(self):
        sub = helpers.call("Bash", command="rm -rf /")
        sub["parent_tool_use_id"] = "toolu_parent"
        events = [helpers.call("Bash", command="a"), helpers.call("Read", file_path="x"), sub, helpers.call("Bash", command="b")]
        self.assertEqual([c for _, c in hooks.bash_commands(events)], ["a", "b"])
        self.assertTrue(all(i for i, _ in hooks.bash_commands(events)))


class FedToModel(unittest.TestCase):
    def test_found_in_a_user_assistant_or_result_event(self):
        for event in (helpers.user_text(f"Stop hook feedback: {NEEDLE}"), {"type": "assistant", "message": {"content": [{"type": "text", "text": NEEDLE}]}},
                      helpers.result(f"I saw the {NEEDLE}")):
            self.assertTrue(hooks.fed_to_model([event], NEEDLE))

    def test_a_system_event_does_not_count(self):
        self.assertFalse(hooks.fed_to_model([{"type": "system", "subtype": "hook_response", "stderr": NEEDLE}], NEEDLE))

    def test_absent(self):
        self.assertFalse(hooks.fed_to_model([helpers.user_text("hello"), helpers.result("done")], NEEDLE))


class PermissionDenials(unittest.TestCase):
    def test_the_last_result_events_denials_and_system_events(self):
        denied = {"type": "system", "subtype": "permission_denied", "tool_name": "Bash"}
        events = [helpers.result("a", denials=[{"tool_name": "Edit"}]), denied, helpers.result("b", denials=[{"tool_name": "Bash"}])]
        self.assertEqual(hooks.permission_denials(events), [{"tool_name": "Bash"}, denied])

    def test_none(self):
        self.assertEqual(hooks.permission_denials([helpers.result("x")]), [])
        self.assertEqual(hooks.permission_denials([]), [])

    def test_a_result_without_the_field(self):
        self.assertEqual(hooks.permission_denials([{"type": "result", "subtype": "success"}]), [])


class Continuation(unittest.TestCase):
    def test_fed(self):
        self.assertEqual(hooks.continuation([helpers.user_text(NEEDLE)], NEEDLE), "fed")

    def test_a_stop_hook_event_with_exit_code_two_or_the_text(self):
        for response in ({"hook_event": "Stop", "exit_code": 2}, {"hook_event": "Stop", "stderr": f"x {NEEDLE}"}):
            events = [{"type": "system", "subtype": "hook_response", **response}]
            self.assertEqual(hooks.continuation(events, NEEDLE), "hook-event-only")

    def test_other_hooks_and_exit_codes_do_not_count(self):
        events = [{"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 0},
                  {"type": "system", "subtype": "hook_response", "hook_event": "PreToolUse", "exit_code": 2}]
        self.assertEqual(hooks.continuation(events, NEEDLE), "none")

    def test_none(self):
        self.assertEqual(hooks.continuation([helpers.init(), helpers.result("done")], NEEDLE), "none")


if __name__ == "__main__":
    unittest.main()
