"""The pure-Python reader and validator of data.js. No JavaScript is executed anywhere."""
import json
import re
import unittest
from pathlib import Path

import helpers
import sitrep_data

SCHEMA = Path(__file__).resolve().parents[3] / "plugins" / "sitrep" / "skills" / "sitrep" / "schema.md"


def read(body, suffix=";"):
    return sitrep_data.parse(f"window.SITREP = {body}{suffix}")


class Reader(unittest.TestCase):
    def test_the_example_of_schema_md_is_read_and_valid_without_node(self):
        data, error = sitrep_data.parse(helpers.schema_example())
        self.assertEqual(error, "")
        self.assertEqual(sitrep_data.validate(data), [])
        self.assertEqual((data["title"], len(data["items"]), data["items"][0]["id"], data["milestones"]["current"]), ("Migration status", 2, "db", "Storage layer"))
        self.assertEqual(data["items"][1]["dependsOn"], ["db"])

    def test_json_is_read_as_json_reads_it(self):
        document = {**helpers.good_data(ms=True), "x": [1.5, -2, 300.0, None, True, False, 'a"b\\c\n\u00e9']}
        text = json.dumps(document, indent=1, ensure_ascii=False)
        self.assertEqual(sitrep_data.parse("window.SITREP = " + text)[0], json.loads(text))

    def test_comments_are_skipped_but_slashes_in_strings_are_text(self):
        data, error = read('{ // line\n a: "https://example.com/a//b", /* block\n comment */ b: \'/* not a comment */\' }', "; // end\n/* end */")
        self.assertEqual((data, error), ({"a": "https://example.com/a//b", "b": "/* not a comment */"}, ""))

    def test_string_escapes(self):
        data, _ = read(r'''["it\'s", 'say "hi"', "say \"hi\"", "a\nb\tc", "é\x41\u{1F600}", "line \
continued", "back\\slash", "\/"]''')
        self.assertEqual(data, ["it's", 'say "hi"', 'say "hi"', "a\nb\tc", "éA\U0001F600", "line continued", "back\\slash", "/"])

    def test_backtick_strings_may_span_lines(self):
        self.assertEqual(read("{a: `one\ntwo`}")[0], {"a": "one\ntwo"})

    def test_bare_quoted_and_dollar_keys_and_trailing_commas(self):
        self.assertEqual(read("{a: 1, 'b c': 2, \"d\": [1, 2,], $e_f: {},}")[0], {"a": 1, "b c": 2, "d": [1, 2], "$e_f": {}})

    def test_numbers_and_constants(self):
        self.assertEqual(read("[0, 12, -3, +4, 1.5, .5, 2e3, -1.5E-2, true, false, null]")[0], [0, 12, -3, 4, 1.5, 0.5, 2000.0, -0.015, True, False, None])
        self.assertIsInstance(read("[7]")[0][0], int)

    def test_the_assignment_may_be_spelled_without_spaces_after_other_text(self):
        self.assertEqual(sitrep_data.parse('"use strict";\n// c\nwindow.SITREP={a:1}\n')[0], {"a": 1})
        self.assertEqual(sitrep_data.parse("window . SITREP = {a: 1};")[0], {"a": 1})

    def test_only_comments_and_use_strict_may_precede_the_assignment(self):
        ok = "/* header */ 'use strict'\n// window.SITREP = {a: 0};\nwindow.SITREP = {a: 1};"
        self.assertEqual(sitrep_data.parse(ok), ({"a": 1}, ""))
        for before in ("import x from 'y';\n", "export const a = 1;\n", "var x = 1;\n", "load();\n", "const SITREP = 1;\n"):
            value, why = sitrep_data.parse(before + "window.SITREP = {a: 1};")
            self.assertIsNone(value, before)
            self.assertIn("before the `window.SITREP =` assignment", why, before)

    def test_what_is_not_readable_says_why(self):
        for body, why in [('{title: "a", items: [', "the data ends"), ('{title: "abc', "unterminated string"), ('{a: 1 /* open', "unterminated comment"),
                          ('{title: "a" + "b"}', "unsupported JavaScript"), ("{a: base.title}", "unsupported JavaScript"),
                          ("data", "unsupported JavaScript"), ("{a: `x${y}`}", "template placeholder"), ('{a: "one\ntwo"}', "line break"),
                          ('{a "x"}', "expected ':'"), ('{a: 1 b: 2}', "expected ','"), ("{a: 1,, b: 2}", "expected a property name"),
                          (r'{a: "\xZZ"}', "malformed escape"), ("{a: -}", "malformed number"), ("{a: @}", "unsupported JavaScript"),
                          ("{[k]: 1}", "expected a property name"), ("[1, 2", "the data ends"), ("", "the data ends")]:
            with self.subTest(body=body):
                data, error = read(body, "")
                self.assertIsNone(data)
                self.assertIn(why, error)

    def test_an_error_names_its_line(self):
        self.assertIn("at line 3", sitrep_data.parse('window.SITREP = {\n a: 1,\n b: ?\n}')[1])

    def test_text_after_the_object_is_refused(self):
        for tail in ("; window.SITREP.items = [];", ' console.log("x");', "; var x = 1;"):
            data, error = read("{a: 1}", tail)
            self.assertIsNone(data)
            self.assertIn("unexpected text", error)

    def test_no_assignment_is_not_data(self):
        for text in ("", "var SITREP = {a: 1};", "window.SITREP_OLD = {};", '{"title": "x"}', "window.sitrep = {};"):
            self.assertEqual(sitrep_data.parse(text), (None, "no `window.SITREP =` assignment"), text)

    def test_deep_nesting_does_not_crash_the_harness(self):
        data, error = read("[" * 5000)
        self.assertIsNone(data)
        self.assertTrue(error)


class Validator(unittest.TestCase):
    def problems(self, mutate):
        data = helpers.good_data(ms=True)
        mutate(data)
        return sitrep_data.validate(data)

    def test_the_good_data_and_a_minimal_object_are_valid(self):
        self.assertEqual(sitrep_data.validate(helpers.good_data(ms=True)), [])
        self.assertEqual(sitrep_data.validate({"title": "t", "items": [{"id": "a", "title": "x"}]}), [])
        self.assertEqual(sitrep_data.validate({"title": "t", "items": []}), [])

    def test_the_enums_equal_the_ones_written_in_schema_md(self):
        text = SCHEMA.read_text(encoding="utf-8")
        for key, enum in (("type", sitrep_data.TYPES), ("status", sitrep_data.STATUSES)):
            written = re.search(rf'\b{key}: "[a-z]+",\s*//\s*([a-z]+(?: \| [a-z]+)+)', text).group(1).split(" | ")
            self.assertEqual(list(enum), written)

    def test_each_departure_is_named(self):
        cases = [
            (lambda d: d.update(title=" "), "title must be"),
            (lambda d: d.pop("title"), "title must be"),
            (lambda d: d.update(items={}), "items must be a list"),
            (lambda d: d.pop("items"), "items must be a list"),
            (lambda d: d.update(summary=3), "summary must be a string"),
            (lambda d: d["items"].append("x"), "item 7 is not an object"),
            (lambda d: d["items"][0].pop("id"), "item 1: id must be"),
            (lambda d: d["items"][0].update(id=""), "item 1: id must be"),
            (lambda d: d["items"][0].update(id=4), "item 1: id must be"),
            (lambda d: d["items"][1].update(id="db"), "duplicate id 'db'"),
            (lambda d: d["items"][0].update(title=""), "item 1: title must be"),
            (lambda d: d["items"][0].update(type="todo"), "type 'todo'"),
            (lambda d: d["items"][0].update(status="done"), "status 'done'"),
            (lambda d: d["items"][0].update(impact=5), "impact 5"),
            (lambda d: d["items"][0].update(impact=0), "impact 0"),
            (lambda d: d["items"][0].update(urgency="2"), "urgency '2'"),
            (lambda d: d["items"][0].update(urgency=True), "urgency True"),
            (lambda d: d["items"][0].update(impact=2.5), "impact 2.5"),
            (lambda d: d["items"][0].update(dependsOn="db"), "dependsOn must be a list"),
            (lambda d: d["items"][0].update(dependsOn=[1]), "dependsOn must be a list"),
            (lambda d: d["items"][0].update(options="a"), "options must be a list"),
            (lambda d: d["items"][0].update(detail=["x"]), "detail must be a string"),
            (lambda d: d.update(next="soon"), "next must be an object"),
            (lambda d: d.update(next={"why": "x"}), "next must be an object"),
            (lambda d: d["milestones"].pop("total"), "milestones must be an object"),
            (lambda d: d["milestones"].update(done=5), "done <= total"),
            (lambda d: d["milestones"].update(done=True), "milestones must be an object"),
            (lambda d: d["milestones"].update(current=3), "title and current must be strings"),
            (lambda d: d["milestones"].update(needsYou="one"), "needsYou and blocked integers"),
        ]
        for mutate, expected in cases:
            with self.subTest(expected=expected):
                found = self.problems(mutate)
                self.assertTrue(any(expected in p for p in found), found)

    def test_a_value_that_is_not_an_object(self):
        for value in (None, [], "x", 3):
            self.assertEqual(sitrep_data.validate(value), ["the value is not an object"])

    def test_many_problems_are_all_listed(self):
        data = {"title": "", "items": [{"title": "", "type": "x"}, {"id": "a", "title": "t", "impact": 9}]}
        self.assertGreaterEqual(len(sitrep_data.validate(data)), 4)


if __name__ == "__main__":
    unittest.main()
