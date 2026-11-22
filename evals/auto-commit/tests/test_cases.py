import csv
import os
import re
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts python/ on the path)
import cases
import fixtures

UNIT = Path(__file__).resolve().parents[1]
HEADER = list(cases.COLUMNS)
OK = ["a-1", "dirty", "saved", "", "do the thing"]


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
    def test_the_shipped_table_loads_and_covers_every_expect_and_fixture(self):
        rows = cases.load_cases(UNIT / "cases" / "cases.csv")
        self.assertEqual({r.expect for r in rows}, set(cases.EXPECTS))
        self.assertEqual({r.fixture for r in rows}, set(fixtures.FIXTURES))
        self.assertEqual(len({r.id for r in rows}), len(rows))

    def test_no_prompt_mentions_git_or_saving(self):
        # the Stop hook's report must be the only thing in the run that asks for a commit
        for row in cases.load_cases(UNIT / "cases" / "cases.csv"):
            self.assertIsNone(re.search(r"\bgit\b|commit|\bsave|push|\bsync|branch|remote|repositor", row.prompt, re.IGNORECASE), row.id)


class Validation(unittest.TestCase):
    def test_a_valid_row(self):
        self.assertEqual(load(OK)[0].fixture, "dirty")

    def test_each_invalid_row_names_its_problem(self):
        for index, value, word in [(0, "Bad Id", "id"), (1, "nope", "fixture"), (2, "x", "expect"), (3, "no/such/file", "edits"), (4, " ", "prompt")]:
            with self.subTest(column=word), self.assertRaisesRegex(ValueError, word):
                load(broken(index, value))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load(OK, OK)

    def test_an_expect_needs_its_fixture(self):
        for expect, fixture in [("merged", "dirty"), ("saved-no-env", "dirty"), ("saved-local", "synced"), ("untouched", "dirty")]:
            with self.subTest(expect=expect), self.assertRaisesRegex(ValueError, "needs the"):
                load(["x", fixture, expect, "", "p"])
        with self.assertRaisesRegex(ValueError, "needs a fixture with a remote"):
            load(["x", "no-remote", "saved", "", "p"])

    def test_untouched_goes_with_a_read_only_prompt(self):
        with self.assertRaisesRegex(ValueError, "read-only"):
            load(["x", "synced", "untouched", "README.md", "p"])

    def test_wrong_columns(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            load(["x"], header=["id"])


if __name__ == "__main__":
    unittest.main()
