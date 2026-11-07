"""The prompt table: its shape, its coverage of the prompt kinds, and the fixture block that the checks read."""
import csv
import tempfile
import unittest
from pathlib import Path

from helpers import PROMPTS, ROWS
import cases


def write_csv(rows, header=cases.COLUMNS):
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    writer = csv.writer(handle)
    writer.writerow(header)
    writer.writerows(rows)
    handle.close()
    return Path(handle.name)


class PromptTableTests(unittest.TestCase):
    def test_about_ten_prompts_with_unique_ids(self):
        self.assertGreaterEqual(len(ROWS), 10)
        self.assertLessEqual(len(ROWS), 12)

    def test_every_kind_the_post_names_is_there(self):
        kinds = {row.kind for row in ROWS.values()}
        self.assertEqual(kinds, set(cases.KINDS))
        self.assertEqual({row.expect for row in ROWS.values()}, set(cases.EXPECTS))

    def test_two_or_three_negative_controls_that_must_not_trigger(self):
        negatives = [row for row in ROWS.values() if row.kind == "negative"]
        self.assertIn(len(negatives), (2, 3))
        self.assertTrue(all(not row.should_trigger for row in negatives))
        self.assertTrue(all(row.should_trigger for row in ROWS.values() if row.kind != "negative"))

    def test_a_request_for_the_full_list_is_a_negative_control(self):
        self.assertFalse(ROWS["negative-list"].should_trigger)
        self.assertEqual(ROWS["negative-list"].expect, "list")

    def test_every_triggering_prompt_carries_four_or_five_pending_items(self):
        for row in ROWS.values():
            if row.should_trigger or row.expect == "list":
                self.assertIn(len(cases.pending_titles(row.prompt)), (4, 5), row.id)

    def test_the_item_titles_do_not_contain_each_other(self):
        for row in ROWS.values():
            titles = [t.lower() for t in cases.pending_titles(row.prompt)]
            for a in titles:
                for b in titles:
                    if a != b:
                        self.assertNotIn(a, b, row.id)

    def test_a_followup_row_has_a_second_message_and_quotes_no_header(self):
        for name in ("followup-skip", "followup-stop", "followup-tangent"):
            self.assertTrue(ROWS[name].reply, name)
            self.assertEqual(cases.previous_header_number(ROWS[name].prompt), 0, name)
        self.assertEqual([r.id for r in ROWS.values() if r.reply], ["followup-skip", "followup-stop", "followup-tangent"])

    def test_the_title_of_a_quoted_header_is_read(self):
        text = "> **Item 3 of 5, decision: Assign the security review**\n> **What it is:** x"
        self.assertEqual(cases.previous_header_number(text), 3)
        self.assertEqual(cases.previous_header_title(text), "Assign the security review")
        self.assertEqual(cases.previous_header_title(ROWS["explicit-name"].prompt), "")

    def test_the_explicit_prompts_name_the_skill(self):
        self.assertIn("$oaat", ROWS["explicit-dollar"].prompt)
        self.assertIn("oaat", ROWS["explicit-name"].prompt)
        self.assertNotIn("oaat", ROWS["implicit-walk"].prompt)
        self.assertNotIn("oaat", ROWS["contextual-review"].prompt)

    def test_the_block_ends_at_the_first_line_that_is_not_an_item(self):
        text = "Pending items:\n- One: first.\n- Two: second.\n\n- Three: not in the block.\n"
        self.assertEqual(cases.pending_titles(text), ["One", "Two"])


class ValidationTests(unittest.TestCase):
    def test_a_valid_file_loads(self):
        self.assertEqual(len(cases.load_prompts(PROMPTS)), len(ROWS))

    def test_each_problem_is_named(self):
        good = ["a-1", "true", "explicit", "item", "text"]
        bad = {
            "duplicate id": ([good, good], "duplicate"),
            "id with a capital": ([["A1", "true", "explicit", "item", "t"]], "id must be"),
            "boolean": ([["a", "yes", "explicit", "item", "t"]], "should_trigger"),
            "kind": ([["a", "true", "weird", "item", "t"]], "kind"),
            "expect": ([["a", "true", "explicit", "weird", "t"]], "expect"),
            "empty prompt": ([["a", "true", "explicit", "item", " "]], "empty"),
        }
        for name, (rows, word) in bad.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, word):
                cases.load_prompts(write_csv(rows))

    def test_wrong_columns_are_refused(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            cases.load_prompts(write_csv([], header=("id", "prompt")))


if __name__ == "__main__":
    unittest.main()
