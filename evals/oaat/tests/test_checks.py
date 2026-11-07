"""Every deterministic check, once on a reply or trace that passes and once on one that fails. All traces are invented."""
import unittest

from helpers import GOOD_ITEM, case_for, make_events, verdicts
import checks

ITEM = "explicit-dollar"      # first turn, 5 pending items
SKIP = "followup-skip"        # the previous header is Item 1
STOP = "followup-stop"
TANGENT = "followup-tangent"
LIST = "negative-list"
NONE = "negative-single"


def passes(case, check_id):
    return verdicts(case)[check_id][0]


class ProcessChecks(unittest.TestCase):
    def test_skill_loaded(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "skill-loaded"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, skills=("sitrep:sitrep",)), "skill-loaded"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, skills=None), "skill-loaded"))

    def test_skill_trigger_when_it_should(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "skill-trigger"))
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM, called=("oaat",)), "skill-trigger"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, called=()), "skill-trigger"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, called=("sitrep:sitrep",)), "skill-trigger"))

    def test_skill_trigger_when_it_should_not(self):
        self.assertTrue(passes(case_for(NONE, "A plain answer.", called=()), "skill-trigger"))
        self.assertFalse(passes(case_for(NONE, "A plain answer."), "skill-trigger"))


class CommonChecks(unittest.TestCase):
    def test_completed(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "completed"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, subtype="error_max_turns", is_error=True), "completed"))
        events = make_events(GOOD_ITEM)[:-1]
        from checks import Case
        from helpers import ROWS
        self.assertFalse(verdicts(Case.build(ROWS[ITEM], events))["completed"][0])

    def test_tool_detours(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM, others=("Read", "Read")), "tool-detours"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, others=("Read", "Bash", "Grep")), "tool-detours"))

    def test_turns_bound(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM, turns=4), "turns-bound"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM, turns=5), "turns-bound"))


class ItemShapeChecks(unittest.TestCase):
    def test_one_header(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "one-header"))
        self.assertFalse(passes(case_for(ITEM, "Just text. What now?"), "one-header"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM + "\n**Item 2 of 5, risk**"), "one-header"))

    def test_header_number_follows_the_previous_turn(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "header-number"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.replace("Item 1 of 5", "Item 2 of 5")), "header-number"))
        second = GOOD_ITEM.replace("Item 1 of 5", "Item 2 of 5")
        self.assertTrue(passes(case_for(SKIP, second), "header-number"))
        self.assertFalse(passes(case_for(SKIP, GOOD_ITEM), "header-number"))

    def test_header_total_matches_the_items_in_the_prompt(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "header-total"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.replace("of 5", "of 3")), "header-total"))

    def test_sections(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "sections"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.replace("**Why it matters:** The choice sets hosting cost.\n", "")), "sections"))
        swapped = GOOD_ITEM.replace("**What it is:**", "**TMP:**").replace("**Why it matters:**", "**What it is:**").replace("**TMP:**", "**Why it matters:**")
        self.assertFalse(passes(case_for(ITEM, swapped), "sections"))

    def test_options_at_most_three(self):
        three = GOOD_ITEM.replace("**Recommendation:**", "3. A third option.\n**Recommendation:**")
        four = three.replace("**Recommendation:**", "4. A fourth option.\n**Recommendation:**")
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "options-max-3"))
        self.assertTrue(passes(case_for(ITEM, three), "options-max-3"))
        self.assertFalse(passes(case_for(ITEM, four), "options-max-3"))
        none = GOOD_ITEM.replace("1. Postgres: more setup, many users.\n2. SQLite: no server, one writer.\n", "")
        self.assertFalse(passes(case_for(ITEM, none), "options-max-3"))

    def test_options_are_always_numbered(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "options-numbered"))
        lettered = GOOD_ITEM.replace("1. Postgres", "A. Postgres").replace("2. SQLite", "B. SQLite")
        bullets = GOOD_ITEM.replace("1. Postgres", "- Postgres").replace("2. SQLite", "- SQLite")
        skipped = GOOD_ITEM.replace("2. SQLite", "3. SQLite")
        for reply in (lettered, bullets, skipped):
            self.assertFalse(passes(case_for(ITEM, reply), "options-numbered"), reply)
        self.assertEqual(len(checks.option_lines(lettered)), 2)   # a lettered list still counts as options for options-max-3
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM.replace(". Postgres", ") Postgres").replace(". SQLite", ") SQLite")), "options-numbered"))

    def test_exactly_one_recommendation(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "recommended-marked"))
        none = GOOD_ITEM.replace("**Recommendation:** Option 1, because growth is expected.\n", "")
        self.assertFalse(passes(case_for(ITEM, none), "recommended-marked"))
        twice = GOOD_ITEM.replace("one writer.", "one writer, recommended by the team.")
        self.assertFalse(passes(case_for(ITEM, twice), "recommended-marked"))

    def test_the_recommendation_follows_the_options_on_a_line_of_its_own(self):
        inside = GOOD_ITEM.replace("**Recommendation:** Option 1, because growth is expected.\n", "").replace("many users.", "many users. Recommended: growth is expected.")
        bullet = GOOD_ITEM.replace("**Recommendation:**", "- **Recommendation:**")
        numbered = GOOD_ITEM.replace("**Recommendation:**", "3. **Recommendation:**")
        before = GOOD_ITEM.replace("**Options:**\n", "**Options:**\n**Recommendation:** Option 1, because growth is expected.\n").replace(
            "1. Postgres: more setup, many users.\n2. SQLite: no server, one writer.\n**Recommendation:** Option 1, because growth is expected.\n",
            "1. Postgres: more setup, many users.\n2. SQLite: no server, one writer.\n")
        for reply in (inside, bullet, numbered, before):
            self.assertFalse(passes(case_for(ITEM, reply), "recommended-marked"), reply)

    def test_a_recommendation_after_the_question_does_not_count(self):
        late = GOOD_ITEM.replace("**Recommendation:** Option 1, because growth is expected.\n", "") + "\nI recommend option 1."
        self.assertFalse(passes(case_for(ITEM, late), "recommended-marked"))
        labelled = late.replace("**One question:**", "**Question:**")
        self.assertFalse(passes(case_for(ITEM, labelled), "recommended-marked"))

    def test_one_question(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "one-question"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM + " And when?"), "one-question"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.replace("use?", "use.")), "one-question"))
        moved = GOOD_ITEM.replace("**What it is:** The first release needs one database.", "**What it is:** Which one?").replace("use?", "use.")
        self.assertFalse(passes(case_for(ITEM, moved), "one-question"))

    def test_other_titles_are_absent(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "no-other-items"))
        leak = GOOD_ITEM.replace("The choice sets hosting cost.", "The choice sets hosting cost; Confirm the launch date depends on it.")
        self.assertFalse(passes(case_for(ITEM, leak), "no-other-items"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.lower().replace("pick the database", "pick the database") + "\nconfirm the launch date"), "no-other-items"))

    def test_no_preview_of_the_next_item(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "no-preview"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM + "\nNext up is item 2."), "no-preview"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM.replace("Postgres:", "Postgres (we decide item 3 after):")), "no-preview"))
        self.assertFalse(passes(case_for(ITEM, GOOD_ITEM + "\nComing up: more."), "no-preview"))

    def test_length_bound(self):
        self.assertTrue(passes(case_for(ITEM, GOOD_ITEM), "length-bound"))
        long_lines = GOOD_ITEM + "\n" + "\n".join(f"Line {i}." for i in range(6))
        self.assertFalse(passes(case_for(ITEM, long_lines), "length-bound"))
        long_words = GOOD_ITEM.replace("The choice sets hosting cost.", "The choice sets hosting cost " + "and more " * 80)
        self.assertFalse(passes(case_for(ITEM, long_words), "length-bound"))

    def test_markdown_emphasis_does_not_hide_a_label(self):
        plain = GOOD_ITEM.replace("**", "")
        self.assertTrue(passes(case_for(ITEM, plain), "sections"))
        self.assertTrue(passes(case_for(ITEM, plain), "one-header"))
        self.assertTrue(passes(case_for(ITEM, "## Item 1 of 5, decision: Pick the database\n" + "\n".join(GOOD_ITEM.splitlines()[3:])), "one-header"))


class SkipConfirmationTests(unittest.TestCase):
    """On a skip, the skill confirms the answer in one line. The reply may name the item that was just parked."""
    SECOND = GOOD_ITEM.replace("Item 1 of 5, decision: Pick the database", "Item 2 of 5, question: Confirm the launch date")

    def test_naming_the_parked_item_is_allowed_on_a_skip(self):
        reply = "Parked Pick the database (item 1). Next:\n\n" + self.SECOND
        self.assertTrue(passes(case_for(SKIP, reply), "no-other-items"))
        self.assertTrue(passes(case_for(SKIP, reply), "no-preview"))

    def test_naming_another_item_is_still_refused_on_a_skip(self):
        reply = "Parked Pick the database. Next:\n\n" + self.SECOND.replace("one database.", "one database; Rename the staging branch follows.")
        self.assertFalse(passes(case_for(SKIP, reply), "no-other-items"))

    def test_a_look_ahead_is_still_refused_on_a_skip(self):
        reply = "Parked item 1. Next:\n\n" + self.SECOND + "\nAfter that, item 3."
        self.assertFalse(passes(case_for(SKIP, reply), "no-preview"))

    def test_the_parked_title_is_not_exempt_on_a_first_turn(self):
        self.assertEqual(checks.parked_title(case_for(ITEM, GOOD_ITEM)), "")
        self.assertEqual(checks.parked_title(case_for(SKIP, self.SECOND)), "pick the database")


class OtherExpectChecks(unittest.TestCase):
    WRAPUP = "**Decided:**\n- Pick the database: Postgres.\n\n**Parked:**\n- Confirm the launch date.\n\n**Next action:** Assign the review."

    def test_wrapup_headings(self):
        self.assertTrue(passes(case_for(STOP, self.WRAPUP), "wrapup-headings"))
        self.assertFalse(passes(case_for(STOP, self.WRAPUP.replace("**Next action:**", "**Then:**")), "wrapup-headings"))
        reordered = "**Parked:**\n- x\n**Decided:**\n- y\n**Next action:** z"
        self.assertFalse(passes(case_for(STOP, reordered), "wrapup-headings"))

    def test_a_stop_reply_has_no_new_item(self):
        self.assertTrue(passes(case_for(STOP, self.WRAPUP), "no-oaat-header"))
        self.assertFalse(passes(case_for(STOP, self.WRAPUP + "\n**Item 4 of 5, question**"), "no-oaat-header"))

    def test_tangent_flag(self):
        good = "This may be a tangent from the database decision. Continue or refocus?"
        self.assertTrue(passes(case_for(TANGENT, good), "tangent-flag"))
        self.assertFalse(passes(case_for(TANGENT, "Podman is nice. Back to the database?"), "tangent-flag"))
        self.assertFalse(passes(case_for(TANGENT, "This may be a tangent. Continue or refocus?"), "tangent-flag"))

    def test_a_tangent_reply_asks_one_question(self):
        good = "This may be a tangent from the database decision. Continue or refocus?"
        self.assertTrue(passes(case_for(TANGENT, good), "one-question"))
        self.assertFalse(passes(case_for(TANGENT, good + " Which runtime?"), "one-question"))

    def test_all_items_listed(self):
        titles = "\n".join(f"- {t}" for t in ("Pick the database", "Confirm the launch date", "Assign the security review",
                                              "Rename the staging branch", "Approve the logo change"))
        self.assertTrue(passes(case_for(LIST, titles, called=()), "all-items-listed"))
        self.assertFalse(passes(case_for(LIST, "- Pick the database", called=()), "all-items-listed"))

    def test_no_header_for_a_plain_answer(self):
        self.assertTrue(passes(case_for(NONE, "A plain answer.", called=()), "no-oaat-header"))
        self.assertFalse(passes(case_for(NONE, GOOD_ITEM, called=()), "no-oaat-header"))


class RunChecksTests(unittest.TestCase):
    def test_the_good_reply_passes_every_check_that_applies(self):
        results = checks.run_checks(case_for(ITEM, GOOD_ITEM))
        self.assertEqual([cid for cid, ok, _ in results if not ok], [])
        self.assertEqual(len(results), 16)

    def test_each_result_is_an_id_a_boolean_and_a_note(self):
        for cid, ok, note in checks.run_checks(case_for(ITEM, "nothing useful")):
            self.assertIsInstance(cid, str)
            self.assertIsInstance(ok, bool)
            self.assertTrue(note)

    def test_every_check_id_belongs_to_a_group(self):
        for expect, row_id in (("item", ITEM), ("stop", STOP), ("tangent", TANGENT), ("list", LIST), ("none", NONE)):
            for cid, _, _ in checks.run_checks(case_for(row_id, "x")):
                self.assertIn(checks.GROUPS[cid], ("outcome", "process", "style", "efficiency"), expect)

    def test_the_four_groups_are_all_used(self):
        self.assertEqual(set(checks.GROUPS.values()), {"outcome", "process", "style", "efficiency"})

    def test_the_bounds_are_the_stated_ones(self):
        self.assertEqual((checks.MAX_REPLY_LINES, checks.MAX_REPLY_WORDS, checks.MAX_OPTIONS), (12, 180, 3))


if __name__ == "__main__":
    unittest.main()
