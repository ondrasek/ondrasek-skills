"""The tool path (AskUserQuestion): offline checks on INVENTED traces. A headless `claude -p` run cannot call the tool (measured),
so these tests are the only place the tool checks run. The event shape is the assumed standard one, UNCONFIRMED against a real trace.

Layout: the branch selection, the good traces on both paths, the MUTATIONS registry (every new check has a failing run), variants."""
import io
import unittest

from helpers import COUNT_LINE, GOOD_ITEM, GOOD_TOOL_INPUT, PROMPTS, TRACES, case_for, make_events, make_tool_events, tool_case_for, tool_input, verdicts
import agent_trace as trace
import ask_tool
import checks
import run_evals

ITEM = "explicit-dollar"
SKIP = "followup-skip"
STOP = "followup-stop"

TOOL_IDS = {"tool-one-call-one-question", "tool-header-short", "tool-options-shape", "tool-recommended-first", "tool-skip-last",
            "tool-question-shape", "tool-no-extra-text"}
NEW_IDS = TOOL_IDS | {"no-tool-search"}
OUTCOME_ON_QUESTION = {"header-number", "header-total", "no-other-items", "no-preview"}
TEXT_STYLE = {"one-header", "sections", "options-max-3", "options-numbered", "recommended-marked", "one-question", "length-bound"}
COMMON_IDS = {"skill-loaded", "skill-trigger", "completed", "tool-detours", "turns-bound"}
# every id of the harness before the tool path existed (20) plus the text-path ids that item and skip rows ran
ORIGINAL_IDS = COMMON_IDS | OUTCOME_ON_QUESTION | TEXT_STYLE | {"wrapup-headings", "tangent-flag", "all-items-listed", "no-oaat-header"}
GOOD_QUESTION = GOOD_TOOL_INPUT["questions"][0]["question"]


def fallback_with_search(query="select:AskUserQuestion"):
    """The good text-template reply, preceded by a ToolSearch call for the tool (what the skill forbids)."""
    events = make_events(GOOD_ITEM)
    search = {"type": "assistant", "parent_tool_use_id": None,
              "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED_S", "name": "ToolSearch", "input": {"query": query, "max_results": 1}}]}}
    events.insert(-2, search)
    return checks.Case.build(case_for(ITEM, GOOD_ITEM).row, events)


def failed(case):
    return {cid for cid, (ok, _) in verdicts(case).items() if not ok}


# One failing run per new check. value: (case builder, ids that fail ALONGSIDE the check). Almost every one fails only its own check.
MUTATIONS = {
    "tool-one-call-one-question": (lambda: tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT)), set()),          # two calls: batched in time
    "tool-header-short": (lambda: tool_case_for(inputs=(tool_input(header="Database decision"),)), set()),             # 17 characters
    "tool-options-shape": (lambda: tool_case_for(inputs=(tool_input(multiSelect=True),)), set()),                       # a multi-select question
    "tool-recommended-first": (lambda: tool_case_for(inputs=(tool_input(options=[
        {"label": "Postgres", "description": "More setup."}, {"label": "SQLite", "description": "No server."},
        {"label": "Skip for now", "description": "Park this item and move on."}]),)), set()),                           # nothing marked
    "tool-skip-last": (lambda: tool_case_for(inputs=(tool_input(options=[
        {"label": "Postgres (Recommended)", "description": "More setup."}, {"label": "SQLite", "description": "No server."},
        {"label": "Later", "description": "Park this item and move on."}]),)), set()),                                  # the last label is not Skip for now
    "tool-question-shape": (lambda: tool_case_for(inputs=(tool_input(question=GOOD_QUESTION + " And when?"),)), set()),  # two question marks
    "tool-no-extra-text": (lambda: tool_case_for(text=GOOD_ITEM), set()),                                              # the whole text template next to the call
    "no-tool-search": (fallback_with_search, set()),                                                                   # fallback path: it searched, then used the text
}


class SelectionTests(unittest.TestCase):
    """Which checks apply: the tool path when the turn calls AskUserQuestion for an item, else exactly what ran before."""

    def test_a_turn_with_the_tool_call_is_graded_on_the_tool_path(self):
        case = tool_case_for()
        self.assertTrue(case.tool_path)
        ids = set(verdicts(case))
        self.assertEqual(ids, COMMON_IDS | {"no-tool-search"} | OUTCOME_ON_QUESTION | TOOL_IDS)
        self.assertFalse(ids & TEXT_STYLE, "the text-template style checks must not apply to an item in the tool input")

    def test_a_turn_without_the_tool_call_keeps_every_text_check(self):
        for row_id, expect in ((ITEM, "item"),):
            case = case_for(row_id, GOOD_ITEM)
            self.assertFalse(case.tool_path)
            self.assertEqual(set(verdicts(case)), COMMON_IDS | {"no-tool-search"} | OUTCOME_ON_QUESTION | TEXT_STYLE, expect)
            self.assertFalse(set(verdicts(case)) & TOOL_IDS)

    def test_a_bad_fallback_reply_still_fails_its_text_checks(self):
        case = case_for(ITEM, GOOD_ITEM.replace("**Why it matters:** The choice sets hosting cost.\n", ""))
        self.assertEqual(failed(case), {"sections"})

    def test_the_selection_follows_the_call_not_the_reply_text(self):
        both = tool_case_for(text=GOOD_ITEM)   # the call is there, so the text is graded as extra text and not as a template
        self.assertTrue(both.tool_path)
        self.assertEqual(failed(both), {"tool-no-extra-text"})

    def test_the_tool_checks_apply_to_item_and_skip_rows_only(self):
        for row_id in (STOP, "followup-tangent", "negative-list", "negative-single"):
            case = tool_case_for(row_id)
            self.assertFalse(case.tool_path, row_id)
            self.assertFalse(set(verdicts(case)) & TOOL_IDS, row_id)

    def test_only_the_last_turn_decides(self):
        # turn 1 used the tool, turn 2 (a skip row) answered in text: the tool path does not leak into turn 2
        events = make_tool_events() + make_events(GOOD_ITEM.replace("Item 1 of 5", "Item 2 of 5"), called=())
        case = checks.Case.build(case_for(SKIP, "x").row, events)
        self.assertFalse(case.tool_path)
        self.assertFalse(set(verdicts(case)) & TOOL_IDS)
        # and the other way round: turn 1 in text, turn 2 with the tool
        second = tool_input(question=GOOD_QUESTION.replace("Item 1 of 5", "Item 2 of 5"))
        events = make_events(GOOD_ITEM) + make_tool_events(inputs=(second,), text="", called=())
        case = checks.Case.build(case_for(SKIP, "x").row, events)
        self.assertTrue(case.tool_path)

    def test_completed_accepts_a_tool_turn_with_no_text_but_not_an_empty_text_turn(self):
        self.assertTrue(verdicts(tool_case_for(text=""))["completed"][0])
        self.assertFalse(verdicts(case_for(ITEM, ""))["completed"][0])


class GoodTracesTests(unittest.TestCase):
    def test_a_good_tool_path_trace_passes_every_applicable_check(self):
        results = checks.run_checks(tool_case_for())
        self.assertEqual([cid for cid, ok, _ in results if not ok], [])
        self.assertEqual(len(results), 17)

    def test_a_good_tool_path_trace_without_a_count_line_passes_too(self):
        self.assertEqual(failed(tool_case_for(text="")), set())

    def test_a_good_fallback_trace_passes_every_check_that_ran_before_and_the_new_process_check(self):
        results = checks.run_checks(case_for(ITEM, GOOD_ITEM))
        self.assertEqual({cid for cid, ok, _ in results if not ok}, set())
        self.assertEqual({cid for cid, _, _ in results}, ORIGINAL_IDS - {"wrapup-headings", "tangent-flag", "all-items-listed", "no-oaat-header"} | {"no-tool-search"})

    def test_one_to_three_choices_give_two_to_four_options(self):
        one = [{"label": "Postgres (Recommended)", "description": "Only choice."}, {"label": "Skip for now", "description": "Park this item and move on."}]
        three = [{"label": "A (Recommended)", "description": "a"}, {"label": "B", "description": "b"}, {"label": "C", "description": "c"},
                 {"label": "Skip for now", "description": "Park this item and move on."}]
        for options in (one, three):
            self.assertEqual(failed(tool_case_for(inputs=(tool_input(options=options),))), set(), len(options))

    def test_a_skip_row_on_the_tool_path_numbers_its_item_from_the_earlier_tool_question(self):
        first, second = GOOD_QUESTION, GOOD_QUESTION.replace("Item 1 of 5, decision: Pick the database", "Item 2 of 5, question: Confirm the launch date")
        events = make_tool_events() + make_tool_events(inputs=(tool_input(question=second, header="Question"),), text="", called=())
        row = case_for(SKIP, "x").row
        case = checks.Case.build(row, events)
        self.assertTrue(case.tool_path)
        self.assertIn("Item 1 of 5", case.history)
        self.assertEqual(failed(case), set())
        # the same turn quoting item 1 again fails header-number only (the harness reads the number from the tool question)
        again = checks.Case.build(row, make_tool_events() + make_tool_events(inputs=(tool_input(question=first),), text="", called=()))
        self.assertEqual(failed(again), {"header-number"})

    def test_the_committed_tool_fixtures_pass_every_check(self):
        for prompt_id in ("explicit-dollar", "followup-skip"):
            case = checks.Case.build(case_for(prompt_id, "x").row, trace.load(TRACES / "tool" / f"{prompt_id}.jsonl"))
            self.assertTrue(case.tool_path, prompt_id)
            self.assertEqual(failed(case), set(), prompt_id)

    def test_the_runner_grades_the_tool_fixtures_offline(self):
        stream = io.StringIO()
        code = run_evals.main(["--fixtures", str(TRACES / "tool"), "--prompts", str(PROMPTS), "--only", "explicit-dollar,followup-skip"], stream=stream)
        self.assertEqual(code, 0, stream.getvalue())
        self.assertIn("summary: prompts passed 2/2 (every trial)", stream.getvalue())

    def test_the_real_trace_and_the_old_fixtures_never_take_the_tool_path(self):
        for path in list(TRACES.glob("after/*.jsonl")) + list(TRACES.glob("before/*.jsonl")) + list(TRACES.glob("real/*.jsonl")):
            events = trace.load(path)
            self.assertEqual(ask_tool.ask_calls(events), [], path.name)
            self.assertEqual(ask_tool.searched_for_tool(events), [], path.name)


class MutationTests(unittest.TestCase):
    def test_every_new_check_has_a_failing_run(self):
        self.assertEqual(set(MUTATIONS), NEW_IDS)
        # a check added to GROUPS without a failing run fails here: only the ids that existed before may lack a mutation
        self.assertEqual(set(checks.GROUPS) - ORIGINAL_IDS, NEW_IDS)

    def test_every_new_check_is_wired_to_a_path_and_a_group(self):
        wired = {cid for cid, _, _ in checks.run_checks(tool_case_for())} | {cid for cid, _, _ in checks.run_checks(case_for(ITEM, GOOD_ITEM))}
        self.assertTrue(NEW_IDS <= wired)
        self.assertEqual(len(checks.TOOL_CHECKS), len(TOOL_IDS))
        for cid in NEW_IDS:
            self.assertIn(checks.GROUPS[cid], ("outcome", "process", "style", "efficiency"))
        self.assertEqual({checks.GROUPS[c] for c in NEW_IDS}, {"process", "style"})

    def test_each_mutation_fails_its_check_and_only_the_listed_ones(self):
        for cid, (build, also) in MUTATIONS.items():
            with self.subTest(check=cid):
                results = verdicts(build())
                self.assertIn(cid, results)
                bad = {k for k, (ok, _) in results.items() if not ok}
                self.assertEqual(bad, {cid} | also, {k: n for k, (ok, n) in results.items() if not ok})

    def test_a_failure_says_what_was_measured(self):
        for cid, (build, _) in MUTATIONS.items():
            self.assertRegex(verdicts(build())[cid][1], r"\d|None|'|empty|missing", cid)


class VariantTests(unittest.TestCase):
    """More failing runs per check than the registry holds. A variant may fail neighbours: they are named."""

    def only(self, case, *ids):
        self.assertEqual(failed(case), set(ids), {k: n for k, (ok, n) in verdicts(case).items() if not ok})

    def test_one_call_with_two_questions(self):
        two = {"questions": GOOD_TOOL_INPUT["questions"] * 2}
        self.only(tool_case_for(inputs=(two,)), "tool-one-call-one-question")

    def test_a_header_that_is_empty_or_missing(self):
        self.only(tool_case_for(inputs=(tool_input(header=" "),)), "tool-header-short")
        data = tool_input()
        del data["questions"][0]["header"]
        self.only(tool_case_for(inputs=(data,)), "tool-header-short")

    def test_a_header_of_exactly_twelve_characters_passes(self):
        self.only(tool_case_for(inputs=(tool_input(header="Architecture"),)))
        self.assertEqual(len("Architecture"), 12)

    def test_options_shape_variants(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        for name, options in (("one option", good[-1:]),
                              ("five options", good[:2] * 2 + good[-1:]),
                              ("empty description", [dict(good[0], description=""), good[1], good[2]]),
                              ("no label", [{"description": "x"}, good[1], good[2]])):
            with self.subTest(name):
                bad = failed(tool_case_for(inputs=(tool_input(options=options),)))
                self.assertIn("tool-options-shape", bad)
        data = tool_input()
        del data["questions"][0]["options"]
        self.assertIn("tool-options-shape", failed(tool_case_for(inputs=(data,))))

    def test_multi_select_false_or_absent_is_fine(self):
        data = tool_input()
        del data["questions"][0]["multiSelect"]
        self.only(tool_case_for(inputs=(data,)))
        self.only(tool_case_for(inputs=(tool_input(multiSelect=False),)))

    def test_recommended_not_first_or_marked_twice(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        later = [good[1], dict(good[0]), good[2]]
        twice = [good[0], dict(good[1], label="SQLite (Recommended)"), good[2]]
        for name, options in (("recommended second", later), ("two recommended", twice)):
            with self.subTest(name):
                self.only(tool_case_for(inputs=(tool_input(options=options),)), "tool-recommended-first")

    def test_skip_option_not_last_or_misspelled(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        for name, options in (("skip first", [good[2], good[0], good[1]]), ("lower case", [good[0], good[1], dict(good[2], label="skip for now")]),
                              ("suffix", [good[0], good[1], dict(good[2], label="Skip for now.")])):
            with self.subTest(name):
                bad = failed(tool_case_for(inputs=(tool_input(options=options),)))
                self.assertIn("tool-skip-last", bad)

    def test_question_shape_variants(self):
        no_prefix = GOOD_QUESTION.replace("Item 1 of 5, decision: Pick the database. ", "")
        no_mark = GOOD_QUESTION.replace("use?", "use.")
        no_type = GOOD_QUESTION.replace("decision: ", "")
        self.assertIn("tool-question-shape", failed(tool_case_for(inputs=(tool_input(question=no_prefix),))))
        self.only(tool_case_for(inputs=(tool_input(question=no_mark),)), "tool-question-shape")
        self.assertIn("tool-question-shape", failed(tool_case_for(inputs=(tool_input(question=no_type),))))
        self.assertIn("tool-question-shape", failed(tool_case_for(inputs=({"questions": [{"options": []}]},))))

    def test_a_question_without_the_header_also_fails_the_outcome_checks_that_read_it(self):
        bad = failed(tool_case_for(inputs=(tool_input(question="Which database should the first release use?"),)))
        self.assertEqual(bad, {"tool-question-shape", "header-number", "header-total"})

    def test_extra_text_variants(self):
        for name, text in (("two lines", COUNT_LINE + "\nNext, think about it."), ("a numbered option", "1. Postgres"),
                           ("a second header", "Item 2 of 5, risk: Something."), ("a label", "**Options:** none"),
                           ("a long paragraph", "word " * 40)):
            with self.subTest(name):
                self.only(tool_case_for(text=text), "tool-no-extra-text")

    def test_the_count_line_alone_and_no_text_are_both_allowed(self):
        self.only(tool_case_for(text=COUNT_LINE))
        self.only(tool_case_for(text=""))

    def test_text_in_several_blocks_is_counted_together(self):
        events = make_tool_events()
        events[-2]["message"]["content"].insert(0, {"type": "text", "text": "Let me think."})
        self.only(checks.Case.build(case_for(ITEM, "x").row, events), "tool-no-extra-text")

    def test_no_tool_search_on_the_tool_path_and_on_the_fallback(self):
        self.only(tool_case_for(searches=("select:AskUserQuestion",)), "no-tool-search")
        self.only(fallback_with_search("ask user question"), "no-tool-search")
        self.only(fallback_with_search("ask_user_question tool"), "no-tool-search")

    def test_a_search_for_another_tool_is_allowed(self):
        self.only(tool_case_for(searches=("select:NotebookEdit",)))

    def test_no_tool_search_applies_to_every_expect(self):
        for row_id in (ITEM, SKIP, STOP, "followup-tangent", "negative-list", "negative-single"):
            self.assertIn("no-tool-search", verdicts(case_for(row_id, GOOD_ITEM)), row_id)

    def test_a_call_in_a_subagent_does_not_count(self):
        events = make_tool_events()
        for event in events:
            if event.get("type") == "assistant":
                event["parent_tool_use_id"] = "toolu_PARENT"
        self.assertFalse(checks.Case.build(case_for(ITEM, "x").row, events).tool_path)


class AskToolHelperTests(unittest.TestCase):
    def test_split_returns_the_events_after_the_previous_result(self):
        events = make_events("one") + make_events("two", called=())
        earlier, turn = ask_tool.split(events)
        self.assertEqual(trace.final_reply(turn), "two")
        self.assertEqual(trace.final_reply(earlier), "one")
        self.assertEqual(ask_tool.split(make_events("one"))[0], [])

    def test_a_cut_off_second_turn_is_the_events_after_the_last_result(self):
        events = make_events("one") + make_events("two", called=())[:-1]
        self.assertEqual(len(ask_tool.last_turn(events)), 2)

    def test_malformed_input_is_read_as_empty(self):
        self.assertEqual(ask_tool.questions({"questions": "x"}), [])
        self.assertEqual(ask_tool.questions({"questions": [1, {"question": "q"}]}), [{"question": "q"}])
        self.assertEqual(ask_tool.question_text({"question": 5}), "")
        self.assertEqual(ask_tool.first_question([]), {})


if __name__ == "__main__":
    unittest.main()
