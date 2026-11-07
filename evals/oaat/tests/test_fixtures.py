"""The invented fixture traces and the captures: the committed files equal what the builder and the demo print now."""
import io
import tempfile
import unittest
from pathlib import Path

import build_fixtures
from helpers import PROMPTS, ROWS, TRACES, UNIT
import agent_trace as trace
import checks
import demo

CAPTURES = UNIT / "captures"
BEFORE_FAILS = {
    "implicit-walk": {"skill-trigger", "one-header", "header-number", "header-total", "sections", "options-max-3", "options-numbered", "recommended-marked",
                      "one-question", "no-other-items"},
    "contextual-review": {"options-max-3", "one-question", "no-preview"},
    "followup-skip": {"header-number"},
    "followup-stop": {"wrapup-headings"},
    "negative-list": {"skill-trigger", "all-items-listed", "no-oaat-header"},
}


def failing(name, prompt_id):
    case = checks.Case.build(ROWS[prompt_id], trace.load(TRACES / name / f"{prompt_id}.jsonl"))
    return {cid for cid, ok, _ in checks.run_checks(case) if not ok}


class FixtureTests(unittest.TestCase):
    def test_the_committed_traces_equal_the_builder_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_fixtures.write(tmp)
            for path in sorted(Path(tmp).rglob("*.jsonl")):   # the builder writes after/ and before/ only; real/ is a recording
                committed = TRACES / path.relative_to(tmp)
                self.assertEqual(committed.read_text(encoding="utf-8"), path.read_text(encoding="utf-8"), committed)
        self.assertEqual(len(list(TRACES.glob("after/*.jsonl"))) + len(list(TRACES.glob("before/*.jsonl"))), 2 * len(ROWS))

    def test_there_is_one_trace_per_prompt_in_each_set(self):
        for name in ("after", "before"):
            self.assertEqual(sorted(p.stem for p in (TRACES / name).glob("*.jsonl")), sorted(ROWS))

    def test_the_edited_skill_passes_every_check_on_every_prompt(self):
        for prompt_id in ROWS:
            self.assertEqual(failing("after", prompt_id), set(), prompt_id)

    def test_the_unedited_skill_fails_exactly_the_five_planted_defects(self):
        for prompt_id in ROWS:
            self.assertEqual(failing("before", prompt_id), BEFORE_FAILS.get(prompt_id, set()), prompt_id)

    def test_every_check_fails_somewhere_in_the_fixtures_or_the_unit_tests(self):
        seen = set().union(*BEFORE_FAILS.values())
        self.assertLessEqual(seen, set(checks.GROUPS))
        self.assertGreaterEqual(len(seen), 12)   # test_checks.py covers the rest, check by check

    def test_the_traces_say_they_are_invented(self):
        readme = (TRACES.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("INVENTED", readme)


class RealTraceTests(unittest.TestCase):
    """tests/fixtures/traces/real/explicit-name.jsonl: one real `claude -p` run (Claude Code 2.1.318, 2026-11-04), trimmed."""

    def setUp(self):
        self.path = TRACES / "real" / "explicit-name.jsonl"
        self.case = checks.Case.build(ROWS["explicit-name"], trace.load(self.path))

    def test_the_stream_shape_matches_what_the_harness_reads(self):
        self.assertEqual(trace.init_skills(self.case.events), ["oaat:oaat"])
        self.assertEqual(trace.skills_invoked(self.case.events), ["oaat:oaat"])
        self.assertEqual(trace.stats(self.case.events), (3, 0.0870393))

    def test_the_real_reply_fails_exactly_the_checks_that_name_its_two_causes(self):
        failed = {cid for cid, ok, _ in checks.run_checks(self.case) if not ok}
        self.assertEqual(failed, {"sections", "length-bound", "recommended-marked"})   # 'Question:' for 'One question:'; 168 words with the user's TLDR block; the recommendation is inside option 1

    def test_the_options_list_ends_where_the_options_end(self):
        self.assertEqual(len(checks.option_lines(self.case.reply)), 3)

    def test_the_trimmed_file_holds_no_path_and_no_user_name(self):
        text = self.path.read_text(encoding="utf-8")
        for needle in ("/Users/", "/private/", "/home/", "ondra", "ondrej"):
            self.assertNotIn(needle, text.lower() if needle.islower() else text, needle)


class CaptureTests(unittest.TestCase):
    def test_each_capture_equals_the_live_output_of_the_demo(self):
        for name in demo.SECTIONS:
            file_name = {"after": "grade-after.txt", "before": "grade-before.txt", "real": "grade-real.txt", "compare": "compare.txt", "judge-prompt": "judge-prompt.txt"}[name]
            self.assertEqual((CAPTURES / file_name).read_text(encoding="utf-8"), demo.render(name), file_name)

    def test_the_captures_hold_no_absolute_path_and_no_home_directory(self):
        for path in CAPTURES.glob("*.txt"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(str(UNIT), text, path.name)
            self.assertNotIn("/Users/", text, path.name)
            self.assertNotIn("/tmp/", text, path.name)

    def test_the_demo_refuses_an_unknown_section(self):
        self.assertEqual(demo.main(["nope"]), 2)


if __name__ == "__main__":
    unittest.main()
