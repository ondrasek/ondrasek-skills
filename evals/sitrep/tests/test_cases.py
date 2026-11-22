import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts python/ on the path)
import cases
import fixtures

UNIT = Path(__file__).resolve().parents[1]
HEADER = ["id", "should_trigger", "kind", "fixture", "expect", "prompt", "reply"]
OK = ["a-1", "true", "explicit", "open-items", "report", "show the situation", ""]


def load(*rows, header=HEADER):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return cases.load_cases(path)


def broken(*changes):
    row = list(OK)
    for index, value in changes:
        row[index] = value
    return row


class RealTable(unittest.TestCase):
    rows = cases.load_cases(UNIT / "cases" / "cases.csv")

    def test_the_shipped_table_loads_and_covers_every_expect(self):
        self.assertEqual({r.expect for r in self.rows}, set(cases.EXPECTS))
        self.assertEqual({r.kind for r in self.rows}, set(cases.KINDS))
        self.assertEqual({r.fixture for r in self.rows}, set(fixtures.FIXTURES))
        self.assertEqual(len({r.id for r in self.rows}), len(self.rows))
        self.assertEqual(sum(1 for r in self.rows if not r.should_trigger), 3)

    def test_the_second_turn_tells_the_agent_the_user_is_done(self):
        reply = next(r.reply for r in self.rows if r.expect == "report-clean")
        self.assertIn("done", reply)
        for word in ("report.sh", "rm -", "delete"):
            self.assertNotIn(word, reply)

    def test_the_feedback_prompt_survives_the_csv_as_the_json_the_report_exports(self):
        pasted = json.loads(next(r.prompt for r in self.rows if r.expect == "feedback"))
        self.assertEqual(set(pasted), {"sitrep", "for_claude", "items"})
        self.assertEqual([i["id"] for i in pasted["items"]], ["db", "sso"])
        self.assertTrue(pasted["items"][1]["resolved"])

    def test_no_prompt_names_the_script_or_the_directory(self):
        for row in self.rows:
            for word in ("report.sh", "TMPDIR", "data.js"):
                self.assertNotIn(word, row.prompt)

    def test_the_positive_prompts_point_the_agent_at_the_fixture_files_only_where_the_case_says_so(self):
        by_id = {r.id: r for r in self.rows}
        self.assertIn("docs/open-questions.md", by_id["explicit-report"].prompt)
        self.assertNotIn("docs/open-questions.md", by_id["implicit-big-picture"].prompt)
        self.assertNotIn("open-questions", by_id["with-milestones"].prompt)


class Validation(unittest.TestCase):
    def test_a_valid_row(self):
        self.assertEqual(load(OK)[0].fixture, "open-items")

    def test_each_invalid_row_names_its_problem(self):
        for index, value, word in [(0, "Bad Id", "id"), (1, "maybe", "should_trigger"), (2, "x", "kind"), (3, "nope", "fixture"),
                                   (4, "x", "expect"), (5, " ", "prompt")]:
            with self.subTest(column=word), self.assertRaisesRegex(ValueError, word):
                load(broken((index, value)))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load(OK, OK)

    def test_none_goes_with_no_trigger(self):
        with self.assertRaisesRegex(ValueError, "expect=none"):
            load(broken((4, "none")))
        with self.assertRaisesRegex(ValueError, "expect=none"):
            load(broken((1, "false")))

    def test_the_milestone_expectation_needs_the_tracker_fixture(self):
        self.assertEqual(load(broken((3, "open-items-ms"), (4, "report-ms")))[0].expect, "report-ms")
        with self.assertRaisesRegex(ValueError, "report-ms"):
            load(broken((4, "report-ms")))
        with self.assertRaisesRegex(ValueError, "report-ms"):
            load(broken((3, "open-items-ms")))

    def test_a_reply_only_for_the_two_turn_case(self):
        self.assertEqual(load(broken((4, "report-clean"), (6, "done")))[0].reply, "done")
        with self.assertRaisesRegex(ValueError, "reply"):
            load(broken((6, "yes")))
        with self.assertRaisesRegex(ValueError, "reply"):
            load(broken((4, "report-clean")))

    def test_wrong_columns(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            load(["x"], header=["id"])


if __name__ == "__main__":
    unittest.main()
