"""The rubric pass: the schema, the validator, the grading prompt and the judge command (a stub, no model)."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import ROWS, TRACES, GOOD_ITEM, stub_judge
import judge
import runner_cmd

GOOD = {"overall_pass": True, "score": 90, "checks": [{"id": "x", "pass": True, "notes": "fine"}]}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = judge.load_schema()

    def test_the_schema_has_the_fields_of_the_post(self):
        self.assertEqual(self.schema["required"], ["overall_pass", "score", "checks"])
        self.assertFalse(self.schema["additionalProperties"])
        item = self.schema["properties"]["checks"]["items"]
        self.assertEqual(item["required"], ["id", "pass", "notes"])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual((self.schema["properties"]["score"]["minimum"], self.schema["properties"]["score"]["maximum"]), (0, 100))

    def test_a_conforming_verdict_has_no_errors(self):
        self.assertEqual(judge.validate(GOOD, self.schema), [])

    def test_each_kind_of_violation_is_reported(self):
        cases = {
            "missing field": ({"overall_pass": True, "score": 1}, "missing checks"),
            "extra field": (dict(GOOD, extra=1), "unexpected property extra"),
            "wrong type": (dict(GOOD, overall_pass="yes"), "expected boolean"),
            "score too high": (dict(GOOD, score=101), "above 100"),
            "score negative": (dict(GOOD, score=-1), "below 0"),
            "score a float": (dict(GOOD, score=90.5), "expected integer"),
            "score a bool": (dict(GOOD, score=True), "expected integer"),
            "no checks": (dict(GOOD, checks=[]), "at least 1"),
            "check missing notes": (dict(GOOD, checks=[{"id": "x", "pass": True}]), "missing notes"),
            "check extra field": (dict(GOOD, checks=[{"id": "x", "pass": True, "notes": "", "z": 1}]), "unexpected property z"),
            "not an object": ([1], "expected object"),
        }
        for name, (value, word) in cases.items():
            with self.subTest(name):
                errors = judge.validate(value, self.schema)
                self.assertTrue(any(word in e for e in errors), errors)

    def test_the_validator_refuses_a_keyword_it_does_not_support(self):
        with self.assertRaisesRegex(ValueError, "pattern"):
            judge.validate("x", {"type": "string", "pattern": "x"})

    def test_the_schema_uses_only_supported_keywords(self):
        known = {"type", "properties", "required", "additionalProperties", "items", "minItems", "minimum", "maximum", "$schema", "title", "description"}

        def keys(node):
            found = set(node)
            for child in list(node.get("properties", {}).values()) + ([node["items"]] if "items" in node else []):
                found |= keys(child)
            return found
        self.assertLessEqual(keys(self.schema), known)


class ExtractTests(unittest.TestCase):
    def test_json_alone_in_a_fence_and_after_a_sentence(self):
        text = json.dumps(GOOD)
        self.assertEqual(judge.extract_json(text), GOOD)
        self.assertEqual(judge.extract_json("Verdict:\n```json\n" + text + "\n```\nbye"), GOOD)
        self.assertEqual(judge.extract_json("{not json} then " + text), GOOD)

    def test_no_json_is_an_error(self):
        with self.assertRaises(ValueError):
            judge.extract_json("fine, it passes")


class PromptTests(unittest.TestCase):
    def test_the_prompt_holds_the_criteria_the_user_message_the_reply_and_the_schema(self):
        prompt = judge.build_prompt(ROWS["explicit-dollar"], GOOD_ITEM)
        for ident, _ in judge.criteria_for("item"):
            self.assertIn(f"- {ident}:", prompt)
        self.assertIn(ROWS["explicit-dollar"].prompt.strip(), prompt)
        self.assertIn(GOOD_ITEM, prompt)
        self.assertIn('"overall_pass"', prompt)

    def test_the_skill_text_is_included_only_when_given(self):
        self.assertNotIn("<skill>", judge.build_prompt(ROWS["explicit-dollar"], "r"))
        prompt = judge.build_prompt(ROWS["explicit-dollar"], "r", skill_text="INVENTED SKILL TEXT")
        self.assertIn("<skill>\nINVENTED SKILL TEXT\n</skill>", prompt)

    def test_every_expect_has_criteria_with_unique_ids(self):
        for expect in ("item", "skip", "stop", "tangent", "list", "none"):
            ids = [i for i, _ in judge.criteria_for(expect)]
            self.assertTrue(ids)
            self.assertEqual(len(ids), len(set(ids)))

    def test_an_empty_reply_is_named(self):
        self.assertIn("(empty reply)", judge.build_prompt(ROWS["explicit-dollar"], ""))


class GradeTests(unittest.TestCase):
    def run_mode(self, mode, prompt_id="explicit-dollar"):
        with mock.patch.dict(os.environ, {"STUB_JUDGE_MODE": mode}):
            return judge.grade(ROWS[prompt_id], GOOD_ITEM, stub_judge())

    def test_a_passing_verdict(self):
        verdict, errors = self.run_mode("pass")
        self.assertEqual(errors, [])
        self.assertTrue(verdict["overall_pass"])
        self.assertEqual([c["id"] for c in verdict["checks"]], [i for i, _ in judge.criteria_for("item")])

    def test_a_failing_verdict_is_still_a_valid_one(self):
        verdict, errors = self.run_mode("fail")
        self.assertEqual(errors, [])
        self.assertFalse(verdict["overall_pass"])

    def test_json_in_a_fence_is_accepted(self):
        self.assertEqual(self.run_mode("fenced")[1], [])

    def test_a_bad_reply_gives_errors_and_no_verdict(self):
        for mode, word in (("garbage", "no JSON"), ("badschema", "above 100"), ("wrongids", "differ"), ("crash", "exited 3")):
            with self.subTest(mode):
                verdict, errors = self.run_mode(mode)
                self.assertIsNone(verdict)
                self.assertTrue(any(word in e for e in errors), errors)

    def test_the_criteria_follow_the_prompt_kind(self):
        verdict, _ = self.run_mode("pass", "followup-stop")
        self.assertEqual([c["id"] for c in verdict["checks"]], [i for i, _ in judge.criteria_for("stop")])


class CommandTests(unittest.TestCase):
    def test_cli_prints_the_prompt_without_a_judge_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = judge.main(["followup-skip", str(TRACES / "after" / "followup-skip.jsonl")])
        self.assertEqual(code, 0)
        self.assertIn("Item 2 of 5", out.getvalue())

    def test_cli_runs_the_judge_command_and_prints_the_verdict(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {"STUB_JUDGE_MODE": "pass"}):
            code = judge.main(["followup-skip", str(TRACES / "after" / "followup-skip.jsonl"), "--judge-cmd", stub_judge()])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["overall_pass"])

    def test_the_default_judge_command_is_claude_with_the_prompt_in_place(self):
        import run_evals
        self.assertEqual(runner_cmd.build(run_evals.DEFAULT_JUDGE_CMD, "P"), ["claude", "-p", "P"])


if __name__ == "__main__":
    unittest.main()
