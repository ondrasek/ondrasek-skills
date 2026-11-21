import csv
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts python/ on the path)
import cases

UNIT = Path(__file__).resolve().parents[1]
HEADER = ["id", "should_trigger", "kind", "fixture", "expect", "prompt", "reply"]
OK = ["a-1", "true", "explicit", "defects", "proposal", "audit it", ""]


def load(*rows, header=HEADER):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return cases.load_cases(path)


def broken(index, value):
    row = list(OK)
    row[index] = value
    return row


class RealTable(unittest.TestCase):
    def test_the_shipped_table_loads_and_covers_every_expect(self):
        rows = cases.load_cases(UNIT / "cases" / "cases.csv")
        self.assertEqual({r.expect for r in rows}, set(cases.EXPECTS))
        self.assertGreaterEqual(sum(1 for r in rows if not r.should_trigger), 3)
        self.assertEqual(len({r.id for r in rows}), len(rows))

    def test_the_apply_case_answers_by_content_not_question_number(self):
        reply = next(r.reply for r in cases.load_cases(UNIT / "cases" / "cases.csv") if r.expect == "apply")
        self.assertNotRegex(reply, r"\bQ\d\b")
        self.assertIn("AGENTS.md", reply)


class Validation(unittest.TestCase):
    def test_a_valid_row(self):
        self.assertEqual(load(OK)[0].fixture, "defects")

    def test_each_invalid_row_names_its_problem(self):
        for index, value, word in [(0, "Bad Id", "id"), (1, "maybe", "should_trigger"), (2, "x", "kind"), (3, "nope", "fixture"),
                                   (4, "x", "expect"), (5, " ", "prompt")]:
            with self.subTest(column=word), self.assertRaisesRegex(ValueError, word):
                load(broken(index, value))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load(OK, OK)

    def test_none_goes_with_no_trigger(self):
        with self.assertRaisesRegex(ValueError, "expect=none"):
            load(broken(4, "none"))
        with self.assertRaisesRegex(ValueError, "expect=none"):
            load(["n", "false", "negative", "bare", "proposal", "p", ""])

    def test_reply_only_for_apply(self):
        with self.assertRaisesRegex(ValueError, "reply"):
            load(broken(6, "yes"))
        with self.assertRaisesRegex(ValueError, "reply"):
            load(broken(4, "apply"))

    def test_wrong_columns(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            load(["x"], header=["id"])


if __name__ == "__main__":
    unittest.main()
