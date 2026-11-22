import csv
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts python/ on the path)
import cases

UNIT = Path(__file__).resolve().parents[1]
HEADER = ["id", "expect", "fixture", "want", "prompt"]
OK = ["u-1", "update", "tracker", "2=done", "update it"]


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
    def setUp(self):
        self.rows = cases.load_cases(UNIT / "cases" / "cases.csv")

    def test_the_shipped_table_loads_and_covers_every_expect_and_fixture(self):
        self.assertEqual({r.expect for r in self.rows}, set(cases.EXPECTS))
        self.assertEqual({r.fixture for r in self.rows}, set(cases.fixtures.FIXTURES))
        self.assertEqual(len({r.id for r in self.rows}), len(self.rows))
        self.assertGreaterEqual(sum(1 for r in self.rows if r.expect == "none"), 3)

    def test_the_reminder_prompts_never_hint_at_the_outcome(self):
        # the outcome may come only from the hook's report: a prompt that mentions the tracker, git or saving would explain it
        for row in (r for r in self.rows if r.expect.startswith("reminded")):
            self.assertNotRegex(row.prompt.lower(), r"git|commit|save|milestone|tracker|progress", row.id)

    def test_the_trigger_prompts_do_not_use_the_slash_command(self):
        # a slash command expands without a Skill tool_use, so the trigger check would fail on correct behaviour
        for row in self.rows:
            self.assertNotIn("/milestones", row.prompt)

    def test_the_update_cases_want_what_their_prompt_says(self):
        wants = {r.id: r.want for r in self.rows}
        self.assertEqual(wants["update-finish-and-start"].states, ((2, "done"), (3, "doing")))
        blocked = wants["update-blocked-needs-you"]
        self.assertEqual((blocked.states, blocked.needs, blocked.needs_you), (((4, "blocked"),), (), (4,)))
        self.assertEqual((wants["create-tracker"].title, wants["create-tracker"].count, wants["create-tracker"].names),
                         ("Importer", 3, ("schema", "importer", "migration")))


class Validation(unittest.TestCase):
    def test_a_valid_row(self):
        self.assertEqual(load(OK)[0].want.states, ((2, "done"),))

    def test_each_invalid_row_names_its_problem(self):
        for index, value, word in [(0, "Bad Id", "id"), (1, "x", "expect"), (2, "nope", "fixture"), (3, "2=maybe", "want"), (4, " ", "prompt")]:
            with self.subTest(column=word), self.assertRaisesRegex(ValueError, word):
                load(broken(index, value))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load(OK, OK)

    def test_an_expect_needs_its_fixture(self):
        for expect, fixture, want in [("show", "bare", ""), ("update", "stale-task", "2=done"), ("create", "tracker", "title=T,count=1,names=a"),
                                      ("reminded-update", "tracker", "2=done"), ("reminded-quiet", "stale-task", "")]:
            with self.subTest(expect=expect), self.assertRaisesRegex(ValueError, "needs fixture"):
                load(["x", expect, fixture, want, "p"])

    def test_the_want_must_fit_the_expect(self):
        for expect, fixture, want in [("update", "tracker", ""), ("update", "tracker", "title=T"), ("show", "tracker", "2=done"),
                                      ("none", "bare", "count=3"), ("reminded-update", "stale-task", ""), ("reminded-quiet", "stale-idle", "2=done"),
                                      ("create", "bare", "title=T,count=3")]:
            with self.subTest(expect=expect, want=want), self.assertRaisesRegex(ValueError, "want column"):
                load(["x", expect, fixture, want, "p"])

    def test_wrong_columns(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            load(["x"], header=["id"])

    def test_should_trigger_follows_expect(self):
        triggers = {r.expect: r.should_trigger for r in load(["a", "show", "tracker", "", "p"], ["b", "none", "bare", "", "p"],
                                                             ["c", "reminded-quiet", "stale-idle", "", "p"])}
        self.assertEqual(triggers, {"show": True, "none": False, "reminded-quiet": False})


class Want(unittest.TestCase):
    def test_every_token_kind(self):
        want = cases.parse_want("2=done, 3=doing,4.needs=set,5.needs=you,title=Importer,count=3,names=a|b")
        self.assertEqual((want.states, want.needs, want.needs_you, want.title, want.count, want.names),
                         (((2, "done"), (3, "doing")), (4,), (5,), "Importer", 3, ("a", "b")))

    def test_empty_is_falsy_and_anything_else_is_truthy(self):
        self.assertFalse(cases.parse_want(""))
        self.assertTrue(cases.parse_want("2=done"))

    def test_bad_tokens(self):
        for text in ["2", "2=", "x=done", "2=finished", "2.needs=maybe", "4.needs=set,4.needs=you", "count=0", "count=a", "title=a,title=b", "2=done,2=doing", "4.needs=set,4.needs=set"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                cases.parse_want(text)


if __name__ == "__main__":
    unittest.main()
