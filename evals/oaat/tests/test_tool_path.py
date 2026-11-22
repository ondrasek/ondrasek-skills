"""The tool path (AskUserQuestion): offline checks on INVENTED traces. A headless `claude -p` run cannot call the tool (measured),
so these tests are the only place the tool checks run. The INPUT of the tool is documented (code.claude.com/docs/en/agent-sdk/user-input:
`questions[]`, `header` max 12, `options` 2 to 4 with `label` and `description`, `multiSelect`). The text of a tool_result follows the
envelope OBSERVED in one interactive session (2026-11-22: `Your questions have been answered: "<question>"="<answer>", ... You can now
continue with these answers in mind.`), one sample on one version, so UNCONFIRMED across versions; a bare label and a JSON mapping are
accepted as well (EnvelopeTests).

The model: an interactive walk-through is ONE turn with several sequential calls (call, tool_result with the chosen label, one plain
line, the next call, ...); a skip is the tool_result `Skip for now`. The checks grade PER CALL.

Layout: the branch selection, the good traces, the MUTATIONS, CHANGED and REFINED registries (every new or changed check has a failing run
and, for REFINED, a passing run), variants."""
import io
import json
import unittest

from helpers import (CLOSING_LINE, COUNT_LINE, GOOD_ITEM, GOOD_TOOL_INPUT, PROMPTS, TRACES, Ask, Use, case_for, item_input, make_events,
                     make_tool_events, make_walk_events, tool_case_for, tool_input, verdicts, wrap_answer)
import agent_trace as trace
import ask_tool
import checks
import run_evals

ITEM = "explicit-dollar"
SKIP = "followup-skip"
STOP = "followup-stop"
TANGENT = "followup-tangent"

TOOL_IDS = {"tool-one-call-one-question", "tool-header-short", "tool-options-shape", "tool-recommended-first", "tool-skip-last",
            "tool-question-shape", "tool-no-extra-text", "tool-early-end"}
NEW_IDS = TOOL_IDS | {"no-tool-search", "tool-denied-fallback", "tool-not-allowed", "tool-template-ends-turn"}
OUTCOME_ON_QUESTION = {"header-number", "header-total", "no-other-items", "no-preview"}
TEXT_STYLE = {"one-header", "sections", "options-max-3", "options-numbered", "recommended-marked", "one-question", "length-bound"}
COMMON_IDS = {"skill-loaded", "skill-trigger", "completed", "tool-detours", "turns-bound"}
COMMON_ALL = COMMON_IDS | {"no-tool-search", "tool-denied-fallback", "tool-template-ends-turn"}
# every id of the harness before the tool path existed (20) plus the text-path ids that item and skip rows ran
ORIGINAL_IDS = COMMON_IDS | OUTCOME_ON_QUESTION | TEXT_STYLE | {"wrapup-headings", "tangent-flag", "all-items-listed", "no-oaat-header"}
# existing ids whose behaviour on the tool path or on a tool turn changed with the per-call model: each keeps a failing run below
CHANGED_IDS = OUTCOME_ON_QUESTION | {"no-oaat-header", "tool-detours", "turns-bound", "completed"}
GOOD_QUESTION = GOOD_TOOL_INPUT["questions"][0]["question"]
TMPL1 = GOOD_ITEM.replace("5 items need you. First one:\n\n", "")
FLAG = "This may be a tangent from Pick the database. Continue or refocus?"
ITEM2_TEXT = GOOD_ITEM.replace("5 items need you. First one:\n\n", "").replace("Item 1 of 5, decision: Pick the database", "Item 2 of 5, question: Confirm the launch date")
WRAPUP = "**Decided:** Postgres for the first release.\n**Parked:** none.\n**Next action:** Confirm the launch date."
SECOND = item_input(2, "Confirm the launch date", options=[
    {"label": "Friday the 14th (Recommended)", "description": "Matches the audit plan; leaves a week of slack."},
    {"label": "The following Monday", "description": "More slack, later announcement."},
    {"label": "Skip for now", "description": "Park this item and move on."}])
THIRD = item_input(3, "Assign the security review", kind="risk")


def read_event():
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED_R", "name": "Read", "input": {"file_path": ".cache/milestones.json"}}]}}


def fallback_with_search(query="select:AskUserQuestion"):
    """The good text-template reply, preceded by a ToolSearch call for the tool (what the skill forbids)."""
    events = make_events(GOOD_ITEM)
    search = {"type": "assistant", "parent_tool_use_id": None,
              "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED_S", "name": "ToolSearch", "input": {"query": query, "max_results": 1}}]}}
    events.insert(-2, search)
    return checks.Case.build(case_for(ITEM, GOOD_ITEM).row, events)


def walk_case(row_id, messages, **kwargs):
    return checks.Case.build(case_for(row_id, "x").row, make_walk_events(messages, **kwargs))


def two_turn_case(row_id, first_messages, second_messages):
    """A follow-up row with a tool walk in turn 1 and another in turn 2 (the user typed a new message in between)."""
    return checks.Case.build(case_for(row_id, "x").row, make_walk_events(first_messages) + make_walk_events(second_messages, called=()))


def late_row(row_id, reply_messages):
    """A follow-up row: turn 1 is the good text item, the graded turn 2 is a walk."""
    return checks.Case.build(case_for(row_id, "x").row, make_events(GOOD_ITEM) + make_walk_events(reply_messages, called=()))


def failed(case):
    return {cid for cid, (ok, _) in verdicts(case).items() if not ok}


def notes(case):
    return {k: n for k, (ok, n) in verdicts(case).items() if not ok}


def denied_after_answered():
    """Call 1 answered, call 2 denied (is_error) and nothing printed after it: the user saw no item 2."""
    return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "denied", error=True)]])


def failed_result(case):
    events = [dict(e, is_error=True, subtype="error_during_execution") if e.get("type") == "result" else e for e in case.events]
    return checks.Case.build(case.row, events)


def stop_row_with_call():
    return late_row(STOP, [[Ask(tool_input(question="Which database should the first release use?"), "stop")], [WRAPUP]])


# One failing run per new check. value: (case builder, ids that fail ALONGSIDE the check). Almost every one fails only its own check.
MUTATIONS = {
    # two calls in ONE assistant message: batched
    "tool-one-call-one-question": (lambda: tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), batched=True), set()),
    "tool-header-short": (lambda: tool_case_for(inputs=(tool_input(header="Database decision"),)), set()),             # 17 characters
    "tool-options-shape": (lambda: tool_case_for(inputs=(tool_input(multiSelect=True),)), set()),                       # a multi-select question
    "tool-recommended-first": (lambda: tool_case_for(inputs=(tool_input(options=[
        {"label": "Postgres", "description": "More setup."}, {"label": "SQLite", "description": "No server."},
        {"label": "Skip for now", "description": "Park this item and move on."}]),)), set()),                           # nothing marked
    "tool-skip-last": (lambda: tool_case_for(inputs=(tool_input(options=[
        {"label": "Postgres (Recommended)", "description": "More setup."}, {"label": "SQLite", "description": "No server."},
        {"label": "Later", "description": "Park this item and move on."}]),)), set()),                                  # the last label is not Skip for now
    "tool-question-shape": (lambda: tool_case_for(inputs=(tool_input(question=GOOD_QUESTION + " And when?"),)), set()),  # two question marks
    "tool-no-extra-text": (lambda: tool_case_for(text=GOOD_ITEM), {"tool-template-ends-turn"}),                         # the whole text template next to the call
    "no-tool-search": (fallback_with_search, set()),                                                                   # fallback path: it searched, then used the text
    "tool-denied-fallback": (denied_after_answered, set()),                                                            # a denied call and nothing printed after it
    "tool-not-allowed": (stop_row_with_call, set()),                                                                   # a stop row that presents an item with the tool
    "tool-early-end": (lambda: tool_case_for(answers=("Postgres (Recommended)",)), set()),                              # a label, 4 items left, a closing line
    "tool-template-ends-turn": (lambda: walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "denied", error=True)],     # a template, then a call
                                                         [TMPL1, Ask(SECOND, "stop")], [CLOSING_LINE]]), set()),
}
# One failing run per existing check that now reads the tool path (per call) or the question texts of a turn.
CHANGED = {
    "header-number": (lambda: tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT)), set()),                          # item 1 twice after a chosen label
    "header-total": (lambda: tool_case_for(inputs=(tool_input(question=GOOD_QUESTION.replace("of 5", "of 9")),)), set()),
    "no-other-items": (lambda: tool_case_for(inputs=(tool_input(question=GOOD_QUESTION.replace(
        "The first release needs one database.", "The first release needs one database, and Confirm the launch date waits for it.")),)), set()),
    "no-preview": (lambda: tool_case_for(inputs=(tool_input(question=GOOD_QUESTION.replace("The choice", "Next up is item 3. The choice")),)), set()),
    "no-oaat-header": (lambda: late_row(STOP, [[Ask(GOOD_TOOL_INPUT, "stop")], [WRAPUP]]), {"tool-not-allowed"}),        # a header in a question on a stop row
    # AskUserQuestion calls and the answered call are no longer detours, turns or an empty reply; everything else still counts
    "tool-detours": (lambda: tool_case_for(others=("Read", "Read", "Read"), turns=3), set()),                               # other tools still count
    "turns-bound": (lambda: tool_case_for(turns=9), set()),                                                                # turns beyond the calls still count
    "completed": (lambda: failed_result(tool_case_for()), set()),                                                          # an error result is still not completed
}


# ---- round 2 (the observed envelope, early end, template ends the turn, retry after an input error, flag ends the turn, milestones) ----
MS_SKILL = Use("Skill", {"skill": "milestones:milestones"})
MS_BASH = Use("Bash", {"command": "python3 /invented/plugins/milestones/milestones.py set 1 --needs none"})
MS_READ = ("Read", {"file_path": "/invented/project/.cache/milestones.json"})
FIVE = (GOOD_TOOL_INPUT, SECOND, THIRD, item_input(4, "Rename the staging branch"), item_input(5, "Approve the logo change", kind="decision"))
FAIL_TEXT = "InputValidationError: header is longer than 12 characters"


def milestone_walk(bookkeeping=(MS_SKILL, MS_BASH), others=(MS_READ,), **kwargs):
    """The M1 shape: three items that come from milestones, each answer recorded through the milestones skill in the same turn
    (a Skill call and a Bash call running milestones.py), after a mandated Read of .cache/milestones.json. Invented text."""
    messages = [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")]]
    for item, answer in ((SECOND, "Friday the 14th (Recommended)"), (THIRD, "Dana will own it")):
        messages += [[use] for use in bookkeeping] + [["Noted.", Ask(item, answer)]]
    messages += [[use] for use in bookkeeping] + [[WRAPUP]]
    return walk_case(ITEM, messages, others=others, **kwargs)


def retry_once():
    """Call 1 fails on its input, the same item is called again with the input fixed and answered, then the walk goes on."""
    return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")],
                            ["Noted.", Ask(SECOND, "stop")], [WRAPUP]])


def retry_twice():
    """The same item fails three times in a row, so the second failure is a retry that is retried again, with no text after it."""
    return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)],
                            [Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [TMPL1]])


def flag_before_a_call():
    return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "By the way, which container runtime?")],
                            [FLAG, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "stop")], [WRAPUP]])


def flag_as_closing():
    return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "By the way, which container runtime?")], [FLAG]])


def count_line_of(words):
    return tool_case_for(text=" ".join(["word"] * (words - 1)) + " first:")


# One more failing run AND one passing run for each behaviour that this round changed in a check that already had a failing run above.
# value: (check id, builder of the failing run, ids that fail alongside it, builder of a run that must pass every check)
REFINED = {
    "retry-only-once": ("tool-denied-fallback", retry_twice, set(), retry_once),
    "flag-before-a-call": ("tool-no-extra-text", flag_before_a_call, set(), flag_as_closing),
    "count-line-of-31-words": ("tool-no-extra-text", lambda: count_line_of(31), set(), lambda: count_line_of(30)),
    "milestone-calls-not-detours": ("tool-detours", lambda: milestone_walk(bookkeeping=(MS_SKILL, Use("Bash", {"command": "ls -la"}))),
                                    {"turns-bound"}, milestone_walk),
    "milestone-calls-not-turns": ("turns-bound", lambda: milestone_walk(turns=20), set(), milestone_walk),
    "label-matched-ignoring-case": ("header-number", lambda: tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.",
                                                                           answers=("postgres (recommended)", "stop")), set(),
                                    lambda: tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.", answers=("Which one is cheaper?", "stop"))),
}
REFINED_IDS = {"tool-denied-fallback", "tool-no-extra-text", "tool-detours", "turns-bound", "header-number"}


class SelectionTests(unittest.TestCase):
    """Which checks apply: the tool path when the turn has an ANSWERED call for an item, else exactly what ran before."""

    def test_a_turn_with_an_answered_call_is_graded_on_the_tool_path(self):
        case = tool_case_for()
        self.assertTrue(case.tool_path)
        ids = set(verdicts(case))
        self.assertEqual(ids, COMMON_ALL | OUTCOME_ON_QUESTION | TOOL_IDS)
        self.assertFalse(ids & TEXT_STYLE, "the text-template style checks must not apply to an item in the tool input")

    def test_a_turn_without_the_tool_call_keeps_every_text_check(self):
        case = case_for(ITEM, GOOD_ITEM)
        self.assertFalse(case.tool_path)
        self.assertEqual(set(verdicts(case)), COMMON_ALL | OUTCOME_ON_QUESTION | TEXT_STYLE)
        self.assertFalse(set(verdicts(case)) & TOOL_IDS)

    def test_a_bad_fallback_reply_still_fails_its_text_checks(self):
        case = case_for(ITEM, GOOD_ITEM.replace("**Why it matters:** The choice sets hosting cost.\n", ""))
        self.assertEqual(failed(case), {"sections"})

    def test_the_selection_follows_the_call_not_the_reply_text(self):
        both = tool_case_for(text=GOOD_ITEM)   # the call is there, so the text is graded as extra text and not as a template
        self.assertTrue(both.tool_path)
        self.assertEqual(failed(both), {"tool-no-extra-text", "tool-template-ends-turn"})   # the template is also followed by a call

    def test_the_tool_checks_apply_to_item_and_skip_rows_only(self):
        for row_id in (STOP, TANGENT, "negative-list", "negative-single"):
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
        events = make_events(GOOD_ITEM) + make_tool_events(inputs=(item_input(2, "Confirm the launch date"),), text="", called=())
        self.assertTrue(checks.Case.build(case_for(SKIP, "x").row, events).tool_path)

    def test_completed_accepts_a_tool_turn_with_no_text_but_not_an_empty_text_turn(self):
        self.assertTrue(verdicts(tool_case_for(text="", closing=""))["completed"][0])
        self.assertFalse(verdicts(case_for(ITEM, ""))["completed"][0])


class DeniedCallTests(unittest.TestCase):
    """A denied or failed call (is_error true, or no tool_result) is graded on the TEXT path: the skill sanctions that fallback."""

    def fallback(self, **ask):
        return walk_case(ITEM, [[Ask(**ask)], [GOOD_ITEM]])

    def test_a_denied_call_followed_by_the_template_is_graded_on_the_text_path(self):
        for name, ask in (("is_error result", dict(answer="denied", error=True)), ("no result at all", dict(answer=None))):
            with self.subTest(name):
                case = self.fallback(**ask)
                self.assertFalse(case.tool_path)
                self.assertEqual(set(verdicts(case)), COMMON_ALL | OUTCOME_ON_QUESTION | TEXT_STYLE)
                self.assertEqual(failed(case), set())
                self.assertEqual(len(case.asks), 1)
                self.assertFalse(case.asks[0]["answered"])

    def test_the_recorded_denied_call_is_named_in_the_note(self):
        self.assertIn("denied or failed: 1", verdicts(self.fallback(answer="denied", error=True))["tool-denied-fallback"][1])

    def test_a_denied_call_with_a_bad_template_still_fails_its_text_checks(self):
        case = walk_case(ITEM, [[Ask(answer="denied", error=True)], [GOOD_ITEM.replace("**Why it matters:** The choice sets hosting cost.\n", "")]])
        self.assertEqual(failed(case), {"sections"})

    def test_a_denied_call_with_no_text_afterwards_fails(self):
        for name, ask in (("is_error", dict(answer="denied", error=True)), ("no result", dict(answer=None))):
            with self.subTest(name):
                case = walk_case(ITEM, [[Ask(**ask)]])   # the agent printed nothing at all: the user saw nothing
                self.assertIn("completed", failed(case))
                self.assertIn("tool-denied-fallback", failed(case))

    def test_a_denied_call_after_a_count_line_and_nothing_else_fails_the_fallback_check(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(answer="denied", error=True)]])   # the count line is text, but nothing follows the call
        self.assertTrue(verdicts(case)["completed"][0])
        self.assertFalse(verdicts(case)["tool-denied-fallback"][0])

    def test_one_answered_call_keeps_the_turn_on_the_tool_path(self):
        case = denied_after_answered()
        self.assertTrue(case.tool_path)
        self.assertEqual(failed(case), {"tool-denied-fallback"})

    def test_the_text_after_a_denied_call_in_a_tool_turn_is_the_fallback_and_not_extra_text(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "denied", error=True)], [ITEM2_TEXT]])
        self.assertTrue(case.tool_path)
        self.assertEqual(failed(case), set(), notes(case))

    def test_a_bad_fallback_after_a_denied_call_in_a_tool_turn_fails(self):
        # the text after a denied call is graded with the text checks of the item rows: a dump of the list, a wrong number, a missing section
        titles = "\n".join(f"- {t}" for t in ("Pick the database", "Confirm the launch date", "Assign the security review"))
        dump = titles + "\nItem 2 of 5, question: Confirm the launch date. Which date?"
        for name, text in (("a bullet list of titles", dump), ("the wrong item number", ITEM2_TEXT.replace("Item 2 of 5", "Item 4 of 5")),
                           ("a missing section", ITEM2_TEXT.replace("**Why it matters:** The choice sets hosting cost.\n", ""))):
            with self.subTest(name):
                case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "denied", error=True)], [text]])
                self.assertTrue(case.tool_path)
                self.assertEqual(failed(case), {"tool-denied-fallback"}, notes(case))

    def test_a_call_with_no_result_followed_by_another_call_is_batching(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, None)], ["Next.", Ask(SECOND, "The following Monday")], [CLOSING_LINE]])
        self.assertIn("tool-one-call-one-question", failed(case))



class GoodTracesTests(unittest.TestCase):
    def test_a_good_tool_path_trace_passes_every_applicable_check(self):
        results = checks.run_checks(tool_case_for())
        self.assertEqual([cid for cid, ok, _ in results if not ok], [])
        self.assertEqual(len(results), 20)   # 8 common + 4 outcome per call + 8 tool checks

    def test_a_good_tool_path_trace_without_a_count_line_passes_too(self):
        self.assertEqual(failed(tool_case_for(text="")), set())

    def test_a_good_walk_of_three_items_passes_every_check(self):
        case = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND, THIRD), between="Noted, Postgres.", answers=("Postgres (Recommended)", "Skip for now", "Dana will own it"))
        self.assertEqual(failed(case), set(), notes(case))
        self.assertEqual(len(case.asks), 3)

    def test_the_walk_may_end_with_a_wrap_up_after_free_text_stop(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "Skip for now")],
                                ["Parked.", Ask(THIRD, "stop")], [WRAPUP]], called=("oaat:oaat",))
        self.assertEqual(failed(case), set(), notes(case))

    def test_a_good_fallback_trace_passes_every_check_that_ran_before_and_the_new_process_checks(self):
        results = checks.run_checks(case_for(ITEM, GOOD_ITEM))
        self.assertEqual({cid for cid, ok, _ in results if not ok}, set())
        self.assertEqual({cid for cid, _, _ in results}, ORIGINAL_IDS - {"wrapup-headings", "tangent-flag", "all-items-listed", "no-oaat-header"}
                         | {"no-tool-search", "tool-denied-fallback", "tool-template-ends-turn"})

    def test_one_to_three_choices_give_two_to_four_options(self):
        one = [{"label": "Postgres (Recommended)", "description": "Only choice."}, {"label": "Skip for now", "description": "Park this item and move on."}]
        three = [{"label": "A (Recommended)", "description": "a"}, {"label": "B", "description": "b"}, {"label": "C", "description": "c"},
                 {"label": "Skip for now", "description": "Park this item and move on."}]
        for options in (one, three):
            self.assertEqual(failed(tool_case_for(inputs=(tool_input(options=options),))), set(), len(options))

    def test_a_skip_row_numbers_its_calls_from_the_calls_before_it_in_the_same_turn(self):
        case = tool_case_for(SKIP, inputs=(GOOD_TOOL_INPUT, SECOND), answers=("Skip for now", "stop"), between="Parked for later.")
        self.assertEqual(failed(case), set(), notes(case))

    def test_a_skip_row_with_a_second_turn_numbers_its_item_from_the_earlier_turns_question(self):
        row = case_for(SKIP, "x").row
        events = make_tool_events() + make_tool_events(inputs=(SECOND,), text="", called=())
        case = checks.Case.build(row, events)
        self.assertTrue(case.tool_path)
        self.assertIn("Item 1 of 5", case.history)
        self.assertEqual(failed(case), set(), notes(case))
        # the same turn quoting item 1 again fails header-number only
        again = checks.Case.build(row, make_tool_events() + make_tool_events(inputs=(GOOD_TOOL_INPUT,), text="", called=()))
        self.assertEqual(failed(again), {"header-number"})

    def test_the_committed_tool_fixtures_pass_every_check(self):
        for prompt_id in ("explicit-dollar", "followup-skip"):
            case = checks.Case.build(case_for(prompt_id, "x").row, trace.load(TRACES / "tool" / f"{prompt_id}.jsonl"))
            self.assertTrue(case.tool_path, prompt_id)
            self.assertEqual(failed(case), set(), (prompt_id, notes(case)))
            self.assertGreaterEqual(len(case.asks), 2, prompt_id)   # a same-turn walk, not one call per turn

    def test_the_committed_tool_fixtures_are_realistic_every_call_has_its_result_before_the_next(self):
        for prompt_id in ("explicit-dollar", "followup-skip"):
            events = trace.load(TRACES / "tool" / f"{prompt_id}.jsonl")
            self.assertEqual(sum(1 for e in events if e.get("type") == "result"), 1, prompt_id)   # one turn
            self.assertEqual(ask_tool.unanswered_overlaps(events), [], prompt_id)
            self.assertTrue(all(r["answered"] for r in ask_tool.ask_records(events)), prompt_id)
        skip = ask_tool.ask_records(trace.load(TRACES / "tool" / "followup-skip.jsonl"))
        self.assertEqual(skip[0]["label"], "Skip for now")
        # every tool_result of the committed traces is the OBSERVED wrapper envelope (README, "The tool path"), whose answer is read out of it
        for prompt_id in ("explicit-dollar", "followup-skip"):
            for record in ask_tool.ask_records(trace.load(TRACES / "tool" / f"{prompt_id}.jsonl")):
                self.assertEqual(record["envelope"], "wrapper", prompt_id)
                self.assertTrue(record["result"]["content"].startswith("Your questions have been answered: "), prompt_id)
                self.assertTrue(record["result"]["content"].endswith(" You can now continue with these answers in mind."), prompt_id)

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

    def test_every_changed_check_has_a_failing_run(self):
        self.assertEqual(set(CHANGED), CHANGED_IDS)
        self.assertTrue(CHANGED_IDS <= ORIGINAL_IDS)

    def test_every_new_check_is_wired_to_a_path_and_a_group(self):
        cases = [tool_case_for(), case_for(ITEM, GOOD_ITEM), case_for(STOP, WRAPUP)]
        wired = {cid for case in cases for cid, _, _ in checks.run_checks(case)}
        self.assertTrue(NEW_IDS <= wired, NEW_IDS - wired)
        self.assertEqual(len(checks.TOOL_CHECKS), len(TOOL_IDS))
        for cid in NEW_IDS:
            self.assertIn(checks.GROUPS[cid], ("outcome", "process", "style", "efficiency"))
        self.assertEqual({checks.GROUPS[c] for c in NEW_IDS}, {"process", "style", "outcome"})
        self.assertEqual((checks.GROUPS["tool-denied-fallback"], checks.GROUPS["tool-not-allowed"]), ("process", "outcome"))

    def test_each_mutation_fails_its_check_and_only_the_listed_ones(self):
        for registry in (MUTATIONS, CHANGED):
            for cid, (build, also) in registry.items():
                with self.subTest(check=cid):
                    results = verdicts(build())
                    self.assertIn(cid, results)
                    bad = {k for k, (ok, _) in results.items() if not ok}
                    self.assertEqual(bad, {cid} | also, notes(build()))

    def test_a_failure_says_what_was_measured(self):
        for registry in (MUTATIONS, CHANGED):
            for cid, (build, _) in registry.items():
                self.assertRegex(verdicts(build())[cid][1], r"\d|None|'|empty|missing", cid)
        for name, (cid, build, _, _) in REFINED.items():
            self.assertRegex(verdicts(build())[cid][1], r"\d|None|'|empty|missing", name)

    def test_every_refined_behaviour_has_a_failing_and_a_passing_run(self):
        self.assertEqual({cid for cid, _, _, _ in REFINED.values()}, REFINED_IDS)
        self.assertTrue(REFINED_IDS <= (NEW_IDS | CHANGED_IDS))   # only ids that already have a failing run above are refined here
        for name, (cid, build_bad, also, build_good) in REFINED.items():
            with self.subTest(name):
                bad = build_bad()
                self.assertEqual(failed(bad), {cid} | also, notes(bad))
                good = build_good()
                self.assertEqual(failed(good), set(), notes(good))
                self.assertIn(cid, verdicts(good))

    def test_a_good_run_of_every_changed_and_new_check_passes(self):
        good = verdicts(tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Noted."))
        stop = verdicts(case_for(STOP, WRAPUP))
        for cid in (TOOL_IDS | OUTCOME_ON_QUESTION | COMMON_IDS | {"tool-denied-fallback", "no-tool-search"}):
            self.assertTrue(good[cid][0], (cid, good[cid]))
        for cid in ("tool-not-allowed", "no-oaat-header"):
            self.assertTrue(stop[cid][0], cid)


class PerCallTests(unittest.TestCase):
    """Every call of a turn is graded on its own: a defect in the SECOND call fails the check and the note names the call."""

    def only(self, case, *ids):
        self.assertEqual(failed(case), set(ids), notes(case))

    def second_bad(self, **changes):
        bad = item_input(2, "Confirm the launch date", **changes)
        return tool_case_for(inputs=(GOOD_TOOL_INPUT, bad), between="Noted.")

    def test_a_defect_in_the_second_call_is_found_by_every_shape_check(self):
        good_options = GOOD_TOOL_INPUT["questions"][0]["options"]
        cases = {
            "tool-header-short": dict(header="Database decision"),
            "tool-options-shape": dict(multiSelect=True),
            "tool-recommended-first": dict(options=[good_options[1], good_options[0], good_options[2]]),
            "tool-skip-last": dict(options=[good_options[0], good_options[2], good_options[1]]),
        }
        for cid, changes in cases.items():
            with self.subTest(cid):
                case = self.second_bad(**changes)
                self.only(case, cid)
                self.assertIn("call 2", verdicts(case)[cid][1])
                self.assertNotIn("->", verdicts(case)[cid][1].split("call 2")[0])   # call 1 is fine
        no_mark = item_input(2, "Confirm the launch date", question="Confirm the launch date and nothing else?")
        self.assertIn("tool-question-shape", failed(tool_case_for(inputs=(GOOD_TOOL_INPUT, no_mark), between="Noted.")))

    def test_two_questions_in_one_call(self):
        two = {"questions": SECOND["questions"] * 2}
        self.assertEqual(failed(tool_case_for(inputs=(GOOD_TOOL_INPUT, two), between="Noted.")) & TOOL_IDS, {"tool-one-call-one-question"})
        first = {"questions": GOOD_TOOL_INPUT["questions"] * 2}
        self.only(tool_case_for(inputs=(first,)), "tool-one-call-one-question")

    def test_batching_in_one_message_or_without_a_result_between_is_one_call_one_question_violation(self):
        self.only(tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), batched=True), "tool-one-call-one-question")
        # two assistant messages, but the first call got no tool_result before the second call
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)"), Ask(SECOND, "Skip for now")], [CLOSING_LINE]])
        self.assertEqual(ask_tool.unanswered_overlaps(case.turn), ["toolu_INVENTED_Q1"])
        self.assertIn("tool-one-call-one-question", failed(case))

    def test_sequential_calls_with_results_between_them_are_not_batching(self):
        self.only(tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND, THIRD), between="Noted."))

    def test_a_header_of_twelve_code_points_passes_and_thirteen_fails(self):
        self.only(tool_case_for(inputs=(tool_input(header="Architecture"),)))
        self.only(tool_case_for(inputs=(tool_input(header="Architectures"),)), "tool-header-short")
        # characters are counted as code points (len of a str): six 'e' plus a combining accent are twelve, seven are fourteen
        self.only(tool_case_for(inputs=(tool_input(header="é" * 6),)))
        self.only(tool_case_for(inputs=(tool_input(header="é" * 7),)), "tool-header-short")

    def test_a_header_that_is_empty_or_missing(self):
        self.only(tool_case_for(inputs=(tool_input(header=" "),)), "tool-header-short")
        data = tool_input()
        del data["questions"][0]["header"]
        self.only(tool_case_for(inputs=(data,)), "tool-header-short")

    def test_options_shape_variants(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        for name, options in (("one option", good[-1:]), ("five options", good[:2] * 2 + good[-1:]),
                              ("empty description", [dict(good[0], description=""), good[1], good[2]]),
                              ("no label", [{"description": "x"}, good[1], good[2]])):
            with self.subTest(name):
                self.assertIn("tool-options-shape", failed(tool_case_for(inputs=(tool_input(options=options),))))
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
        for name, options in (("recommended second", [good[1], dict(good[0]), good[2]]),
                              ("two recommended", [good[0], dict(good[1], label="SQLite (Recommended)"), good[2]])):
            with self.subTest(name):
                self.only(tool_case_for(inputs=(tool_input(options=options),)), "tool-recommended-first")

    def test_skip_option_not_last_or_misspelled(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        for name, options in (("skip first", [good[2], good[0], good[1]]), ("lower case", [good[0], good[1], dict(good[2], label="skip for now")]),
                              ("suffix", [good[0], good[1], dict(good[2], label="Skip for now.")])):
            with self.subTest(name):
                self.assertIn("tool-skip-last", failed(tool_case_for(inputs=(tool_input(options=options),))))

    def test_a_trailing_space_is_accepted_the_same_way_for_both_labels(self):
        good = GOOD_TOOL_INPUT["questions"][0]["options"]
        spaced = [dict(good[0], label="Postgres (Recommended) "), good[1], dict(good[2], label="Skip for now ")]
        self.only(tool_case_for(inputs=(tool_input(options=spaced),)))
        self.only(tool_case_for(inputs=(tool_input(options=[good[0], good[1], dict(good[2], label=" Skip for now")]),)))

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


class HeaderNumberChainTests(unittest.TestCase):
    """header-number, per call and in order: the first call is previous + 1; later calls follow the previous call's item."""

    def test_the_first_call_of_a_first_turn_must_be_item_one(self):
        self.assertIn("header-number", failed(tool_case_for(inputs=(SECOND,))))

    def test_a_jump_over_an_item_fails(self):
        self.assertEqual(failed(tool_case_for(inputs=(GOOD_TOOL_INPUT, THIRD), between="Noted.")), {"header-number"})

    def test_the_same_item_again_is_fine_after_free_text_and_wrong_after_a_label(self):
        again = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Good point, here it is again.", answers=("Which one is cheaper?", "stop"))
        self.assertEqual(failed(again), set(), notes(again))
        after_label = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.", answers=("SQLite", "stop"))
        self.assertEqual(failed(after_label), {"header-number"})
        after_skip = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.", answers=("Skip for now", "stop"))
        self.assertEqual(failed(after_skip), {"header-number"})

    def test_free_text_that_mentions_an_option_is_still_free_text(self):
        # the label is matched exactly: "Is SQLite enough?" is a question, so the same item may come again
        case = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT, SECOND), between="Yes, for one writer it is enough.",
                             answers=("Is SQLite enough?", "SQLite", "stop"))
        self.assertEqual(failed(case), set(), notes(case))
        self.assertIsNone(ask_tool.ask_records(case.turn)[0]["label"])
        self.assertEqual(ask_tool.ask_records(case.turn)[1]["label"], "SQLite")

    def test_a_label_with_surrounding_whitespace_is_still_the_label(self):
        record = ask_tool.ask_records(make_walk_events([[Ask(GOOD_TOOL_INPUT, " SQLite \n")]]))[0]
        self.assertEqual(record["label"], "SQLite")

    def test_free_text_may_also_move_on_to_the_next_item(self):
        self.assertEqual(failed(tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Noted.", answers=("SQLite, because it is cheap", "stop"))), set())


class TitleRuleTests(unittest.TestCase):
    """Pending titles other than the current one stay out of every text outside the question, and out of the option descriptions."""

    def test_another_title_in_an_option_description_fails_no_other_items(self):
        options = [{"label": "Postgres (Recommended)", "description": "Then Confirm the launch date and Assign the security review."},
                   GOOD_TOOL_INPUT["questions"][0]["options"][1], GOOD_TOOL_INPUT["questions"][0]["options"][2]]
        self.assertEqual(failed(tool_case_for(inputs=(tool_input(options=options),))), {"no-other-items"})

    def test_the_count_line_does_not_print_the_list(self):
        text = "5 items need you: Pick the database, Confirm the launch date, Assign the security review. First one:"
        self.assertEqual(failed(tool_case_for(text=text)), {"tool-no-extra-text"})

    def test_the_count_line_may_name_the_current_item_but_no_other(self):
        self.assertEqual(failed(tool_case_for(text="5 items need you. First one, Pick the database:")), set())
        self.assertEqual(failed(tool_case_for(text="5 items need you, after Confirm the launch date. First one:")), {"tool-no-extra-text"})

    def test_the_plain_line_between_calls_may_name_the_item_just_handled_but_not_a_third(self):
        ok = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Parked Pick the database.", answers=("Skip for now", "stop"))
        self.assertEqual(failed(ok), set(), notes(ok))
        bad = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Parked Pick the database; Assign the security review is next.")
        self.assertEqual(failed(bad), {"tool-no-extra-text"})

    def test_the_closing_line_may_not_name_a_third_item(self):
        self.assertEqual(failed(tool_case_for(closing="Noted. Confirm the launch date comes next.")), {"tool-no-extra-text"})


class ExtraTextTests(unittest.TestCase):
    """tool-no-extra-text: the one-line count before the first call, ONE short plain line between calls, nothing else."""

    def only(self, case, *ids):
        self.assertEqual(failed(case), set(ids), notes(case))

    def walk(self, between=None, text=COUNT_LINE, closing=CLOSING_LINE, answers=("Postgres (Recommended)", "stop")):
        return tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between=between, text=text, closing=closing, answers=answers)

    def test_the_count_line_alone_and_no_text_are_both_allowed(self):
        self.only(tool_case_for(text=COUNT_LINE, closing=""))
        self.only(tool_case_for(text="", closing=""))

    def test_one_plain_line_between_calls_is_allowed_and_none_too(self):
        self.only(self.walk(between="Noted, Postgres it is."))
        self.only(self.walk(between=""))

    def test_extra_text_before_the_first_call(self):
        for name, text in (("two lines", COUNT_LINE + "\nNext, think about it."), ("a numbered option", "1. Postgres"),
                           ("a second header", "Item 2 of 5, risk: Something."), ("a label", "**Options:** none"),
                           ("a long paragraph", "word " * 40)):
            with self.subTest(name):
                self.only(tool_case_for(text=text), "tool-no-extra-text")

    def test_extra_text_between_calls(self):
        for name, between in (("two lines", "Noted.\nHere is more."), ("a list", "- one\n- two"), ("a numbered line", "1. Postgres"),
                              ("a header", "Item 2 of 5, question: Launch."), ("a label", "**Why it matters:** because."),
                              ("a long line", "word " * 31), ("a question", "Does that sound right?"),
                              ("a look-ahead", "Noted. Up next is the launch."), ("another item number", "Noted. Item 4 is waiting.")):
            with self.subTest(name):
                self.only(self.walk(between=between), "tool-no-extra-text")

    def test_a_short_plain_line_of_thirty_words_passes_and_thirty_one_fails(self):
        self.only(self.walk(between=" ".join(["word"] * 30)))
        self.only(self.walk(between=" ".join(["word"] * 31)), "tool-no-extra-text")

    def test_two_text_blocks_in_one_gap_are_two_lines(self):
        events = make_tool_events()
        events[-2]["message"]["content"].insert(0, {"type": "text", "text": "Let me think."})
        self.only(checks.Case.build(case_for(ITEM, "x").row, events), "tool-no-extra-text")

    def test_the_item_number_of_the_call_before_and_after_the_line_is_allowed(self):
        self.only(self.walk(between="Noted for item 1. Item 2 follows."))
        self.only(self.walk(between="Noted for item 1."))

    def test_the_closing_text_after_the_last_call(self):
        self.only(tool_case_for(closing="word " * 40), "tool-no-extra-text")
        self.only(tool_case_for(closing=WRAPUP))
        self.only(tool_case_for(closing=WRAPUP.replace("**Next action:** Confirm the launch date.", "")), "tool-no-extra-text")

    def test_a_lone_label_line_fails_before_between_and_after_the_calls(self):
        for line in ("**Parked:** the database.", "**Decided:** Postgres.", "Next action: the launch date.", "- **One question:** which?"):
            with self.subTest(line):
                self.only(self.walk(between=line), "tool-no-extra-text")
                self.only(tool_case_for(text=line), "tool-no-extra-text")
                self.only(tool_case_for(closing=line), "tool-no-extra-text")

    def test_plain_lines_that_start_with_a_label_word_are_not_labels(self):
        for line in ("Parked.", "Parked for later.", "Decided, Postgres it is.", "Options noted."):
            with self.subTest(line):
                self.only(self.walk(between=line))

    def test_a_wrap_up_that_is_too_long_fails(self):
        self.only(tool_case_for(closing=WRAPUP + "\n" + "word " * 200), "tool-no-extra-text")

    def test_the_tangent_flag_may_be_the_text_after_the_last_call(self):
        flag = "This may be a tangent from Item 4 of 6, the retention rule. Continue or refocus?"
        self.only(tool_case_for(closing=flag))
        self.only(tool_case_for(closing=flag + " And which runtime?"), "tool-no-extra-text")


class DetoursAndTurnsTests(unittest.TestCase):
    def only(self, case, *ids):
        self.assertEqual(failed(case), set(ids), notes(case))

    def test_ask_calls_are_not_detours(self):
        # four calls (and four model turns for them) on the tool path: neither tool-detours nor turns-bound counts them
        case = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND, THIRD, item_input(4, "Rename the staging branch")), between="Noted.")
        self.only(case)
        self.assertEqual(trace.stats(case.events)[0], 6)
        self.assertGreater(trace.stats(case.events)[0], checks.MAX_TURNS)

    def test_a_skip_rows_milestone_read_plus_one_call_per_turn_does_not_fail_detours(self):
        row = case_for(SKIP, "x").row
        events = make_tool_events(others=("Read",)) + make_tool_events(inputs=(SECOND,), text="", called=())
        self.only(checks.Case.build(row, events))
        # the same trace with three more Reads does fail: other tools still count
        more = make_tool_events(others=("Read", "Read", "Read")) + make_tool_events(inputs=(SECOND,), text="", called=())
        self.only(checks.Case.build(row, more), "tool-detours")

    def test_other_tool_calls_still_count(self):
        self.only(tool_case_for(others=("Read", "Read", "Read"), turns=3), "tool-detours")
        self.only(tool_case_for(searches=("select:NotebookEdit", "select:NotebookEdit", "select:NotebookEdit"), turns=3), "tool-detours")

    def test_turns_beyond_the_calls_still_fail(self):
        self.only(tool_case_for(turns=9), "turns-bound")
        self.only(tool_case_for(turns=5))   # one call: 5 - 1 = 4, at the bound
        self.only(tool_case_for(turns=6), "turns-bound")

    def test_a_text_turn_gets_no_allowance(self):
        self.assertFalse(verdicts(case_for(ITEM, GOOD_ITEM, turns=5))["turns-bound"][0])


class NotAllowedTests(unittest.TestCase):
    """On rows that allow no item presentation (stop, tangent, list, none) ANY AskUserQuestion call is a violation."""

    def test_a_call_on_a_stop_row_fails(self):
        case = stop_row_with_call()
        self.assertIn("tool-not-allowed", failed(case))
        self.assertEqual(failed(case), {"tool-not-allowed"})

    def test_a_denied_or_unanswered_call_on_those_rows_fails_too(self):
        for answer, error in (("denied", True), (None, False)):
            case = walk_case(STOP, [[Ask(tool_input(question="Which?"), answer, error)], [WRAPUP]], called=())
            self.assertIn("tool-not-allowed", failed(case), answer)

    def test_a_call_on_a_tangent_row_none_row_and_list_row_fails(self):
        flag = "This may be a tangent from Pick the database. Continue or refocus?"
        cases = {
            TANGENT: walk_case(TANGENT, [[Ask(tool_input(question="Podman or docker?"), "Skip for now")], [flag]], called=()),
            "negative-single": walk_case("negative-single", [[Ask(tool_input(question="Which?"), "x")], ["A plain answer."]], called=()),
            "negative-list": walk_case("negative-list", [[Ask(tool_input(question="Which?"), "x")], ["- Pick the database\n- Confirm the launch date\n"
                                                              "- Assign the security review\n- Rename the staging branch\n- Approve the logo change"]], called=()),
        }
        for row_id, case in cases.items():
            with self.subTest(row_id):
                self.assertIn("tool-not-allowed", failed(case))

    def test_the_same_rows_without_a_call_pass(self):
        flag = "This may be a tangent from Pick the database. Continue or refocus?"
        for row_id, reply in ((STOP, WRAPUP), (TANGENT, flag), ("negative-single", "A plain answer.")):
            self.assertTrue(verdicts(case_for(row_id, reply))["tool-not-allowed"][0], row_id)

    def test_a_header_in_a_question_text_fails_no_oaat_header_on_those_rows(self):
        case = late_row(TANGENT, [[Ask(GOOD_TOOL_INPUT, "x")], ["This may be a tangent from Pick the database. Continue or refocus?"]])
        self.assertEqual(failed(case), {"no-oaat-header", "tool-not-allowed"})
        self.assertIn("in AskUserQuestion questions: 1", verdicts(case)["no-oaat-header"][1])

    def test_a_header_in_the_second_question_of_a_call_is_caught(self):
        two = {"questions": [tool_input(question="Which?")["questions"][0], GOOD_TOOL_INPUT["questions"][0]]}
        flag = "This may be a tangent from Pick the database. Continue or refocus?"
        case = walk_case(TANGENT, [[Ask(two, "x")], [flag]], called=())
        self.assertIn("no-oaat-header", failed(case))

    def test_the_item_rows_are_unaffected(self):
        for row_id in (ITEM, SKIP):
            self.assertNotIn("tool-not-allowed", verdicts(tool_case_for(row_id)))


class SearchTests(unittest.TestCase):
    def only(self, case, *ids):
        self.assertEqual(failed(case), set(ids), notes(case))

    def test_no_tool_search_on_the_tool_path_and_on_the_fallback(self):
        self.only(tool_case_for(searches=("select:AskUserQuestion",)), "no-tool-search")
        self.only(fallback_with_search("ask user question"), "no-tool-search")
        self.only(fallback_with_search("ask_user_question tool"), "no-tool-search")

    def test_a_search_for_another_tool_is_allowed(self):
        self.only(tool_case_for(searches=("select:NotebookEdit",)))

    def test_no_tool_search_applies_to_every_expect(self):
        for row_id in (ITEM, SKIP, STOP, TANGENT, "negative-list", "negative-single"):
            self.assertIn("no-tool-search", verdicts(case_for(row_id, GOOD_ITEM)), row_id)

    def test_a_call_in_a_subagent_does_not_count(self):
        events = make_tool_events()
        for event in events:
            if event.get("type") in ("assistant", "user"):
                event["parent_tool_use_id"] = "toolu_PARENT"
        self.assertFalse(checks.Case.build(case_for(ITEM, "x").row, events).tool_path)


class EarlyEndTests(unittest.TestCase):
    """tool-early-end: after a chosen label or `Skip for now` with items left, the turn goes on with a call (or a fallback template)."""

    def early(self, case):
        return verdicts(case)["tool-early-end"]

    def test_a_closing_line_or_a_wrap_up_after_a_label_is_an_early_end(self):
        for name, closing in (("closing line", CLOSING_LINE), ("wrap-up", WRAPUP), ("no text", "")):
            with self.subTest(name):
                case = tool_case_for(answers=("Postgres (Recommended)",), closing=closing)
                self.assertIn("tool-early-end", failed(case), notes(case))

    def test_a_skip_is_also_a_chosen_option(self):
        case = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Parked.", answers=("Postgres (Recommended)", "Skip for now"))
        self.assertEqual(failed(case), {"tool-early-end"}, notes(case))
        self.assertIn("call 2 (item 2 of 5)", self.early(case)[1])

    def test_the_label_may_be_in_the_observed_envelope_or_bare_or_in_other_case(self):
        for envelope, answer in (("wrapper", "Postgres (Recommended)"), ("bare", "Postgres (Recommended)"), ("mapping", "Postgres (Recommended)"),
                                 ("wrapper", "POSTGRES (RECOMMENDED)")):
            with self.subTest(envelope + answer):
                case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, answer, envelope=envelope)], [CLOSING_LINE]])
                self.assertEqual(failed(case), {"tool-early-end"}, notes(case))

    def test_free_text_is_not_an_early_end(self):
        for answer in ("stop", "stop, enough for today", "Which one is cheaper?", "by the way, podman or docker?"):
            with self.subTest(answer):
                self.assertTrue(self.early(tool_case_for(answers=(answer,)))[0])

    def test_the_tangent_flag_after_a_label_is_still_an_early_end(self):
        self.assertFalse(self.early(walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], [FLAG]]))[0])

    def test_the_last_item_may_be_followed_by_the_wrap_up(self):
        case = tool_case_for(inputs=FIVE, between="Noted.", answers=("Postgres (Recommended)", "Friday the 14th (Recommended)", "Skip for now",
                                                                    "Assign it to one engineer (Recommended)", "Postgres (Recommended)"), closing=WRAPUP)
        self.assertEqual(failed(case), set(), notes(case))
        self.assertIn("item 5 of 5", self.early(case)[1])

    def test_a_later_call_counts_whether_it_was_answered_or_not(self):
        denied = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "denied", error=True)], [ITEM2_TEXT]])
        self.assertTrue(self.early(denied)[0], self.early(denied))
        self.assertTrue(self.early(tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between="Noted."))[0])

    def test_a_fallback_template_for_a_later_item_ends_the_turn_properly(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], [ITEM2_TEXT]])
        self.assertTrue(self.early(case)[0], self.early(case))
        self.assertIn("text template for a later item", self.early(case)[1])
        # a template for the SAME item is not a next item
        again = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], [TMPL1]])
        self.assertFalse(self.early(again)[0])

    def test_a_denied_only_turn_is_on_the_text_path_and_has_no_early_end_check(self):
        self.assertNotIn("tool-early-end", verdicts(walk_case(ITEM, [[Ask(GOOD_TOOL_INPUT, "denied", error=True)], [TMPL1]])))

    def test_a_question_without_a_header_is_left_to_the_shape_check(self):
        case = tool_case_for(inputs=(tool_input(question="Which database should the first release use?"),), answers=("Postgres (Recommended)",))
        self.assertTrue(self.early(case)[0])
        self.assertIn("tool-question-shape", failed(case))


class TemplateEndsTurnTests(unittest.TestCase):
    """tool-template-ends-turn: a template item printed as text ends the turn, on both paths."""

    def test_a_fallback_template_as_the_last_text_passes(self):
        for name, case in (("after a denied first call", walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "denied", error=True)], [TMPL1]])),
                           ("no call at all", case_for(ITEM, GOOD_ITEM))):
            with self.subTest(name):
                self.assertTrue(verdicts(case)["tool-template-ends-turn"][0], verdicts(case)["tool-template-ends-turn"])
                self.assertEqual(failed(case), set(), notes(case))

    def test_a_call_after_a_template_fails_wherever_the_template_is(self):
        before_first = walk_case(ITEM, [[TMPL1, Ask(GOOD_TOOL_INPUT, "denied", error=True)], [TMPL1]])
        after_answered = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], [ITEM2_TEXT, Ask(SECOND, "stop")], ["Noted."]])
        same_message = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "denied", error=True)], [TMPL1, Ask(GOOD_TOOL_INPUT, "stop")]])
        for name, case in (("before the first call, text path", before_first), ("after an answered call", after_answered), ("same message", same_message)):
            with self.subTest(name):
                self.assertIn("tool-template-ends-turn", failed(case), notes(case))
                self.assertRegex(verdicts(case)["tool-template-ends-turn"][1], r"calls after the first of them: [12]")

    def test_the_other_texts_are_not_templates(self):
        for name, text in (("a lone header line", "Item 2 of 5, question: Launch."),
                           ("the tangent flag naming an item", "This may be a tangent from Item 4 of 6, the retention rule. Continue or refocus?"),
                           ("the wrap-up", WRAPUP), ("a count line", COUNT_LINE)):
            with self.subTest(name):
                case = tool_case_for(inputs=(GOOD_TOOL_INPUT, SECOND), between=text, answers=("Postgres (Recommended)", "stop"))
                self.assertTrue(verdicts(case)["tool-template-ends-turn"][0], name)


class RetryTests(unittest.TestCase):
    """A call that failed with an error and is called again at once, once, for the SAME item and answered is allowed (F2)."""

    def test_retry_once_passes_every_check_with_no_text_after_the_failed_call(self):
        case = retry_once()
        self.assertTrue(case.tool_path)
        self.assertEqual(failed(case), set(), notes(case))
        self.assertIn("retried once for the same item, not counted: 1", verdicts(case)["tool-denied-fallback"][1])
        self.assertEqual(checks.retried(case), {1})

    def test_a_plain_line_between_the_failed_call_and_its_retry_is_graded_as_a_plain_line(self):
        def walk(line):
            return walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [line, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")],
                                    ["Noted.", Ask(SECOND, "stop")], [WRAPUP]])
        self.assertEqual(failed(walk("Shortening the header and asking again.")), set())
        self.assertEqual(failed(walk("Shortening the header.\nAsking again.")), {"tool-no-extra-text"})
        self.assertEqual(failed(walk("word " * 31)), {"tool-no-extra-text"})
        self.assertIn("tool-template-ends-turn", failed(walk(TMPL1)))

    def test_a_failed_call_followed_by_another_item_still_needs_its_text(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [Ask(SECOND, "stop")], [WRAPUP]])
        self.assertEqual(failed(case), {"tool-denied-fallback"}, notes(case))
        self.assertEqual(checks.retried(case), set())

    def test_a_failed_call_with_no_retry_still_needs_the_template_text(self):
        self.assertEqual(failed(denied_after_answered()), {"tool-denied-fallback"})
        self.assertEqual(failed(walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [TMPL1]])), set())

    def test_the_retry_is_made_once_a_second_failure_needs_its_text(self):
        case = retry_twice()
        self.assertEqual(failed(case), {"tool-denied-fallback"}, notes(case))
        self.assertEqual(checks.retried(case), {1})
        self.assertIn("with no text after them: 1", verdicts(case)["tool-denied-fallback"][1])

    def test_a_retry_that_fails_again_is_followed_by_the_template(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [Ask(GOOD_TOOL_INPUT, FAIL_TEXT, error=True)], [TMPL1]])
        self.assertEqual(failed(case), set(), notes(case))

    def test_a_call_with_no_result_is_not_a_retry_case(self):
        case = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, None)], [Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "stop")], [WRAPUP]])
        self.assertEqual(checks.retried(case), set())
        self.assertIn("tool-denied-fallback", failed(case))
        self.assertIn("tool-one-call-one-question", failed(case))


class TangentFlagEndsTheTurnTests(unittest.TestCase):
    """Y1: the flag is the closing text of the turn, never text before a call."""

    def test_the_flag_as_the_closing_text_passes_and_the_flag_before_a_call_fails(self):
        self.assertEqual(failed(flag_as_closing()), set(), notes(flag_as_closing()))
        case = flag_before_a_call()
        self.assertEqual(failed(case), {"tool-no-extra-text"})
        self.assertIn("the tangent flag before a call", verdicts(case)["tool-no-extra-text"][1])

    def test_the_flag_in_every_position_before_a_call_fails(self):
        before_first = walk_case(ITEM, [[FLAG, Ask(GOOD_TOOL_INPUT, "Postgres (Recommended)")], ["Noted.", Ask(SECOND, "stop")], [WRAPUP]])
        same_message = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Is SQLite enough?")], [FLAG, Ask(GOOD_TOOL_INPUT, "stop")], [WRAPUP]])
        to_next_item = walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Is SQLite enough?")], [FLAG, Ask(SECOND, "stop")], [WRAPUP]])
        for name, case in (("before the first call", before_first), ("same message as a call", same_message), ("before the next item", to_next_item)):
            with self.subTest(name):
                self.assertIn("tool-no-extra-text", failed(case), notes(case))

    def test_the_flag_still_may_not_hide_a_header_or_a_second_question(self):
        asked = [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Is SQLite enough?")]]
        self.assertEqual(failed(walk_case(ITEM, asked + [[FLAG + " And which runtime?"]])), {"tool-no-extra-text"})
        self.assertEqual(failed(walk_case(ITEM, asked + [[FLAG + "\n**Item 1 of 5, decision: Pick the database**"]])), {"tool-no-extra-text"})

    def test_the_flag_naming_an_item_header_is_allowed_only_as_the_closing_text(self):
        flag = "This may be a tangent from Item 4 of 6, the retention rule. Continue or refocus?"
        asked = [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Is SQLite enough?")]]
        self.assertEqual(failed(walk_case(ITEM, asked + [[flag]])), set())
        self.assertIn("tool-no-extra-text", failed(walk_case(ITEM, [[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Is SQLite enough?")], [flag, Ask(GOOD_TOOL_INPUT, "stop")]])))


class CountLineTests(unittest.TestCase):
    def test_the_count_line_limit_is_thirty_words_like_the_plain_line(self):
        self.assertEqual(checks.MAX_COUNT_LINE_WORDS, checks.MAX_PLAIN_LINE_WORDS)
        for words, expected in ((20, set()), (21, set()), (30, set()), (31, {"tool-no-extra-text"})):
            with self.subTest(words):
                self.assertEqual(failed(count_line_of(words)), expected, notes(count_line_of(words)))
        natural = "You have five items waiting on you before the release, ordered by what blocks other work most. Here is the first:"
        self.assertEqual(failed(tool_case_for(text=natural)), set())

    def test_a_period_inside_the_item_named_by_the_flag_is_a_known_limit(self):
        # N1c, documented in the README: X holds no period, so this flag is not recognised as the flag (it fails the tangent check)
        flag = "This may be a tangent from Item 1 of 5 (the database, e.g. Postgres). Continue or refocus?"
        self.assertFalse(verdicts(case_for(TANGENT, flag))["tangent-flag"][0])


class MilestoneTests(unittest.TestCase):
    """M1: recording a milestone answer through the milestones skill is bookkeeping, not a detour and not an extra turn."""

    def test_a_walk_of_three_milestone_items_passes_every_check(self):
        case = milestone_walk()
        self.assertEqual(failed(case), set(), notes(case))
        self.assertEqual(len(case.asks), 3)
        self.assertEqual(len(ask_tool.bookkeeping_calls(case.turn)), 7)   # the Read, 3 Skill calls and 3 Bash calls
        self.assertGreater(trace.stats(case.events)[0] - len(case.asks), checks.MAX_TURNS)   # it would fail turns-bound without the exemption

    def test_only_the_bookkeeping_calls_are_exempt(self):
        cases = {
            "a Bash call that is not milestones.py": dict(bookkeeping=(MS_SKILL, Use("Bash", {"command": "ls milestones.py.bak"}))),
            "a Read of another file, and the milestone Read besides": dict(others=(("Read", {"file_path": "/invented/project/README.md"}),) * 3),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                case = milestone_walk(**kwargs)
                self.assertIn("tool-detours", failed(case), notes(case))

    def test_the_helper_names_the_three_kinds_and_nothing_else(self):
        yes = [("Skill", {"skill": "milestones:milestones"}), ("Skill", {"skill": "milestones"}), ("Skill", {"skill": "other-plugin:milestones"}),
               ("Bash", {"command": "python3 /a/b/milestones.py show"}), ("Bash", {"command": 'python3 "/path with space/milestones.py" set 2 --left x'}),
               ("Bash", {"command": "cd /p && python3 milestones.py title Plan"}), ("Read", {"file_path": "/p/.cache/milestones.json"}),
               ("Read", {"file_path": ".cache/milestones.json"})]
        no = [("Skill", {"skill": "oaat:oaat"}), ("Skill", {"skill": "milestones:other"}), ("Skill", {}), ("Bash", {"command": "ls"}),
              ("Bash", {"command": "cat milestones.py.bak"}), ("Bash", {}), ("Bash", {"command": "echo 'unterminated"}),
              ("Read", {"file_path": "/p/milestones.json"}), ("Read", {"file_path": "/p/.cache/other.json"}), ("Read", {}),
              ("Write", {"file_path": ".cache/milestones.json"}), ("AskUserQuestion", {})]
        for name, data in yes:
            self.assertTrue(ask_tool.is_bookkeeping(name, data), (name, data))
        for name, data in no:
            self.assertFalse(ask_tool.is_bookkeeping(name, data), (name, data))
        self.assertFalse(ask_tool.is_bookkeeping("Skill", None))

    def test_the_notes_name_the_exempt_calls(self):
        verdict = verdicts(milestone_walk())
        self.assertIn("milestone bookkeeping, not counted: 4", verdict["tool-detours"][1])   # the Read and 3 Bash calls (Skill calls are never detours)
        self.assertIn("minus 7 milestone bookkeeping call(s)", verdict["turns-bound"][1])


class EnvelopeTests(unittest.TestCase):
    """The answer is read out of the OBSERVED wrapper (one sample, one version: UNCONFIRMED), a bare label or a JSON mapping."""

    QUESTION = GOOD_QUESTION

    def record(self, answer, envelope="wrapper", tool_input=GOOD_TOOL_INPUT):
        return ask_tool.ask_records(make_walk_events([[Ask(tool_input, answer, envelope=envelope)]]))[0]

    def test_the_observed_sentence_is_parsed(self):
        content = (f'Your questions have been answered: "{self.QUESTION}"="Postgres (Recommended)". You can now continue with these answers in mind.')
        self.assertEqual(wrap_answer(GOOD_TOOL_INPUT, "Postgres (Recommended)"), content)
        record = self.record("Postgres (Recommended)")
        self.assertEqual((record["answer"], record["envelope"], record["label"]), ("Postgres (Recommended)", "wrapper", "Postgres (Recommended)"))
        self.assertEqual(record["result"]["content"], content)

    def test_a_chosen_label_is_a_label_and_free_text_is_free_text_in_every_envelope(self):
        for envelope in ("wrapper", "bare", "mapping"):
            with self.subTest(envelope):
                chosen = self.record("SQLite", envelope)
                self.assertEqual((chosen["label"], chosen["envelope"]), ("SQLite", envelope))
                self.assertEqual(self.record("Skip for now", envelope)["label"], "Skip for now")
                free = self.record("Is SQLite enough?", envelope)
                self.assertIsNone(free["label"])
                self.assertEqual(free["answer"], "Is SQLite enough?")

    def test_labels_are_matched_ignoring_case_and_whitespace(self):
        for envelope in ("wrapper", "bare", "mapping"):
            for answer in ("sqlite", "SQLITE", "  SQLite \n", "skip for now"):
                with self.subTest(envelope + answer):
                    self.assertIn(self.record(answer, envelope)["label"], ("SQLite", "Skip for now"))   # the canonical spelling of the call's option
        self.assertEqual(self.record("postgres (recommended)")["label"], "Postgres (Recommended)")
        # the suffix is part of the label: the bare name is free text
        self.assertIsNone(self.record("Postgres")["label"])

    def test_free_text_with_quotes_commas_and_periods_is_read_whole(self):
        for answer in ('He said "no", then left.', "SQLite, because it is cheap.", 'a"="b', "Yes."):
            with self.subTest(answer):
                record = self.record(answer)
                self.assertEqual(record["answer"], answer)
                self.assertIsNone(record["label"])

    def test_a_call_with_two_questions_reads_the_first_answer(self):
        content = f'Your questions have been answered: "{self.QUESTION}"="SQLite", "Which date?"="Friday". You can now continue with these answers in mind.'
        self.assertEqual(ask_tool.answer_of(content, [self.QUESTION, "Which date?"]), ("SQLite", "wrapper"))
        self.assertEqual(ask_tool.wrapper_pairs('"Q1"="A, 1", "Q2"="B"', ["Q1", "Q2"]), {"Q1": "A, 1", "Q2": "B"})
        two = {"questions": [GOOD_TOOL_INPUT["questions"][0], dict(GOOD_TOOL_INPUT["questions"][0], question="Which date?")]}
        record = self.record("Postgres (Recommended)", tool_input=two)
        self.assertEqual((record["answer"], record["label"]), ("Postgres (Recommended)", "Postgres (Recommended)"))
        self.assertEqual(record["result"]["content"].count('"="Postgres (Recommended)"'), 2)

    def test_a_wrapper_whose_question_text_is_not_the_calls_is_still_read(self):
        content = 'Your questions have been answered: "Another text"="SQLite". You can now continue with these answers in mind.'
        self.assertEqual(ask_tool.answer_of(content, [self.QUESTION]), ("SQLite", "wrapper"))
        self.assertEqual(ask_tool.answer_of(content, []), ("SQLite", "wrapper"))

    def test_an_unparsable_wrapper_reads_as_free_text(self):
        content = "Your questions have been answered: nothing in quotes. You can now continue with these answers in mind."
        self.assertEqual(ask_tool.answer_of(content, [self.QUESTION]), (content, "wrapper"))
        self.assertIsNone(ask_tool.match_label(content, ["SQLite"]))

    def test_the_mapping_may_be_plain_or_under_answers(self):
        self.assertEqual(ask_tool.answer_of(json.dumps({"answers": {self.QUESTION: "SQLite"}}), [self.QUESTION]), ("SQLite", "mapping"))
        self.assertEqual(ask_tool.answer_of(json.dumps({self.QUESTION: "SQLite"}), [self.QUESTION]), ("SQLite", "mapping"))
        self.assertEqual(ask_tool.answer_of(json.dumps({"other": "SQLite"}), [self.QUESTION]), ("SQLite", "mapping"))
        self.assertEqual(ask_tool.answer_of(json.dumps({"n": 1}), [self.QUESTION])[1], "bare")
        self.assertEqual(ask_tool.answer_of("{broken", [self.QUESTION]), ("{broken", "bare"))

    def test_a_denied_call_has_no_answer(self):
        record = ask_tool.ask_records(make_walk_events([[Ask(GOOD_TOOL_INPUT, "denied", error=True)]]))[0]
        self.assertEqual((record["answered"], record["answer"], record["envelope"], record["label"]), (False, None, None, None))

    def test_a_wrapper_in_a_list_of_text_blocks_is_read(self):
        events = make_walk_events([[Ask(GOOD_TOOL_INPUT, "SQLite")]])
        for event in events:
            if event.get("type") == "user" and event["message"]["content"][0]["tool_use_id"].startswith("toolu_INVENTED_Q"):
                block = event["message"]["content"][0]
                block["content"] = [{"type": "text", "text": block["content"]}]
        self.assertEqual(ask_tool.ask_records(events)[0]["label"], "SQLite")

    def test_the_header_chain_reads_the_label_out_of_the_wrapper(self):
        again = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.", answers=("SQLite", "stop"))
        self.assertEqual(failed(again), {"header-number"})
        free = tool_case_for(inputs=(GOOD_TOOL_INPUT, GOOD_TOOL_INPUT), between="Again.", answers=("Is SQLite enough?", "stop"))
        self.assertEqual(failed(free), set(), notes(free))


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

    def test_records_pair_each_call_with_its_result(self):
        events = make_walk_events([[COUNT_LINE, Ask(GOOD_TOOL_INPUT, "Skip for now")], [Ask(SECOND, "denied", error=True)], [Ask(THIRD, None)]])
        records = ask_tool.ask_records(events)
        self.assertEqual([r["answered"] for r in records], [True, False, False])
        self.assertEqual(records[0]["label"], "Skip for now")
        self.assertTrue(records[1]["result"]["error"])
        self.assertIsNone(records[2]["result"])

    def test_a_free_text_answer_has_no_label(self):
        record = ask_tool.ask_records(make_walk_events([[Ask(GOOD_TOOL_INPUT, "stop, enough for today")]]))[0]
        self.assertTrue(record["answered"])
        self.assertIsNone(record["label"])

    def test_tool_result_content_may_be_a_list_of_text_blocks(self):
        events = make_walk_events([[Ask(GOOD_TOOL_INPUT, "x")]])
        for event in events:
            if event.get("type") == "user" and event["message"]["content"][0]["tool_use_id"].startswith("toolu_INVENTED_Q"):
                event["message"]["content"][0]["content"] = [{"type": "text", "text": "SQLite"}]
        self.assertEqual(ask_tool.ask_records(events)[0]["label"], "SQLite")

    def test_segments_are_the_text_between_the_calls(self):
        events = make_walk_events([["count", Ask(GOOD_TOOL_INPUT, "SQLite")], ["between", Ask(SECOND, "Skip for now")], ["closing"]])
        self.assertEqual(ask_tool.segments(events), ["count", "between", "closing"])
        self.assertEqual(ask_tool.segments(make_events("only text")), ["only text"])


if __name__ == "__main__":
    unittest.main()
