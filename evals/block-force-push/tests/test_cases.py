import csv
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts python/ on the path)
import cases

UNIT = Path(__file__).resolve().parents[1]
HEADER = list(cases.COLUMNS)
OK = ["a-1", "diverged", "blocked", "push it"]


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
    def test_the_shipped_table_loads_and_covers_every_expect_once(self):
        rows = cases.load_cases(UNIT / "cases" / "cases.csv")
        self.assertEqual([r.expect for r in rows], ["blocked", "kept", "pushed"])
        self.assertEqual({r.expect for r in rows}, set(cases.EXPECTS))
        self.assertEqual(len({r.id for r in rows}), len(rows))

    def test_the_prompts_name_no_hook_and_the_control_is_a_plain_ask(self):
        by_id = {r.id: r for r in cases.load_cases(UNIT / "cases" / "cases.csv")}
        for row in by_id.values():
            self.assertNotRegex(row.prompt.lower(), r"hook|plugin|block")
        self.assertIn("--force", by_id["force-explicit"].prompt)
        self.assertNotRegex(by_id["force-implicit"].prompt, r"--force|\s-f\b|force")
        self.assertNotRegex(by_id["plain-push-control"].prompt, r"force|rewrite")


class Validation(unittest.TestCase):
    def test_a_valid_row_loads(self):
        self.assertEqual(load(OK)[0], cases.Row("a-1", "diverged", "blocked", "push it"))

    def test_each_problem_raises(self):
        for rows, header in [([broken(0, "Bad Id")], HEADER), ([OK, OK], HEADER), ([broken(1, "nowhere")], HEADER), ([broken(2, "maybe")], HEADER),
                             ([broken(3, "  ")], HEADER), ([OK], HEADER[:-1])]:
            with self.subTest(rows=rows, header=header):
                with self.assertRaises(ValueError):
                    load(*rows, header=header)

    def test_pushed_goes_with_the_ahead_fixture_and_only_with_it(self):
        self.assertEqual(load(["a", "ahead", "pushed", "p"])[0].expect, "pushed")
        for row in (["a", "diverged", "pushed", "p"], ["a", "ahead", "blocked", "p"], ["a", "ahead", "kept", "p"]):
            with self.assertRaises(ValueError):
                load(row)


if __name__ == "__main__":
    unittest.main()
