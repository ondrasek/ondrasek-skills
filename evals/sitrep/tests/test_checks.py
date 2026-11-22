"""Every check has a passing run and a failing one. A test fails when a check id has no failing run (see test_every_check_is_mutated)."""
import re
import unittest

import helpers  # noqa: F401  (puts python/ on the path: first)
import checks
from helpers import CLEAN, CLEAN_CALLS, DIR, GOOD_REPLY, LISTED_REPLY, NEW, PLUGIN, REPORT_CALLS, SCRIPT, SKILL, TRACKER, WS


def data_with(ms=False, **change):
    data = helpers.good_data(ms)
    data.update(change)
    return data


def without_deps():
    data = helpers.good_data()
    for entry in data["items"]:
        entry["dependsOn"] = []
    return data


def without_next():
    data = helpers.good_data()
    del data["next"]
    return data


def with_bad_values():
    data = helpers.good_data()
    data["items"][0].update(impact=5, type="todo")
    return data


def three_items():
    data = helpers.good_data()
    data["items"] = [i for i in data["items"] if i["id"] in ("db", "sso", "migration")]
    return data


def ms_without_item():
    data = helpers.good_data(ms=True)
    data["items"] = [i for i in data["items"] if not i["id"].startswith("ms-")]
    return data


def ms_wrong_strip():
    data = helpers.good_data(ms=True)
    data["milestones"] = {"title": "To release 1.0", "done": 0, "total": 3, "current": "Schema design"}
    return data


def ms_wrong_current():
    data = helpers.good_data(ms=True)
    data["milestones"]["current"] = "Schema design"
    return data


def with_items(**by_id):
    """good data whose items are changed by id: {id: dict of changes}, or {id: None} to drop the item."""
    data = helpers.good_data()
    data["items"] = [{**i, **by_id.get(i["id"], {})} for i in data["items"] if by_id.get(i["id"], {}) is not None]
    return data


def reversed_dependency():
    return with_items(db={"detail": "Postgres or SQLite; unblocks the schema migration", "dependsOn": ["migration"]},
                      migration={"detail": "The migration scripts differ between Postgres and SQLite", "dependsOn": []})


# The report directory is gone: everything that reads the report fails with it (a cascade written down, not accidental)
NO_REPORT = {"created-by-script", "report-outside-project", "index-untouched", "only-data-added", "data-defines-sitrep", "data-parses",
             "data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "start-here-set", "reply-hands-over"}
# a report directory that report.sh was tried for but that was not found: created-by-script saw the call
TRIED_NO_REPORT = NO_REPORT - {"created-by-script"}
# a data.js that is not there: what reads or parses it fails with it
NO_DATA = {"data-defines-sitrep", "data-parses", "data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "only-data-added", "start-here-set"}
# data.js that does not parse leaves nothing for the checks that read the parsed object
UNPARSED = {"data-valid", "items-cover-planted", "dependency-recorded", "dependency-direction", "start-here-set"}

# check id -> (expect, override of helpers.good that makes it fail, ids that fail with it by necessity); the rest of the failing
# set must be empty: a mutation that breaks another check by accident is a bug in the mutation
MUTATIONS = {
    "run-completed": ("none", dict(result_subtype="error_max_budget_usd"), set()),
    "state-recorded": ("feedback", dict(drop_state=("before",)), {"project-untouched"}),
    "skill-loaded": ("report", dict(skills=()), set()),
    "skill-trigger": ("none", dict(calls1=[("Skill", {"skill": SKILL})], should_trigger=False), set()),
    "not-permission-blocked": ("report", dict(denials=[{"tool_name": "Write", "tool_input": {"file_path": DIR + "/data.js"}}]), set()),
    "report-located": ("report", dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": NEW}, "")], report_found=False, tmp_sitrep=[]), TRIED_NO_REPORT),
    "created-by-script": ("report", dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": "mkdir -p /tmp/sitrep-report"}, ""),
                                                 ("Write", {"file_path": "/tmp/sitrep-report/index.html"})]), set()),
    "report-outside-project": ("report", dict(report_dir=WS + "/sitrep.Ab12Cd"), set()),
    "project-untouched": ("report", dict(after1_extra={"sitrep.html": "<html></html>"}), set()),
    "data-defines-sitrep": ("report", dict(report_patch={"data_text": "var SITREP = {};"}), set()),
    "data-parses": ("report", dict(data=helpers.data_js(helpers.good_data())[:300]), UNPARSED),
    "data-valid": ("report", dict(data=with_bad_values()), set()),
    "index-untouched": ("report", dict(index="<html>edited by the agent</html>\n"), set()),
    "only-data-added": ("report", dict(extra_files={"notes.md": "scratch"}), set()),
    "items-cover-planted": ("report", dict(data=three_items()), set()),
    "dependency-recorded": ("report", dict(data=without_deps()), {"dependency-direction"}),
    "dependency-direction": ("report", dict(data=reversed_dependency()), set()),
    "start-here-set": ("report", dict(data=without_next()), set()),
    "milestone-item": ("report-ms", dict(data=ms_without_item()), set()),
    "milestone-strip": ("report-ms", dict(data=ms_wrong_strip()), {"milestone-current"}),
    "milestone-current": ("report-ms", dict(data=ms_wrong_current()), set()),
    "tracker-untouched": ("report-ms", dict(after1_extra={TRACKER: "{}\n"}), {"project-untouched"}),
    "reply-hands-over": ("report", dict(reply1=LISTED_REPLY), set()),
    "report-cleaned": ("report-clean", dict(report2_exists=True), set()),
    "cleaned-by-script": ("report-clean", dict(calls2=[("Bash", {"command": f"rm -rf {DIR}"}, "")]), set()),
    "no-report-when-negative": ("none", dict(tmp_sitrep=["sitrep.Ab12Cd"]), set()),
}
SCRIPT_Q = f'"{helpers.SCRIPT}"'


TABLE = "\n\n| # | item |\n|---|---|\n" + "\n".join(f"| {n} | {t} |" for n, t in enumerate(("Pick the database", "SSO vendor", "Font licences", "Data retention", "Schema migration"), 1))
BOLD = "\n" + "\n".join(f"**#{n} {t}**" for n, t in enumerate(("Pick the database", "SSO vendor", "Font licences", "Data retention", "Schema migration"), 1))
DENIED_WRITE = [("Skill", {"skill": SKILL}), ("Bash", {"command": NEW}, DIR + "\n"),
                ("Write", {"file_path": DIR + "/data.js", "content": "..."}, "Claude requested permissions to write to data.js, but you haven't granted it yet.")]
WRITE_DENIAL = [{"tool_name": "Write", "tool_input": {"file_path": DIR + "/data.js"}}]
# commands that only name report.sh or data.js are not what the skill needs permission for: a denial of them says nothing about the skill
NODE_CHECK = f"node --check {DIR}/data.js"
NODE_DENIED = REPORT_CALLS + [("Bash", {"command": NODE_CHECK}, "Permission to use Bash with command node --check has been denied.")]
NODE_DENIAL = [{"tool_name": "Bash", "tool_input": {"command": NODE_CHECK}}]
RM_DENIED = REPORT_CALLS + [("Bash", {"command": f"rm {DIR}/data.js"}, "Permission to use Bash has been denied.")]
RM_DENIAL = [{"tool_name": "Bash", "tool_input": {"command": f"rm {DIR}/data.js"}}]
# a clean call in turn 1: the report was removed before the user said they were done
EARLY_CLEAN = REPORT_CALLS + [("Bash", {"command": CLEAN}, f"deleted {DIR}\n")]
GONE = helpers.state.report_record(DIR, None, helpers.TEMPLATE_SHA, "trace, but the directory is gone")
REFUSED_CLEAN = [("Bash", {"command": f"{SCRIPT_Q} clean {DIR}/"}, "refusing to delete: " + DIR + "/\n"), ("Bash", {"command": 'rm -rf "$TMPDIR"/sitrep.*'}, "")]
SPOKEN_SUMMARY = (f"The report is at {DIR}/index.html. 5 open items: 2 decisions (Postgres vs SQLite, SSO vendor), 1 risk (licence audit), "
                  "1 question (retention), 1 blocker (schema migration). Start with the database decision. Press Copy JSON to send your comments back.")
# The real with-milestones shape (invented text, not the recorded trace): ONE compound exploration command, denied as a whole under dontAsk
# because `git log` and `head` were not granted; the agent then read files, never ran report.sh and answered in text
EXPLORE = "ls -la . .cache 2>&1; cat .cache/milestones.json 2>&1; git log --oneline | head; cat /p/skills/sitrep/schema.md"
DONTASK = ("Permission to use Bash has been denied because Claude Code is running in don't ask mode. IMPORTANT: You *may* attempt to "
           "accomplish this action using other tools that might naturally be used to accomplish this goal.")
STOPPED_CALLS = [("Skill", {"skill": SKILL}), ("Bash", {"command": EXPLORE}, DONTASK), ("Read", {"file_path": "/r/ws/.cache/milestones.json"})]
STOPPED = dict(calls1=STOPPED_CALLS, report_found=False, tmp_sitrep=[])
EXPLORE_DENIAL = [{"tool_name": "Bash", "tool_use_id": "toolu_x", "tool_input": {"command": EXPLORE}}]
# everything that reads the report or the milestone mapping fails with it (report.sh never ran, so there is no report)
STOPPED_CASCADE = NO_REPORT | {"milestone-item", "milestone-strip", "milestone-current"}
# (label, check id, expect, override, cascade): further failing runs of a check, the reviewer's and the ones a check was fixed for
MORE_MUTATIONS = [
    ("a turn that ends in an error result", "run-completed", "feedback", dict(result_subtype="error_during_execution"), set()),
    ("a refusal that reaches the trace only as tool_result text", "not-permission-blocked", "report", dict(calls1=DENIED_WRITE, omit_data=True), NO_DATA),
    ("a refused write of data.js plus an edited index.html", "index-untouched", "report", dict(denials=WRITE_DENIAL, index="<html>edited</html>\n"), {"not-permission-blocked"}),
    ("a table of every item in the reply", "reply-hands-over", "report", dict(reply1=GOOD_REPLY + TABLE), set()),
    ("bold lines of every item in the reply", "reply-hands-over", "report", dict(reply1=GOOD_REPLY + BOLD), set()),
    ("a sentence per item in the reply", "reply-hands-over", "report",
     dict(reply1=GOOD_REPLY + " Postgres or SQLite is open. The SSO vendor is open. The licence audit is open. Retention is open. The migration waits."), set()),
    ("a reply that pastes the list as one long paragraph", "reply-hands-over", "report", dict(reply1=GOOD_REPLY + " Details: " + "Postgres needs operations, SQLite has one writer. " * 40), set()),
    ("a reply that says index.html but names no directory", "reply-hands-over", "report", dict(reply1="I could not open index.html. Copy JSON is not available here."), set()),
    ("a dependency recorded between a migration and its database item, backwards", "dependency-direction", "report", dict(data=reversed_dependency()), set()),
    ("a dependency on an item that does not exist", "dependency-recorded", "report", dict(data=with_items(migration={"dependsOn": ["nowhere"]}, db={"dependsOn": []})), {"dependency-direction"}),
    ("a progress strip with the right numbers and the wrong current milestone", "milestone-current", "report-ms", dict(data=ms_wrong_current()), set()),
    ("a sentence per item in the reply, with an abbreviation in the middle", "reply-hands-over", "report",
     dict(reply1=GOOD_REPLY + " Postgres vs. SQLite is open. The SSO vendor, e.g. Okta, is open. The licence audit is open. Retention is open. The migration waits."), set()),
    ("the report cleaned in the turn that made it", "created-by-script", "report", dict(calls1=EARLY_CLEAN, report_patch=GONE), NO_REPORT - {"created-by-script", "report-outside-project", "reply-hands-over"}),
    ("report.sh new, then a directory made inside `bash -lc '...'` (combined short flags)", "created-by-script", "report",
     dict(calls1=REPORT_CALLS + [("Bash", {"command": "bash -lc 'mkdir -p /r/tmp/x'"}, "")]), set()),
    ("report.sh new, then a removal inside `bash -ec '...'` in turn 2 instead of clean", "cleaned-by-script", "report-clean",
     dict(calls2=[("Bash", {"command": f"bash -ec 'rm -rf {DIR}'"}, "")]), set()),
    ("a refused clean followed by rm on a sitrep.* glob", "cleaned-by-script", "report-clean", dict(calls2=REFUSED_CLEAN), set()),
    ("a clean call the script refused", "cleaned-by-script", "report-clean", dict(calls2=REFUSED_CLEAN[:1]), set()),
    ("the compound `clean || /bin/rm -rf`", "cleaned-by-script", "report-clean", dict(calls2=[("Bash", {"command": f"{SCRIPT_Q} clean {DIR} || /bin/rm -rf {DIR}"}, "")]), set()),
    ("find -delete instead of clean", "cleaned-by-script", "report-clean", dict(calls2=[("Bash", {"command": f"find {DIR} -delete"}, "")]), set()),
    ("the retention item dropped, the word kept in another item", "items-cover-planted", "report",
     dict(data=with_items(retention=None, sso={"detail": "Choose between the two shortlisted vendors. Retention is also unclear."})), set()),
    ("a data.js that starts with an import", "data-parses", "report", dict(data="import x from 'y';\n" + helpers.data_js(helpers.good_data())), UNPARSED),
    ("a report page written into the workspace by a negative case", "no-report-when-negative", "none", dict(after1_extra={"open-questions.html": "<html></html>"}), set()),
    ("a data.js written into the workspace by a negative case", "no-report-when-negative", "none", dict(after1_extra={"docs/data.js": "window.SITREP = {};"}), set()),
    ("a hand-made report.sh at an installed absolute path", "skill-loaded", "report",
     dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": "cd /x && '/home/u/.claude/plugins/sitrep/skills/sitrep/scripts/report.sh' new"}, DIR + "\n"), REPORT_CALLS[2]]), set()),
    ("two plugins named sitrep", "skill-loaded", "report", dict(plugins=("sitrep", "sitrep")), set()),
    ("the sitrep plugin loaded from another path", "skill-loaded", "report", dict(plugin_path="/installed/sitrep"), set()),
    ("a compound exploration command denied (dontAsk text in the tool_result) and report.sh new never tried", "not-permission-blocked", "report-ms", STOPPED, STOPPED_CASCADE),
    ("the same denial recorded only in result.permission_denials, with a command that reads as a success", "not-permission-blocked", "report-ms",
     dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": EXPLORE}, "")], denials=EXPLORE_DENIAL, report_found=False, tmp_sitrep=[]), STOPPED_CASCADE),
    ("report.sh run from outside the plugin under test", "skill-loaded", "report",
     dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": '"/installed/sitrep/skills/sitrep/scripts/report.sh" new'}, DIR + "\n"), REPORT_CALLS[2]]), set()),
]
ALL_EXPECTS = ("none", "feedback", "report", "report-clean", "report-ms")


def run(expect, **override):
    row, events, states = helpers.good(expect, **override)
    return [(cid, ok, note) for cid, ok, note in checks.run_checks(checks.Case.build(row, events, states))]


def failed(expect, **override):
    return {cid for cid, ok, _ in run(expect, **override) if not ok}


def passes(cid, expect="report", **override):
    return {c: ok for c, ok, _ in run(expect, **override)}[cid]


def judge(expect, **override):
    """The verdict of a trial: checks.verdict needs the case as well as the results (the damage is scoped by turn)."""
    row, events, states = helpers.good(expect, **override)
    case = checks.Case.build(row, events, states)
    return checks.verdict(case, checks.run_checks(case))


def judge_case(case):
    return checks.verdict(case, checks.run_checks(case))


class GoodRuns(unittest.TestCase):
    def test_every_good_run_passes_every_check(self):
        for expect in ALL_EXPECTS:
            for cid, ok, note in run(expect):
                self.assertTrue(ok, f"{expect}: {cid}: {note}")

    def test_every_check_runs_for_some_expect(self):
        self.assertEqual({f.__name__.replace("_", "-") for fs in checks.BY_EXPECT.values() for f in fs}, set(checks.GROUPS))

    def test_the_checks_that_run_are_known(self):
        for expect in ALL_EXPECTS:
            self.assertLessEqual({cid for cid, _, _ in run(expect)}, set(checks.GROUPS))

    def test_the_milestone_checks_run_only_for_the_milestone_case(self):
        ids = {e: {cid for cid, _, _ in run(e)} for e in ALL_EXPECTS}
        for cid in ("milestone-item", "milestone-strip", "tracker-untouched"):
            self.assertEqual([e for e in ALL_EXPECTS if cid in ids[e]], ["report-ms"])
        self.assertEqual([e for e in ALL_EXPECTS if "report-cleaned" in ids[e]], ["report-clean"])


class Mutations(unittest.TestCase):
    def test_every_check_is_mutated(self):
        self.assertEqual(set(MUTATIONS), set(checks.GROUPS))

    def test_each_mutation_fails_its_check_and_exactly_the_written_cascade(self):
        for cid, (expect, override, cascade) in MUTATIONS.items():
            with self.subTest(check=cid):
                self.assertEqual(failed(expect, **override), {cid} | cascade)

    def test_every_further_failing_run_fails_its_check_and_exactly_the_written_cascade(self):
        for label, cid, expect, override, cascade in MORE_MUTATIONS:
            with self.subTest(mutation=label):
                self.assertEqual(failed(expect, **override), {cid} | cascade)

    def test_the_further_runs_name_known_checks(self):
        self.assertLessEqual({cid for _, cid, *_ in MORE_MUTATIONS}, set(checks.GROUPS))


class Verdict(unittest.TestCase):
    def verdict(self, expect, **override):
        return judge(expect, **override)

    def test_pass_fail_and_inconclusive(self):
        self.assertEqual(self.verdict("report"), "PASS")
        self.assertEqual(self.verdict("report", data=with_bad_values()), "FAIL")
        mutation = MUTATIONS["not-permission-blocked"]
        self.assertEqual(self.verdict(mutation[0], **mutation[1]), "INCONCLUSIVE")

    def test_a_missing_report_is_inconclusive_and_still_lists_the_failed_gate_checks(self):
        expect, override, _ = MUTATIONS["report-located"]
        results = run(expect, **override)
        self.assertEqual(judge(expect, **override), "INCONCLUSIVE")
        self.assertIn("only-data-added", {cid for cid, ok, _ in results if not ok})

    def test_a_write_into_the_project_beats_a_failed_precondition(self):
        blocked = dict(denials=[{"tool_name": "Bash", "tool_input": {"command": NEW}}], after1_extra={"sitrep.html": "x"})
        self.assertEqual(self.verdict("report", **blocked), "FAIL")

    def test_a_skill_that_did_not_trigger_beats_a_failed_precondition(self):
        self.assertEqual(self.verdict("report", calls1=[], report_found=False), "FAIL")

    def test_a_plugin_that_did_not_load_is_inconclusive_not_a_failure_of_the_skill(self):
        self.assertEqual(self.verdict("report", skills=(), calls1=[], report_found=False), "INCONCLUSIVE")
        self.assertEqual(self.verdict("none", plugins=(), skills=()), "INCONCLUSIVE")

    def test_a_write_into_the_project_is_a_failure_even_when_the_plugin_did_not_load(self):
        self.assertEqual(self.verdict("feedback", skills=(), after1_extra={"notes.md": "x"}), "FAIL")

    def test_groups(self):
        self.assertEqual(sorted(cid for cid, g in checks.GROUPS.items() if g == "precondition"), ["not-permission-blocked", "report-located", "run-completed", "state-recorded"])
        self.assertEqual(set(checks.GROUPS.values()), {"precondition", "process", "gate", "outcome", "soft"})

    def test_the_advisory_checks_are_the_ones_that_only_match_words_in_text_the_model_wrote(self):
        self.assertEqual(sorted(cid for cid, g in checks.GROUPS.items() if g == "soft"),
                         ["dependency-direction", "items-cover-planted", "milestone-current", "reply-hands-over"])
        self.assertEqual(checks.SOFT, {cid for cid, g in checks.GROUPS.items() if g == "soft"})


class SoftChecks(unittest.TestCase):
    """The advisory group: computed, listed, counted as WARN, never part of the verdict."""

    def warnings(self, expect, **override):
        row, events, states = helpers.good(expect, **override)
        case = checks.Case.build(row, events, states)
        return checks.warnings(checks.run_checks(case)), judge_case(case)

    def test_a_trial_that_fails_only_advisory_checks_is_a_pass_with_warnings(self):
        for cid in sorted(checks.SOFT):
            expect, override, cascade = MUTATIONS[cid]
            with self.subTest(check=cid):
                self.assertLessEqual(cascade, checks.SOFT)
                warned, verdict = self.warnings(expect, **override)
                self.assertEqual((sorted(warned), verdict), (sorted({cid} | cascade), "PASS"))

    def test_every_hard_check_still_decides(self):
        for cid, (expect, override, cascade) in MUTATIONS.items():
            if cid not in checks.SOFT:
                with self.subTest(check=cid):
                    self.assertNotEqual(judge(expect, **override), "PASS")

    def test_every_advisory_check_has_a_failing_run_and_a_passing_one(self):
        for cid in checks.SOFT:
            expect, override, _ = MUTATIONS[cid]
            self.assertFalse(passes(cid, expect=expect, **override), cid)
            self.assertTrue(passes(cid, expect=expect), cid)

    def test_all_the_advisory_checks_failing_together_is_still_a_pass(self):
        data = helpers.good_data(ms=True)
        data["items"] = data["items"][:2] + [data["items"][-1]]
        for entry in data["items"]:
            entry["dependsOn"] = ["sso"] if entry["id"] == "db" else []
        data["milestones"]["current"] = "Nothing"
        warned, verdict = self.warnings("report-ms", data=data, reply1=LISTED_REPLY + f"\n{DIR} Copy JSON")
        self.assertEqual((sorted(warned), verdict), (sorted(checks.SOFT), "PASS"))

    def test_the_other_failures_of_the_same_trial_still_decide(self):
        warned, verdict = self.warnings("report", reply1=LISTED_REPLY, after1_extra={"notes.md": "x"})
        self.assertEqual((warned, verdict), (["reply-hands-over"], "FAIL"))
        warned, verdict = self.warnings("report", reply1=LISTED_REPLY, data=without_deps())
        self.assertEqual(verdict, "FAIL")
        self.assertIn("reply-hands-over", warned)

    def test_a_warning_never_turns_an_inconclusive_trial_into_a_failure_or_a_pass(self):
        warned, verdict = self.warnings("report", reply1=LISTED_REPLY, result_subtype="error_max_budget_usd")
        self.assertEqual((warned, verdict), (["reply-hands-over"], "INCONCLUSIVE"))


class ScopedVerdict(unittest.TestCase):
    """A precondition explains only the failures of the turn it damaged (round 3, finding I1)."""

    CLEAN_DENIAL = [{"tool_name": "Bash", "tool_input": {"command": CLEAN}}]
    CRASH = dict(result2_subtype="error_during_execution")

    def test_turn_one_that_finished_keeps_its_findings_when_turn_two_crashed(self):
        self.assertEqual(judge("report-clean", data=without_deps(), **self.CRASH), "FAIL")
        self.assertEqual(judge("report-clean", reply1=LISTED_REPLY, data=without_deps(), **self.CRASH), "FAIL")
        self.assertEqual(judge("report-clean", data=with_bad_values(), **self.CRASH), "FAIL")
        self.assertEqual(judge("report-clean", index="<html>edited</html>\n", **self.CRASH), "FAIL")

    def test_turn_one_that_finished_keeps_its_findings_when_turn_two_never_ran(self):
        self.assertEqual(judge("report-clean", data=without_deps(), turn2=False), "FAIL")
        self.assertEqual(judge("report-clean", reply1=LISTED_REPLY, data=without_deps(), turn2=False), "FAIL")

    def test_turn_one_that_finished_keeps_its_findings_when_turn_two_was_denied(self):
        self.assertEqual(judge("report-clean", data=without_deps(), denials2=self.CLEAN_DENIAL), "FAIL")
        self.assertEqual(judge("report-clean", data=without_deps(), denials2=self.CLEAN_DENIAL, calls2=[("Bash", {"command": CLEAN}, "")]), "FAIL")

    def test_a_turn_two_that_failed_alone_is_inconclusive_and_explains_the_cleanup_checks(self):
        self.assertEqual(judge("report-clean", **self.CRASH), "INCONCLUSIVE")
        self.assertEqual(judge("report-clean", turn2=False), "INCONCLUSIVE")
        self.assertEqual(judge("report-clean", denials2=self.CLEAN_DENIAL), "INCONCLUSIVE")
        self.assertEqual(judge("report-clean", calls2=[], **self.CRASH), "INCONCLUSIVE")
        self.assertEqual(judge("report-clean", calls2=[], report2_exists=True, **self.CRASH), "INCONCLUSIVE")

    def test_the_cleanup_checks_are_not_explained_by_a_turn_two_that_ended_well(self):
        self.assertEqual(judge("report-clean", calls2=[]), "FAIL")
        self.assertEqual(judge("report-clean", report2_exists=True), "FAIL")

    def test_a_turn_one_that_failed_explains_everything_after_it(self):
        self.assertEqual(judge("report-clean", result_subtype="error_max_budget_usd", data=without_deps(), turn2=False), "INCONCLUSIVE")
        self.assertEqual(judge("report-clean", denials=WRITE_DENIAL, data=without_deps(), calls2=[]), "INCONCLUSIVE")

    def test_the_runner_note_belongs_to_the_turn_that_failed(self):
        row, events, states = helpers.good("report-clean", turn2=False, data=without_deps())
        note = "turn 1 printed no session id, so the second turn cannot resume it"
        problems = checks.turn_problems(checks.Case.build(row, events, states, note))
        self.assertEqual((problems[1], len(problems[2]) > 0), ([], True))
        self.assertEqual(judge_case(checks.Case.build(row, events, states, note)), "FAIL")
        row, events, states = helpers.good("none", data=None)
        self.assertEqual(len(checks.turn_problems(checks.Case.build(row, events, states, "agent command exited 3"))[1]), 1)

    def test_a_skill_call_in_a_negative_case_stands_although_the_run_crashed(self):
        override = dict(calls1=[("Skill", {"skill": SKILL})], tmp_sitrep=["sitrep.x1y2z3"], result_subtype="error_max_budget_usd")
        results = {c: ok for c, ok, _ in run("none", **override)}
        self.assertEqual([c for c, ok in results.items() if not ok], ["run-completed", "skill-trigger", "no-report-when-negative"])
        self.assertEqual(judge("none", **override), "FAIL")
        self.assertEqual(judge("none", calls1=[("Skill", {"skill": SKILL})], result_subtype="error_max_budget_usd"), "FAIL")
        self.assertEqual(judge("none", tmp_sitrep=["sitrep.x1y2z3"], result_subtype="error_max_budget_usd"), "FAIL")

    def test_a_negative_case_that_made_no_report_and_crashed_is_inconclusive(self):
        self.assertEqual(judge("none", result_subtype="error_max_budget_usd"), "INCONCLUSIVE")
        self.assertEqual(judge("none", plugins=(), skills=(), calls1=[("Skill", {"skill": SKILL})], tmp_sitrep=["sitrep.x"]), "INCONCLUSIVE")

    def test_a_skill_that_did_not_trigger_stands_only_when_turn_one_finished(self):
        self.assertEqual(judge("report", calls1=[], report_found=False, denials=WRITE_DENIAL), "FAIL")
        self.assertEqual(judge("report", calls1=[], report_found=False, result_subtype="error_max_budget_usd"), "INCONCLUSIVE")

    def test_a_report_made_by_hand_after_report_sh_failed_is_inconclusive_by_design(self):
        """`report.sh new` that fails may be the environment (an unsubstituted ${CLAUDE_SKILL_DIR}, README item 6): the hand-made report is graded
        INCONCLUSIVE, not FAIL, although created-by-script fails and lists the mkdir."""
        calls = [("Skill", {"skill": SKILL}), ("Bash", {"command": NEW}, "No such file or directory"), ("Bash", {"command": "mkdir -p /r/tmp/sitrep-report"}, ""),
                 ("Write", {"file_path": "/r/tmp/sitrep-report/index.html"}), ("Write", {"file_path": "/r/tmp/sitrep-report/data.js"})]
        override = dict(calls1=calls, report_found=False, tmp_sitrep=[])
        results = {c: (ok, note) for c, ok, note in run("report", **override)}
        self.assertFalse(results["created-by-script"][0])
        self.assertIn("mkdir -p /r/tmp/sitrep-report", results["created-by-script"][1])
        self.assertFalse(results["report-located"][0])
        self.assertEqual(judge("report", **override), "INCONCLUSIVE")

    def test_a_report_made_by_hand_next_to_one_that_report_sh_made_is_a_failure(self):
        calls = REPORT_CALLS + [("Bash", {"command": "mkdir -p /r/tmp/other"}, "")]
        self.assertEqual(judge("report", calls1=calls), "FAIL")
        self.assertEqual(judge("report-clean", calls1=calls, **self.CRASH), "FAIL")


class Process(unittest.TestCase):
    def test_a_plugin_that_loaded_without_its_skill_listed_is_not_loaded(self):
        self.assertFalse(passes("skill-loaded", skills=("update-config",)))

    def test_a_skill_listed_without_the_plugin_is_not_loaded(self):
        self.assertFalse(passes("skill-loaded", plugins=()))

    def test_a_trace_without_init_is_not_loaded(self):
        row, events, states = helpers.good("none")
        events = [e for e in events if e.get("subtype") != "init"]
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["skill-loaded"])

    def test_a_negative_without_the_skill_passes_and_a_positive_without_it_fails(self):
        self.assertTrue(passes("skill-trigger", expect="none", should_trigger=False))
        self.assertFalse(passes("skill-trigger", calls1=[("Read", {"file_path": "x"})]))

    def test_the_report_is_made_by_the_script(self):
        self.assertTrue(passes("created-by-script", calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": "report.sh new"}, DIR)]))
        self.assertFalse(passes("created-by-script", calls1=[("Skill", {"skill": SKILL})]))
        self.assertFalse(passes("created-by-script", calls1=REPORT_CALLS + [("Bash", {"command": "mktemp -d"}, "x")]))

    def test_script_calls_in_the_spelling_skill_md_gives(self):
        for command in (NEW, f"{SCRIPT_Q} new", "report.sh new", "'/x/report.sh' new"):
            self.assertTrue(checks.state.REPORT_NEW_RE.search(command), command)
        for command in (CLEAN, f"{SCRIPT_Q} clean /x", "report.sh clean /x"):
            self.assertTrue(checks.state.REPORT_CLEAN_RE.search(command), command)
        self.assertIsNone(checks.state.REPORT_NEW_RE.search(CLEAN))
        self.assertIsNone(checks.state.REPORT_CLEAN_RE.search(NEW))
        self.assertIsNone(checks.state.REPORT_NEW_RE.search("cat report.sh"))

    def test_removals_are_read_from_the_parsed_command_not_from_its_text(self):
        for command in (f"rm -rf {DIR}", f"cd /x && rm -r {DIR}", f"rmdir {DIR}", f"unlink {DIR}", f"{CLEAN} || /bin/rm -rf {DIR}", f"/usr/bin/env rm -r {DIR}",
                        f"find {DIR} -delete", f"find {DIR} -exec rm -rf {{}} +", f"python3 -c 'import shutil; shutil.rmtree(\"{DIR}\")'", f"true;rm {DIR}",
                        f"bash -c 'rm -r {DIR}'", f"ls {DIR}\nrm {DIR}", f"find /r/tmp -name 'sitrep.*' | xargs rm -rf"):
            self.assertTrue(checks.removals(command), command)
        for command in (CLEAN, "cat /home/form /x", "report.sh clean /x/sitrep.a", "ls /x/perm ", "cat /x/Makefile.rm.bak ", "find /x -name '*.js'",
                        'echo "rm -rf /x/sitrep.a"', "grep rm notes.md", "git rm tracked.txt", f"cat {DIR}/data.js", "echo rmtree", "python3 -c 'print(1)'", "ls $TMPDIR/sitrep.*"):
            self.assertEqual(checks.removals(command), [], command)

    def test_a_removal_counts_only_where_it_names_the_report_or_a_sitrep_path(self):
        names = lambda command: [c.name for c, text in checks.removals(command) if "Ab12Cd" in text or "sitrep" in text]
        self.assertEqual(names(f"rm -f /r/ws/notes.txt && {CLEAN}"), [])
        self.assertEqual(names(f"rm -rf {DIR}"), ["rm"])
        self.assertEqual(names("find /r/tmp -name 'sitrep.*' | xargs rm -rf"), ["rm"])

    def test_directories_made_by_hand_are_read_from_the_parsed_command(self):
        for command in ("mkdir -p /r/tmp/x", "cd /r && mkdir x", "TMP=$(mktemp -d)", "bash -c 'mkdir x'"):
            self.assertTrue(checks.creates_directories(command), command)
        for command in ("grep mkdir notes.md", 'echo "mktemp -d"', "cat mkdir.txt", "ls /r/mkdir", NEW):
            self.assertEqual(checks.creates_directories(command), [], command)

    def test_report_sh_is_called_only_where_it_is_the_command(self):
        for command, verbs in ((NEW, ["new"]), (CLEAN, ["clean"]), (f"cd /x && {NEW}", ["new"]), (f"bash {SCRIPT} new", ["new"]), ("report.sh", [""]),
                               (f"{NEW} && {CLEAN}", ["new", "clean"]), ('"${CLAUDE_SKILL_DIR}/scripts/report.sh" new', ["new"])):
            self.assertEqual([verb for _, verb, _ in checks.script_calls(command)], verbs, command)
        for command in (f"cat {SCRIPT}", f"grep new {SCRIPT}", f"echo '{NEW}'", "ls scripts/report.sh.bak", f"less {SCRIPT} new"):
            self.assertEqual(checks.script_calls(command), [], command)

    def test_ordinary_read_only_commands_flag_nothing(self):
        reads = [("Bash", {"command": c}, "") for c in (f"cat {SCRIPT}", "grep -rn mkdir /r/ws", f"ls {DIR}", f"cat {DIR}/data.js", "git status", "git rm --cached x", 'echo "rm -rf"')]
        self.assertEqual(judge("report", calls1=REPORT_CALLS + reads), "PASS")
        self.assertEqual(judge("report-clean", calls1=REPORT_CALLS + reads, calls2=reads + CLEAN_CALLS), "PASS")
        self.assertTrue(passes("no-report-when-negative", expect="none", calls1=reads))

    def test_clean_by_the_script_names_the_recorded_directory(self):
        other = [("Bash", {"command": f'"{helpers.SCRIPT}" clean /r/tmp/sitrep.Other1'}, "")]
        self.assertFalse(passes("cleaned-by-script", expect="report-clean", calls2=other))
        self.assertFalse(passes("cleaned-by-script", expect="report-clean", calls2=[]))
        both = [("Bash", {"command": CLEAN}, ""), ("Bash", {"command": f"rm -rf {DIR}"}, "")]
        self.assertFalse(passes("cleaned-by-script", expect="report-clean", calls2=both))

    def test_clean_needs_a_second_turn(self):
        row, events, states = helpers.good("report-clean")
        events = events[:events.index(next(e for e in events if e.get("type") == "result")) + 1]
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["cleaned-by-script"])


class Precondition(unittest.TestCase):
    def test_the_denied_browser_open_is_expected(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": f'open "{DIR}/index.html"'}}
        self.assertTrue(passes("not-permission-blocked", denials=[denial]))

    def test_denials_of_report_sh_data_js_and_of_unknown_shape_block(self):
        for denial in ({"tool_name": "Bash", "tool_input": {"command": NEW}}, {"tool_name": "Edit", "tool_input": {"file_path": DIR + "/data.js"}},
                       {"detail": f"denied {NEW}"}):
            self.assertFalse(passes("not-permission-blocked", denials=[denial]), denial)

    def test_a_denial_of_another_tool_does_not_block(self):
        self.assertTrue(passes("not-permission-blocked", denials=[{"tool_name": "WebFetch", "tool_input": {"url": "https://x/report.sh"}}]))

    def test_a_denial_in_the_first_turn_of_two_is_seen(self):
        self.assertFalse(passes("not-permission-blocked", expect="report-clean", denials=[{"tool_name": "Write", "tool_input": {"file_path": DIR + "/data.js"}}]))

    def test_an_edit_result_that_echoes_item_text_is_not_a_refusal(self):
        echo = ("The file /r/tmp/sitrep.Ab12Cd/data.js has been updated. Here's the result of running `cat -n` on a snippet of the edited file:\n"
                "    12\t      detail: 'Waiting for permission from legal; the signing key was not granted yet.'")
        edit = ("Edit", {"file_path": DIR + "/data.js", "old_string": "a", "new_string": "b"}, echo)
        self.assertTrue(passes("not-permission-blocked", calls1=REPORT_CALLS + [edit]))
        created = ("Write", {"file_path": DIR + "/data.js", "content": "x"}, "File created successfully at: " + DIR + "/data.js (permission denied is just a word here)")
        self.assertTrue(passes("not-permission-blocked", calls1=REPORT_CALLS[:2] + [created]))

    def test_a_short_refusal_of_a_write_or_edit_still_blocks(self):
        for text in ("Permission to use Write has been denied.", "Claude requested permissions to write to data.js, but you haven't granted it yet.", "Error: permission denied"):
            for name in ("Write", "Edit"):
                call = (name, {"file_path": DIR + "/data.js"}, text)
                self.assertFalse(passes("not-permission-blocked", calls1=REPORT_CALLS[:2] + [call], omit_data=True), (name, text))

    def test_the_report_must_exist(self):
        self.assertFalse(passes("report-located", report_patch={"exists": False}))


class DeniedExploration(unittest.TestCase):
    """The first real with-milestones run: the harness's own grants denied the agent's exploration, so report.sh new was never exercised."""

    def stopped(self, **override):
        return helpers.good("report-ms", **{**STOPPED, **override})

    def case(self, **override):
        row, events, states = self.stopped(**override)
        return checks.Case.build(row, events, states)

    def test_the_real_shape_is_inconclusive_not_a_failure_and_names_the_denied_command(self):
        case = self.case()
        results = checks.run_checks(case)
        self.assertEqual(checks.verdict(case, results), "INCONCLUSIVE")
        note = next(n for cid, ok, n in results if cid == "not-permission-blocked")
        self.assertIn("git log --oneline", note)
        self.assertIn("never tried report.sh new", note)
        # the skill loaded and was triggered: those checks stand, and they pass
        self.assertEqual({cid for cid, ok, _ in results if cid in ("skill-loaded", "skill-trigger") and not ok}, set())

    def test_every_channel_alone_gives_the_same_verdict(self):
        by_result = dict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": EXPLORE}, "")], denials=EXPLORE_DENIAL)
        for label, override in (("tool_result text", {}), ("result.permission_denials", by_result)):
            with self.subTest(channel=label):
                self.assertEqual(checks.verdict(self.case(**override), checks.run_checks(self.case(**override))), "INCONCLUSIVE")
        # a system permission_denied event (the real one has tool_name and tool_use_id, no tool_input)
        row, events, states = self.stopped(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": EXPLORE}, "")])
        call_id = next(i for i, name, _ in checks.hook_trace.tool_uses(events) if name == "Bash")
        events.insert(-1, {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": call_id, "message": DONTASK})
        case = checks.Case.build(row, events, states)
        self.assertEqual(checks.verdict(case, checks.run_checks(case)), "INCONCLUSIVE")
        self.assertEqual([c for _, c in checks.denied_bash(case.turns[0])], [EXPLORE])

    def test_one_denied_call_seen_through_all_three_channels_counts_once(self):
        row, events, states = self.stopped()
        call_id = next(i for i, name, _ in checks.hook_trace.tool_uses(events) if name == "Bash")
        events.insert(-1, {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": call_id, "message": DONTASK})
        events[-1]["permission_denials"] = [{"tool_name": "Bash", "tool_use_id": call_id, "tool_input": {"command": EXPLORE}}]
        self.assertEqual(checks.denied_bash(checks.Case.build(row, events, states).turns[0]), [(call_id, EXPLORE)])

    def test_a_denied_unrelated_call_followed_by_a_report_sh_new_that_ran_stays_a_pass(self):
        denied = ("Bash", {"command": EXPLORE}, DONTASK)
        calls = [REPORT_CALLS[0], denied] + REPORT_CALLS[1:]
        self.assertEqual(judge("report", calls1=calls, denials=EXPLORE_DENIAL), "PASS")
        self.assertTrue(passes("not-permission-blocked", calls1=calls, denials=EXPLORE_DENIAL))

    def test_a_denied_report_sh_new_is_still_inconclusive_by_the_old_rule(self):
        calls = [REPORT_CALLS[0], ("Bash", {"command": NEW}, DONTASK)]
        kwargs = dict(calls1=calls, denials=[{"tool_name": "Bash", "tool_input": {"command": NEW}}], report_found=False, tmp_sitrep=[])
        self.assertFalse(passes("not-permission-blocked", **kwargs))
        self.assertEqual(judge("report", **kwargs), "INCONCLUSIVE")

    def test_a_successful_bash_result_that_mentions_the_text_is_not_a_denial(self):
        calls = [REPORT_CALLS[0], ("Bash", {"command": "cat notes.md"}, "Permission to use Bash has been denied (a quoted note)")] + REPORT_CALLS[1:]
        row, events, states = helpers.good("report", calls1=calls)
        # the same text on a call that says is_error: false is not read as a denial
        for event in events:
            for block in event.get("message", {}).get("content", []) if event.get("type") == "user" else []:
                block["is_error"] = False
        self.assertEqual(checks.denied_bash(checks.Case.build(row, events, states).turns[0]), [])

    def test_a_skill_that_never_ran_report_sh_without_any_denial_is_still_a_failure(self):
        calls = [("Skill", {"skill": SKILL}), ("Read", {"file_path": "/r/ws/docs/open-questions.md"})]
        self.assertEqual(judge("report", calls1=calls, report_found=False, tmp_sitrep=[]), "FAIL")

    def test_a_denial_in_turn_two_only_does_not_damage_turn_one(self):
        # turn 1 made the report; an unrelated Bash denial in the cleanup turn is not an exploration stop (turn 1 ran new)
        calls2 = [("Bash", {"command": "git log | head"}, DONTASK)] + CLEAN_CALLS
        self.assertEqual(judge("report-clean", calls2=calls2), "PASS")


class Gates(unittest.TestCase):
    def test_a_report_inside_the_workspace_or_named_otherwise_fails(self):
        self.assertFalse(passes("report-outside-project", report_dir=WS + "/sitrep.Ab12Cd"))
        self.assertFalse(passes("report-outside-project", report_dir=WS + "/sub/../sitrep.Ab12Cd/"))
        self.assertFalse(passes("report-outside-project", report_dir="/r/tmp/report"))
        self.assertTrue(passes("report-outside-project", report_dir="/r/wsx/sitrep.Ab12Cd"))

    def test_project_changes_in_turn_two_and_in_git_are_seen(self):
        row, events, states = helpers.good("report-clean")
        states["after2"] = helpers.state.snapshot_from({**helpers.fixtures.build("open-items"), "x.md": "y"}, head="h1")
        self.assertFalse(checks.project_untouched(checks.Case.build(row, events, states))[1])
        row, events, states = helpers.good("report-clean")
        states["after2"] = helpers.state.snapshot_from(helpers.fixtures.build("open-items"), head="h2")
        self.assertFalse(checks.project_untouched(checks.Case.build(row, events, states))[1])
        row, events, states = helpers.good("report")
        states["after1"] = helpers.state.snapshot_from(helpers.fixtures.build("open-items"), head="h1", status=" M README.md\n")
        self.assertFalse(checks.project_untouched(checks.Case.build(row, events, states))[1])

    def test_the_claude_marker_in_git_status_is_not_a_change(self):
        row, events, states = helpers.good("report")
        states["after1"] = helpers.state.snapshot_from(helpers.fixtures.build("open-items"), head="h1", status="?? .claude/\n")
        self.assertTrue(checks.project_untouched(checks.Case.build(row, events, states))[1])

    def test_a_two_turn_case_without_the_second_snapshot_is_not_a_write_into_the_project(self):
        row, events, states = helpers.good("report-clean")
        states["after2"] = None
        case = checks.Case.build(row, events, states)
        self.assertTrue(checks.project_untouched(case)[1])
        self.assertIn("did not run", checks.project_untouched(case)[2])
        self.assertFalse(checks.run_completed(case)[1])
        states["after1"] = helpers.state.snapshot_from({**helpers.fixtures.build("open-items"), "x.md": "y"}, head="h1")
        self.assertFalse(checks.project_untouched(checks.Case.build(row, events, states))[1])

    def test_a_missing_snapshot_before_or_after_turn_one_fails(self):
        for key in ("before", "after1"):
            row, events, states = helpers.good("report")
            states[key] = None
            self.assertFalse(checks.project_untouched(checks.Case.build(row, events, states))[1], key)

    def test_an_edit_to_an_existing_file_is_seen(self):
        self.assertFalse(passes("project-untouched", after1_extra={"README.md": "changed\n"}))

    def test_missing_report_files_fail_the_report_gates(self):
        self.assertFalse(passes("index-untouched", report_patch={"files": {"data.js": "x"}}))
        self.assertFalse(passes("only-data-added", report_patch={"files": {"index.html": "x"}}))

    def test_a_tracker_that_is_gone_or_never_snapshotted_fails(self):
        row, events, states = helpers.good("report-ms")
        del states["after1"]["files"][TRACKER]
        self.assertFalse(checks.tracker_untouched(checks.Case.build(row, events, states))[1])
        row, events, states = helpers.good("report-ms")
        states["after1"] = None
        self.assertFalse(checks.tracker_untouched(checks.Case.build(row, events, states))[1])

    def test_a_negative_run_that_ran_the_script_fails_even_when_the_directory_is_elsewhere(self):
        self.assertFalse(passes("no-report-when-negative", expect="none", calls1=[("Bash", {"command": NEW}, "/elsewhere/sitrep.x1y2z3")]))

    def test_a_negative_run_without_a_listing_fails(self):
        row, events, states = helpers.good("none")
        del states["tmp_sitrep"]
        self.assertFalse(checks.no_report_when_negative(checks.Case.build(row, events, states))[1])


class Outcome(unittest.TestCase):
    def test_the_assignment_may_be_spelled_loosely(self):
        for text in ("window.SITREP={};", "window . SITREP = {};", "\nwindow.SITREP = {};"):
            self.assertTrue(passes("data-defines-sitrep", report_patch={"data_text": text}), text)
        for text in ("window.SITREP_OLD = {};", "var SITREP = {};", "window.sitrep = {};", ""):
            self.assertFalse(passes("data-defines-sitrep", report_patch={"data_text": text}), text)

    def test_data_that_is_not_an_object_does_not_parse(self):
        self.assertFalse(passes("data-parses", data="window.SITREP = [1, 2];\n"))

    def test_the_unreadable_javascript_note_names_the_reason(self):
        results = {c: note for c, _, note in run("report", data='window.SITREP = {title: "a" + "b", items: []};')}
        self.assertIn("unsupported JavaScript", results["data-parses"])

    def test_milestone_ids_and_values(self):
        data = helpers.good_data(ms=True)
        data["items"][-1]["urgency"] = 2
        self.assertFalse(passes("milestone-item", expect="report-ms", data=data))
        data = helpers.good_data(ms=True)
        data["items"][-1]["type"] = "decision"
        self.assertFalse(passes("milestone-item", expect="report-ms", data=data))

    def test_the_current_milestone_is_matched_loosely(self):
        data = helpers.good_data(ms=True)
        data["milestones"]["current"] = "#2 Storage layer"
        self.assertTrue(passes("milestone-strip", expect="report-ms", data=data))
        data["milestones"]["total"] = 3
        self.assertFalse(passes("milestone-strip", expect="report-ms", data=data))

    def test_a_dependency_on_itself_does_not_count(self):
        data = helpers.good_data()
        for entry in data["items"]:
            entry["dependsOn"] = []
        data["items"][0].update(detail="Postgres migration is the question.", dependsOn=["db"])
        self.assertFalse(passes("dependency-recorded", data=data))

    def test_a_start_here_card_needs_a_title(self):
        self.assertFalse(passes("start-here-set", data=data_with(next={"title": " ", "why": "x"})))
        self.assertFalse(passes("start-here-set", data=data_with(next="Decide on the database")))

    def test_no_items_cover_nothing(self):
        self.assertFalse(passes("items-cover-planted", data=data_with(items=[])))

    def test_every_planted_word_matters(self):
        for key in helpers.fixtures.PLANTED:
            data = helpers.good_data()
            wanted = helpers.fixtures.PLANTED[key]
            data["items"] = [i for i in data["items"] if not re.search(wanted, f"{i['title']} {i['detail']}".lower())]
            self.assertFalse(passes("items-cover-planted", data=data), key)


class Reply(unittest.TestCase):
    def test_what_a_hand_over_needs(self):
        self.assertTrue(passes("reply-hands-over"))
        no_path = GOOD_REPLY.replace(DIR, "somewhere").replace("open", "launch")
        no_copy = GOOD_REPLY.replace("Copy JSON", "the button")
        long = GOOD_REPLY + "\n" + "\n".join(f"- item {n}" for n in range(5))
        for text in (no_path, no_copy, long, LISTED_REPLY, ""):
            self.assertFalse(passes("reply-hands-over", reply1=text), text[:40])

    def test_an_open_line_without_the_directory_is_not_a_hand_over_and_four_list_lines_are_fine(self):
        text = '! open "/somewhere/index.html"\n\nPress Copy JSON.\n' + "\n".join(f"- {n}" for n in range(4))
        self.assertFalse(passes("reply-hands-over", reply1=text))
        self.assertTrue(passes("reply-hands-over", reply1=text.replace("/somewhere", DIR)))

    def test_up_to_four_units_naming_a_topic_are_fine_five_are_the_list(self):
        self.assertTrue(passes("reply-hands-over", reply1=GOOD_REPLY + " The SSO vendor and the data retention question are open."))
        self.assertTrue(passes("reply-hands-over", reply1=GOOD_REPLY + " SSO is open. The licence audit is open. Data retention is open. The migration waits."))
        self.assertFalse(passes("reply-hands-over", reply1=GOOD_REPLY + " Postgres is open. SSO is open. The licence audit is open. Data retention is open. The migration waits."))

    def test_a_three_line_spoken_summary_that_names_a_topic_on_each_line_is_a_hand_over(self):
        reply = (f"Report: {DIR}/index.html\n5 open items; the most pressing is the database decision (Postgres or SQLite).\n"
                 "It blocks the schema migration, so start there.\nThe SSO vendor and the font licence audit come next. Comment in the page and press Copy JSON.")
        self.assertTrue(passes("reply-hands-over", reply1=reply))

    def test_a_period_after_an_abbreviation_does_not_end_a_sentence(self):
        for vs in ("vs", "vs."):
            reply = f"Report: {DIR}/index.html\n5 open items; the most pressing is the database decision (Postgres {vs} SQLite). It blocks the migration. Press Copy JSON."
            self.assertEqual(checks.reply_hands_over(self.case(reply))[1], True, vs)
        reply = f"Report: {DIR}/index.html. Decide Postgres vs. SQLite first, e.g. today, because the schema migration, i.e. the blocker, waits; the SSO vendor etc. later. Press Copy JSON."
        self.assertTrue(passes("reply-hands-over", reply1=reply))

    def case(self, reply):
        row, events, states = helpers.good("report", reply1=reply)
        return checks.Case.build(row, events, states)

    def test_the_spoken_summary_skill_md_asks_for_is_one_unit_however_many_topics_it_names(self):
        self.assertTrue(passes("reply-hands-over", reply1=SPOKEN_SUMMARY))
        self.assertTrue(passes("reply-hands-over", reply1=GOOD_REPLY + " SSO, the licence audit and data retention are open."))

    def test_the_directory_name_must_stand_alone(self):
        self.assertFalse(passes("reply-hands-over", reply1="See /r/tmp/sitrep.Ab12CdXY and press Copy JSON."))

    def test_the_directory_alone_counts(self):
        self.assertTrue(passes("reply-hands-over", reply1=f"Report in {DIR}, then Copy JSON."))

    def test_a_file_that_only_looks_like_the_directory_does_not(self):
        self.assertFalse(passes("reply-hands-over", reply1="See /docs/sitrep.html and press Copy JSON."))


class RunCompleted(unittest.TestCase):
    def case(self, expect="none", mutate=None, note="", **override):
        row, events, states = helpers.good(expect, **override)
        if mutate:
            events = mutate(events)
        return checks.Case.build(row, events, states, note)

    def test_a_trace_with_init_only_did_not_complete(self):
        case = self.case(mutate=lambda events: [e for e in events if e.get("type") != "result"])
        self.assertFalse(checks.run_completed(case)[1])
        self.assertEqual(judge_case(case), "INCONCLUSIVE")

    def test_a_runner_note_means_the_run_did_not_complete(self):
        case = self.case(note="agent command exited 124: timed out")
        self.assertFalse(checks.run_completed(case)[1])
        self.assertIn("exited 124", checks.run_completed(case)[2])
        self.assertEqual(judge_case(case), "INCONCLUSIVE")

    def test_every_negative_case_is_inconclusive_when_the_run_crashed(self):
        for kwargs in (dict(result_subtype="error_max_budget_usd"), dict(result_subtype="error_during_execution", calls1=[])):
            self.assertEqual(judge("none", **kwargs), "INCONCLUSIVE", kwargs)

    def test_a_success_result_that_is_an_error_does_not_complete(self):
        def mutate(events):
            return [{**e, "is_error": True} if e.get("type") == "result" else e for e in events]
        self.assertFalse(checks.run_completed(self.case(mutate=mutate))[1])

    def test_a_two_turn_case_needs_two_results(self):
        row, events, states = helpers.good("report-clean")
        self.assertTrue(checks.run_completed(checks.Case.build(row, events, states))[1])
        first = events[:events.index(next(e for e in events if e.get("type") == "result")) + 1]
        self.assertFalse(checks.run_completed(checks.Case.build(row, first, states))[1])

    def test_a_crashed_run_is_not_a_failure_to_trigger(self):
        self.assertEqual(judge("report", result_subtype="error_max_budget_usd", calls1=[], report_found=False), "INCONCLUSIVE")

    def test_the_canary_whose_first_turn_failed_is_inconclusive_not_a_project_write(self):
        row, events, states = helpers.good("report-clean", denials=[{"tool_name": "Write", "tool_input": {"file_path": DIR + "/data.js"}}])
        first = events[:events.index(next(e for e in events if e.get("type") == "result")) + 1]
        states["after2"], states["report2"] = None, None
        case = checks.Case.build(row, first, states)
        self.assertEqual(judge_case(case), "INCONCLUSIVE")
        self.assertTrue({cid: ok for cid, ok, _ in checks.run_checks(case)}["project-untouched"])
        self.assertEqual(judge_case(checks.Case.build(row, first, states, "agent command exited 1: budget")), "INCONCLUSIVE")


class Findings(unittest.TestCase):
    def test_a_skill_that_ran_no_script_is_a_failure_of_the_skill_not_of_the_environment(self):
        calls = [("Skill", {"skill": SKILL}), ("Read", {"file_path": "/r/ws/docs/open-questions.md"})]
        override = dict(calls1=calls, report_found=False, tmp_sitrep=[], reply1=LISTED_REPLY)
        ok = {cid: ok for cid, ok, _ in run("report", **override)}
        self.assertTrue(ok["report-located"])
        self.assertFalse(ok["created-by-script"])
        self.assertEqual(judge("report", **override), "FAIL")

    def test_a_report_script_that_was_tried_and_left_nothing_is_inconclusive(self):
        expect, override, _ = MUTATIONS["report-located"]
        self.assertEqual(judge(expect, **override), "INCONCLUSIVE")

    def test_a_refusal_in_a_tool_result_alone_is_inconclusive(self):
        expect, override = "report", dict(calls1=DENIED_WRITE, omit_data=True)
        self.assertEqual(judge(expect, **override), "INCONCLUSIVE")
        self.assertTrue(passes("not-permission-blocked", calls1=REPORT_CALLS))

    def test_a_tool_result_that_only_contains_the_word_in_another_tool_does_not_block(self):
        calls = REPORT_CALLS + [("Read", {"file_path": "/r/ws/data.js"}, "permission denied is just text here")]
        self.assertTrue(passes("not-permission-blocked", calls1=calls))

    def test_an_edited_index_beats_a_refused_precondition(self):
        label, cid, expect, override, _ = next(m for m in MORE_MUTATIONS if m[1] == "index-untouched")
        self.assertEqual(judge(expect, **override), "FAIL")

    def test_a_missing_index_without_a_report_script_does_not_beat_the_environment(self):
        self.assertEqual(judge("report", skills=(), calls1=[], report_found=False), "INCONCLUSIVE")

    def test_the_wrong_plugin_is_inconclusive(self):
        for override in (dict(plugins=("sitrep", "sitrep")), dict(plugin_path="/installed/sitrep")):
            self.assertEqual(judge("report", **override), "INCONCLUSIVE", override)

    def test_a_recording_without_the_plugin_path_skips_the_path_test(self):
        row, events, states = helpers.good("report", plugin_path="/anywhere")
        del states["plugin"]
        self.assertTrue(checks.skill_loaded(checks.Case.build(row, events, states))[1])


class GoodVariants(unittest.TestCase):
    """Runs that do nothing wrong and must PASS although something about them looks like a failure to a loose check."""

    def verdict(self, expect="report", **override):
        return judge(expect, **override)

    def test_a_denied_syntax_check_of_data_js_is_not_a_blocked_report(self):
        self.assertEqual(self.verdict(calls1=NODE_DENIED), "PASS")
        self.assertEqual(self.verdict(denials=NODE_DENIAL), "PASS")
        self.assertEqual(self.verdict(calls1=NODE_DENIED, denials=NODE_DENIAL), "PASS")
        python = REPORT_CALLS + [("Bash", {"command": f"python3 -c \"open('{DIR}/data.js')\""}, "Permission to use Bash has been denied.")]
        self.assertEqual(self.verdict(calls1=python), "PASS")
        cat = REPORT_CALLS + [("Bash", {"command": f"cat {SCRIPT}"}, "permission denied")]
        self.assertEqual(self.verdict(calls1=cat, denials=[{"tool_name": "Bash", "tool_input": {"command": f"cat {SCRIPT}"}}]), "PASS")

    def test_what_is_blocked_is_report_sh_and_a_write_of_data_js(self):
        for name, tool_input, kinds in (("Bash", {"command": NEW}, {"script"}), ("Bash", {"command": CLEAN}, {"script"}), ("Bash", {"command": NODE_CHECK}, set()),
                                        ("Bash", {"command": f"cat {SCRIPT}"}, set()), ("Write", {"file_path": DIR + "/data.js"}, {"data"}),
                                        ("Edit", {"file_path": "data.js"}, {"data"}), ("Write", {"file_path": DIR + "/data.js.bak"}, set()),
                                        ("Write", {"file_path": DIR + "/mydata.js"}, set()), ("Read", {"file_path": DIR + "/data.js"}, set())):
            self.assertEqual(checks.call_kinds(name, tool_input), kinds, (name, tool_input))
        for denial, kinds in (({"detail": f"denied {NEW}"}, {"script"}), ({"detail": "denied", "path": DIR + "/data.js"}, {"data"}),
                              ({"detail": f"denied {NODE_CHECK}"}, set()), ({"tool_name": "WebFetch", "tool_input": {"url": "https://x/report.sh new"}}, set()),
                              ({"tool_name": "Bash", "tool_input": "report.sh new"}, {"script"})):
            self.assertEqual(checks.denial_kinds(denial), kinds, denial)

    def test_a_denied_cat_of_report_sh_is_not_an_attempt_to_make_a_report(self):
        calls = [("Skill", {"skill": SKILL})]
        denial = {"tool_name": "Bash", "tool_input": {"command": f"cat {SCRIPT}"}}
        self.assertTrue(passes("report-located", calls1=calls, report_found=False, tmp_sitrep=[], denials=[denial]))

    def test_a_relative_report_sh_after_a_cd_is_the_plugin_under_test(self):
        for command in ("scripts/report.sh new", f"cd {PLUGIN}/skills/sitrep && ./scripts/report.sh new", '"${CLAUDE_SKILL_DIR}/scripts/report.sh" new'):
            calls = [("Skill", {"skill": SKILL}), ("Bash", {"command": command}, DIR + "\n"), REPORT_CALLS[2]]
            self.assertEqual(self.verdict(calls1=calls), "PASS", command)

    def test_an_installed_copy_by_absolute_path_is_not(self):
        for command in ("/home/u/.claude/plugins/sitrep/scripts/report.sh new", "~/.claude/plugins/sitrep/scripts/report.sh new", "cd /x && /usr/local/report.sh new"):
            calls = [("Skill", {"skill": SKILL}), ("Bash", {"command": command}, DIR + "\n"), REPORT_CALLS[2]]
            self.assertFalse(passes("skill-loaded", calls1=calls), command)

    def test_a_database_item_whose_title_names_the_migration_it_unblocks(self):
        self.assertTrue(passes("dependency-direction", data=with_items(db={"title": "Postgres or SQLite (blocks migration)"})))
        self.assertTrue(passes("dependency-direction", data=with_items(db={"title": "Postgres or SQLite (blocks migration)"}, migration={"title": "Schema migration (needs the database)"})))

    def test_two_items_that_only_mention_each_other_do_not_make_a_dependency_either_way(self):
        backwards = with_items(db={"title": "Postgres or SQLite (blocks migration)", "dependsOn": ["migration"]}, migration={"dependsOn": []})
        self.assertFalse(passes("dependency-direction", data=backwards))
        unrelated = with_items(db={"title": "SSO vendor note"}, migration={"title": "Font migration", "dependsOn": ["sso"]})
        self.assertFalse(passes("dependency-direction", data=unrelated))

    def test_the_report_removed_in_turn_one_is_a_finding_against_the_skill(self):
        ok = {cid: ok for cid, ok, _ in run("report", calls1=EARLY_CLEAN, report_patch=GONE)}
        self.assertTrue(ok["report-located"])
        self.assertFalse(ok["created-by-script"])
        self.assertEqual(judge("report", calls1=EARLY_CLEAN, report_patch=GONE), "FAIL")
        self.assertEqual(judge("report", calls1=EARLY_CLEAN, report_found=False, tmp_sitrep=[]), "FAIL")

    def test_a_report_script_that_was_tried_and_left_nothing_stays_inconclusive_without_a_clean(self):
        self.assertEqual(self.verdict(calls1=[("Skill", {"skill": SKILL}), ("Bash", {"command": NEW}, "")], report_found=False, tmp_sitrep=[]), "INCONCLUSIVE")

    def test_the_clean_of_another_directory_in_turn_one_is_not_this_report_cleaned(self):
        other = REPORT_CALLS + [("Bash", {"command": f'"{helpers.SCRIPT}" clean /r/tmp/sitrep.Other1'}, "deleted /r/tmp/sitrep.Other1\n")]
        self.assertTrue(passes("created-by-script", calls1=other))

    def test_a_clean_that_deleted_the_directory_and_no_rm_passes(self):
        self.assertEqual(self.verdict("report-clean"), "PASS")

    def test_a_removal_that_does_not_name_the_directory_but_a_sitrep_glob_fails(self):
        for command in ('rm -rf "$TMPDIR"/sitrep.*', "find $TMPDIR -name 'sitrep.*' -delete"):
            calls = CLEAN_CALLS + [("Bash", {"command": command}, "")]
            self.assertFalse(passes("cleaned-by-script", expect="report-clean", calls2=calls), command)
        unrelated = CLEAN_CALLS + [("Bash", {"command": "rm -f /r/ws/notes.txt"}, "")]
        self.assertTrue(passes("cleaned-by-script", expect="report-clean", calls2=unrelated))

    def test_a_reply_that_is_long_but_names_no_topic_still_fails_on_length_alone(self):
        self.assertFalse(passes("reply-hands-over", reply1=GOOD_REPLY + " " + "More words. " * 150))


class Dependency(unittest.TestCase):
    def test_the_direction_matters_to_the_advisory_check_and_not_to_the_structural_one(self):
        self.assertTrue(passes("dependency-direction"))
        self.assertFalse(passes("dependency-direction", data=reversed_dependency()))
        self.assertTrue(passes("dependency-recorded", data=reversed_dependency()))

    def test_a_cycle_has_a_link_but_no_direction(self):
        data = with_items(db={"dependsOn": ["migration"]})
        self.assertFalse(passes("dependency-direction", data=data))
        self.assertTrue(passes("dependency-recorded", data=data))

    def test_a_database_item_that_mentions_the_migration_is_still_a_database_item(self):
        self.assertTrue(passes("dependency-direction", data=with_items(db={"detail": "Postgres or SQLite; it unblocks the schema migration"})))

    def test_titles_decide_before_details(self):
        data = with_items(db={"title": "Postgres or SQLite?"}, migration={"title": "Schema migration blocked", "detail": "Waits for Postgres or SQLite."})
        self.assertTrue(passes("dependency-direction", data=data))

    def test_a_database_item_with_a_neutral_title_is_found_by_the_dependency_that_points_at_it(self):
        data = with_items(db={"title": "Storage backend", "detail": "Postgres or SQLite; the schema migration waits on it."})
        self.assertTrue(passes("dependency-direction", data=data))
        self.assertTrue(passes("dependency-recorded", data=data))
        self.assertFalse(passes("dependency-direction", data=with_items(db={"title": "Storage backend", "detail": "Postgres or SQLite; the schema migration waits on it."},
                                                                         migration={"dependsOn": []})))

    def test_a_link_needs_an_existing_other_item(self):
        self.assertFalse(passes("dependency-recorded", data=with_items(migration={"dependsOn": ["migration"]}, db={"dependsOn": []})))
        self.assertFalse(passes("dependency-recorded", data=with_items(migration={"dependsOn": ["ghost"]}, db={"dependsOn": []})))
        self.assertFalse(passes("dependency-recorded", data=with_items(migration={"dependsOn": "db"}, db={"dependsOn": []})))
        self.assertTrue(passes("dependency-recorded", data=with_items(sso={"dependsOn": ["fonts"]}, migration={"dependsOn": []}, db={"dependsOn": []})))


class Coverage(unittest.TestCase):
    def test_each_planted_item_needs_an_item_of_its_own(self):
        self.assertFalse(passes("items-cover-planted", data=with_items(retention=None, sso={"detail": "Vendor choice. Retention is unclear too."})))
        self.assertTrue(passes("items-cover-planted", data=with_items(sso={"detail": "Vendor choice. Retention is unclear too."})))

    def test_one_item_that_mentions_two_topics_can_stand_for_only_one(self):
        merged = with_items(migration=None, db={"detail": "Postgres or SQLite, and the schema migration that follows."})
        self.assertFalse(passes("items-cover-planted", data=merged))

    def test_an_item_matching_two_keys_is_assigned_to_the_free_one(self):
        data = with_items(migration={"detail": "Scripts differ between Postgres and SQLite."})
        self.assertTrue(passes("items-cover-planted", data=data))


class MissingState(unittest.TestCase):
    """State that was never recorded (a case that was not run, a trace without its .state.json) proves nothing about the skill: INCONCLUSIVE
    in every case, never FAIL (round 4, R4-1). Facts read from the trace still stand."""

    def case_without(self, expect, *names, **override):
        row, events, states = helpers.good(expect, **override)
        for name in names:
            states[name] = None
        return checks.Case.build(row, events, states)

    def test_a_missing_before_or_after1_snapshot_is_inconclusive_for_every_case(self):
        for expect in ALL_EXPECTS:
            for name in ("before", "after1"):
                with self.subTest(expect=expect, missing=name):
                    case = self.case_without(expect, name)
                    results = checks.run_checks(case)
                    self.assertFalse({c: ok for c, ok, _ in results}["state-recorded"])
                    self.assertEqual(judge_case(case), "INCONCLUSIVE")

    def test_the_note_names_the_missing_snapshot(self):
        case = self.case_without("report", "after1")
        cid, ok, note = checks.state_recorded(case)
        self.assertFalse(ok)
        self.assertIn("after1", note)
        self.assertIn("project-untouched", {c for c, ok, _ in checks.run_checks(case) if not ok})

    def test_a_missing_snapshot_after_turn_two_is_inconclusive(self):
        case = self.case_without("report-clean", "after2")
        self.assertTrue(checks.state_recorded(case)[1])        # turn 2's snapshot is run-completed's, per turn
        results = {c: ok for c, ok, _ in checks.run_checks(case)}
        self.assertFalse(results["run-completed"])
        self.assertEqual(judge_case(case), "INCONCLUSIVE")

    def test_a_missing_tmp_listing_is_inconclusive_for_a_negative_case_only(self):
        self.assertEqual(judge_case(self.case_without("none", "tmp_sitrep")), "INCONCLUSIVE")
        self.assertTrue(checks.state_recorded(self.case_without("report", "tmp_sitrep"))[1])
        self.assertTrue(checks.state_recorded(checks.Case.build(*helpers.good("none")))[1])    # [] (no report directory) is a record

    def test_no_state_file_at_all_is_inconclusive_for_every_case(self):
        for expect in ALL_EXPECTS:
            with self.subTest(expect=expect):
                row, events, _ = helpers.good(expect)
                for states in ({}, None):
                    case = checks.Case.build(row, events, states)
                    self.assertEqual(judge_case(case), "INCONCLUSIVE")
                    self.assertFalse({c: ok for c, ok, _ in checks.run_checks(case)}["state-recorded"])

    def test_a_case_that_was_never_recorded_is_inconclusive_not_a_failure(self):
        for expect in ALL_EXPECTS:
            with self.subTest(expect=expect):
                row, _, _ = helpers.good(expect)
                self.assertEqual(judge_case(checks.Case.build(row, [], {}, "no recorded trace for x")), "INCONCLUSIVE")

    def test_nothing_of_the_state_is_not_a_write_into_the_project(self):
        case = self.case_without("feedback", "before", "after1")
        results = {c: ok for c, ok, _ in checks.run_checks(case)}
        self.assertEqual([c for c, ok in results.items() if not ok], ["state-recorded", "project-untouched"])
        self.assertEqual(judge_case(case), "INCONCLUSIVE")

    def test_a_write_into_the_project_still_fails_when_both_snapshots_exist(self):
        self.assertEqual(judge("feedback", after1_extra={"notes.md": "x"}), "FAIL")

    def test_what_the_trace_proves_stands_without_the_state(self):
        # a skill that was called in a negative case, a report.sh call in a negative case, a skill that never ran in a positive case
        row, events, states = helpers.good("none", calls1=[("Skill", {"skill": SKILL})])
        self.assertEqual(judge_case(checks.Case.build(row, events, {})), "FAIL")
        row, events, states = helpers.good("none", calls1=[("Bash", {"command": NEW}, DIR + "\n")])
        self.assertEqual(judge_case(checks.Case.build(row, events, {})), "FAIL")
        row, events, states = helpers.good("report", calls1=[], report_found=False)
        self.assertEqual(judge_case(checks.Case.build(row, events, {})), "FAIL")

    def test_a_crash_without_state_stays_inconclusive(self):
        row, events, _ = helpers.good("report", result_subtype="error_max_budget_usd")
        self.assertEqual(judge_case(checks.Case.build(row, events, {})), "INCONCLUSIVE")


class Turns(unittest.TestCase):
    def test_split_turns(self):
        row, events, states = helpers.good("report-clean")
        self.assertEqual(len(checks.split_turns(events)), 2)

    def test_the_report_is_not_cleaned_when_it_was_never_located(self):
        self.assertFalse(passes("report-cleaned", expect="report-clean", report_found=False))

    def test_a_cleaned_report_needs_the_second_record(self):
        row, events, states = helpers.good("report-clean")
        states["report2"] = None
        self.assertFalse(checks.report_cleaned(checks.Case.build(row, events, states))[1])


if __name__ == "__main__":
    unittest.main()
