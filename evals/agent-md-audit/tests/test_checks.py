"""Every check has a passing trace and a failing one. A test fails when a check id has no failing trace (see test_every_check_is_mutated)."""
import unittest

import checks
import helpers
from helpers import DEFAULT_CALLS, DOCS, PARTS, SKILL

ORIGINAL_CLAUDE_LINES = 229
SKILL_CALL = DEFAULT_CALLS[0]
FETCH = DEFAULT_CALLS[1]
READ = DEFAULT_CALLS[2]

# check id -> (expect, override of helpers.good that must make exactly that check fail)
MUTATIONS = {
    "skill-loaded": ("proposal", dict(init_skills=())),
    "skill-trigger": ("proposal", dict(calls1=[FETCH, READ])),
    "research-first": ("proposal", dict(calls1=[SKILL_CALL, READ])),
    "docs-cited": ("proposal", dict(omit=("sources",))),
    "no-edit-before-approval": ("proposal", dict(after1_edit={"CLAUDE.md": "changed\n"})),
    "no-mutating-tool-before-approval": ("proposal", dict(calls1=DEFAULT_CALLS + [("Write", {"file_path": "x"})])),
    "verdict-given": ("proposal", dict(omit=("verdict",))),
    "table-present": ("proposal", dict(omit=("table",))),
    "confidence-marked": ("proposal", dict(omit=("confidence",))),
    "asks-approval": ("proposal", dict(omit=("approval",))),
    "finds-dead-import": ("proposal", dict(omit=("dead-import",))),
    "finds-stale-command": ("proposal", dict(omit=("stale-command",))),
    "finds-emphasis": ("proposal", dict(omit=("emphasis",))),
    "finds-contradiction": ("proposal", dict(omit=("contradiction",))),
    "finds-size": ("proposal", dict(omit=("size",))),
    "verdict-keep": ("keep", dict(reply1=f"Verdict: CLAUDE.md is fine. Source: {DOCS}")),
    "clean-not-overhauled": ("keep", dict(reply1="Verdict: CLAUDE.md keep.\n\n| # | P |\n| - | - |\n" + "\n".join(f"| {i} | x |" for i in range(6)))),
    "apply-edited-claude-md": ("apply", dict(after2_claude=None)),   # replaced below by the original text
    "apply-shrunk": ("apply", dict(after2_claude="x\n" * 100)),
    "apply-dead-import-gone": ("apply", dict(after2_claude="# Demo\n@docs/missing-guide.md\n")),
    "apply-emphasis-gone": ("apply", dict(after2_claude="# Demo\nNEVER EVER skip\n")),
    "apply-contradiction-gone": ("apply", dict(after2_claude="Use tabs\nUse 2-space\n")),
    "apply-agents-unchanged": ("apply", dict(after2_agents="# changed\n")),
    "apply-import-resolves": ("apply", dict(after2_claude="# Demo\n@docs/other.md\n")),
    "apply-no-commit": ("apply", dict(after2_head="h2")),
    "apply-no-new-files": ("apply", dict(after2_extra={".claude/settings.json": "{}"})),
    "apply-reported": ("apply", dict(reply2="done.")),
}


def run(expect, **override):
    row, events, states = helpers.good(expect, **override)
    return {cid: (ok, note) for cid, ok, note in checks.run_checks(checks.Case.build(row, events, states))}


class GoodRuns(unittest.TestCase):
    def test_every_good_run_passes_every_check(self):
        for expect in ("none", "proposal", "keep", "apply"):
            for cid, (ok, note) in run(expect).items():
                self.assertTrue(ok, f"{expect}: {cid}: {note}")

    def test_the_checks_that_run_are_known(self):
        for expect in ("none", "proposal", "keep", "apply"):
            self.assertLessEqual(set(run(expect)), set(checks.GROUPS))


class Mutations(unittest.TestCase):
    def test_every_check_is_mutated(self):
        self.assertEqual(set(MUTATIONS), set(checks.GROUPS))

    def test_each_mutation_fails_its_check(self):
        for cid, (expect, override) in MUTATIONS.items():
            if cid == "apply-edited-claude-md":
                override = dict(after2_claude=helpers.fixtures.build("defects")["CLAUDE.md"])
            with self.subTest(check=cid):
                results = run(expect, **override)
                self.assertIn(cid, results)
                self.assertFalse(results[cid][0], f"{cid} still passes: {results[cid][1]}")

    def test_a_mutation_does_not_break_unrelated_checks(self):
        # the proposal-content mutations must fail only their own check
        for cid in ("docs-cited", "verdict-given", "table-present", "finds-size", "asks-approval", "finds-contradiction"):
            expect, override = MUTATIONS[cid]
            failed = [k for k, (ok, _) in run(expect, **override).items() if not ok]
            self.assertEqual(failed, [cid])


class Trigger(unittest.TestCase):
    def test_a_negative_that_loads_the_skill_fails(self):
        results = run("none", calls1=[SKILL_CALL], should_trigger=False)
        self.assertFalse(results["skill-trigger"][0])

    def test_a_negative_without_the_skill_passes(self):
        self.assertTrue(run("none", calls1=[("Read", {"file_path": "x"})], should_trigger=False)["skill-trigger"][0])


class ResearchOrder(unittest.TestCase):
    def test_a_subagent_fetch_counts(self):
        calls = [SKILL_CALL, ("Agent", {"subagent_type": "web-fetch", "prompt": "Fetch https://code.claude.com/docs/en/memory"}), READ]
        self.assertTrue(run("proposal", calls1=calls)["research-first"][0])

    def test_a_search_counts(self):
        self.assertTrue(run("proposal", calls1=[SKILL_CALL, ("WebSearch", {"query": "claude code memory"}), READ])["research-first"][0])

    def test_reading_before_researching_fails(self):
        self.assertFalse(run("proposal", calls1=[SKILL_CALL, READ, FETCH])["research-first"][0])

    def test_parallel_calls_of_one_message_count_as_simultaneous(self):
        row, events, states = helpers.good("proposal", calls1=[SKILL_CALL])
        events[-1:-1] = [helpers.call("Read", mid="same", file_path="/w/AGENTS.md"), helpers.call("WebFetch", mid="same", url=DOCS)]
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertTrue(results["research-first"])

    def test_research_with_no_instruction_file_read_passes(self):
        self.assertTrue(run("proposal", calls1=[SKILL_CALL, FETCH])["research-first"][0])


class BashMutation(unittest.TestCase):
    def test_commands(self):
        mutating = ["rm CLAUDE.md", "mv a b", "sed -i '' s/a/b/ CLAUDE.md", "echo x > CLAUDE.md", "cat a >> b", "git add -A",
                    "git commit -m x", "tee out.txt", "cp a b"]
        reading = ["wc -l CLAUDE.md", "wc -l CLAUDE.md 2>/dev/null", "git status --porcelain -- CLAUDE.md", "ls -la", "claude --version",
                   "git ls-files | head", "cmd 2>&1", "cat AGENTS.md"]
        for command in mutating:
            self.assertTrue(checks.bash_mutates(command), command)
        for command in reading:
            self.assertFalse(checks.bash_mutates(command), command)

    def test_a_mutating_bash_call_in_turn_one_fails(self):
        results = run("proposal", calls1=DEFAULT_CALLS + [("Bash", {"command": "rm -f CLAUDE.md"})])
        self.assertFalse(results["no-mutating-tool-before-approval"][0])


class Turns(unittest.TestCase):
    def test_a_trace_without_a_second_turn_fails_the_apply_checks(self):
        row, events, states = helpers.good("apply")
        states["after2"] = None
        events = events[:events.index(next(e for e in events if e.get("type") == "result")) + 1]
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["apply-edited-claude-md"])
        self.assertFalse(results["apply-reported"])

    def test_split_turns(self):
        row, events, states = helpers.good("apply")
        self.assertEqual(len(checks.split_turns(events)), 2)

    def test_the_edit_in_turn_two_is_not_a_gate_violation(self):
        self.assertTrue(run("apply")["no-mutating-tool-before-approval"][0])


class Helpers(unittest.TestCase):
    def test_table_rows(self):
        self.assertEqual(checks.table_rows(PARTS["table"])[0], 3)
        self.assertEqual(checks.table_rows("no table")[0], 0)

    def test_the_original_fixture_is_229_lines(self):
        self.assertEqual(helpers.state.entry(helpers.fixtures.build("defects")["CLAUDE.md"].encode())["lines"], ORIGINAL_CLAUDE_LINES)


if __name__ == "__main__":
    unittest.main()
