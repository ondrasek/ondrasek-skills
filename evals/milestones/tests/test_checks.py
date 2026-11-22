"""Every check has a passing trace and a failing one. A test fails when a check id has no failing trace (see test_every_check_is_mutated)."""
import unittest

import helpers
import checks
import run_evals
import test_schema_check
from helpers import INIT_EMPTY, NOTHING, SAME, SET_2_DONE, SET_3_DOING, SHOW, SKILL_ID, moved, tracker

SKILL_CALL = ("Skill", {"skill": SKILL_ID})
READ_TRACKER = ("Bash", {"command": "cat .cache/milestones.json"})
BARE_EXPECTS = ("show", "update", "create", "none", "reminded-update", "reminded-quiet")


def without(data, ident, key):
    """A copy of tracker `data` whose item `ident` lacks `key`."""
    out = moved(data, {})
    next(i for i in out["items"] if i["id"] == ident).pop(key, None)
    return out


def with_top_level(data, **extra):
    return {**data, **extra}


T = tracker("tracker")
INLINE = {"name": "milestones", "source": "milestones@inline", "path": "/w/plugins/milestones"}
INSTALLED = {"name": "milestones", "source": "milestones@ondrasek-skills", "path": "/home/u/cache/milestones"}
NEW_TRACKER = {"title": "Other", "updated": helpers.FUTURE, "items": [{"id": n, "name": f"schema importer migration {n}", "state": "todo"} for n in (1, 2, 3)]}

# check id -> (expect, override of helpers.good that must make exactly that check fail)
MUTATIONS = {
    "skill-loaded": ("update", dict(skills=())),
    "plugin-under-test-isolated": ("show", dict(entries=[INLINE, INSTALLED])),
    "skill-trigger": ("show", dict(calls=[("Bash", {"command": SHOW})])),
    "not-permission-blocked": ("update", dict(denials=[{"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}], outputs={SET_2_DONE: ("denied", True)})),
    "shown-by-script": ("show", dict(outputs={SHOW: ("python3: can't open file 'milestones.py'", True)})),
    "agent-completed": ("update", dict(result_error="error_max_budget_usd")),
    "show-reads-only": ("show", dict(after_tracker=moved(T, {2: "done"}))),
    "reply-names-current": ("show", dict(reply="Here is the tracker.")),
    "tracker-valid": ("update", dict(after_tracker=with_top_level(moved(T, {2: "done", 3: "doing"}), owner="me"))),
    "wanted-state": ("update", dict(after_tracker=moved(T, {2: "done"}))),
    "others-untouched": ("update", dict(after_tracker=moved(T, {2: "done", 3: "doing", 4: "doing"}))),
    "timestamps-by-script": ("update", dict(after_tracker=without(moved(T, {2: "done", 3: "doing"}), 2, "finished"))),
    "no-direct-tracker-write": ("update", dict(calls=helpers.CALLS["update"] + [("Write", {"file_path": ".cache/milestones.json"})])),
    "written-by-script": ("update", dict(calls=[SKILL_CALL])),
    "tracker-not-committed": ("update", dict(committed=[".cache/milestones.json"])),
    "nothing-else-changed": ("show", dict(after_extra={"MILESTONES.md": "# Milestones\n"})),
    "tracker-created": ("create", dict(after_tracker=NEW_TRACKER)),
    "no-tracker-when-negative": ("none", dict(after_tracker=NEW_TRACKER)),
    "reminder-fired": ("reminded-update", dict(marker=False)),
    "reminder-delivered": ("reminded-quiet", dict(hook=False)),
    "quiet-when-nothing-changed": ("reminded-quiet", dict(after_tracker=moved(tracker("stale-idle"), {2: "done"}))),
    # R3-1: an invented update the script refused (is_error, no denial) leaves the tracker as it was: only the trace shows the attempt
    "no-write-attempt": ("reminded-quiet", dict(calls=[("Read", {"file_path": "/w/src/textutil.py"}), ("Bash", {"command": SET_2_DONE})],
                                                outputs={SET_2_DONE: ("error: item 2 is already doing", True)})),
}


def run(expect, **override):
    row, events, states = helpers.good(expect, **override)
    return {cid: (ok, note) for cid, ok, note in checks.run_checks(checks.Case.build(row, events, states))}


def failed(expect, **override):
    return sorted(cid for cid, (ok, _) in run(expect, **override).items() if not ok)


class GoodRuns(unittest.TestCase):
    def test_every_good_run_passes_every_check(self):
        for expect in BARE_EXPECTS:
            for cid, (ok, note) in run(expect).items():
                self.assertTrue(ok, f"{expect}: {cid}: {note}")

    def test_a_negative_on_a_tracker_fixture_may_edit_code(self):
        results = run("none", fixture="tracker", src_edit=True)
        self.assertEqual(sorted(results), sorted(["agent-completed", "plugin-under-test-isolated", "show-reads-only", "skill-loaded", "skill-trigger", "no-direct-tracker-write",
                                                  "tracker-not-committed", "nothing-else-changed", "not-permission-blocked", "no-write-attempt"]))
        self.assertTrue(all(ok for ok, _ in results.values()))

    def test_a_negative_on_a_bare_fixture_runs_the_bare_branch(self):
        self.assertEqual(sorted(run("none")), sorted(["agent-completed", "plugin-under-test-isolated", "no-tracker-when-negative", "skill-loaded", "skill-trigger",
                                                      "no-direct-tracker-write", "tracker-not-committed", "nothing-else-changed", "not-permission-blocked",
                                                      "no-write-attempt"]))

    def test_the_checks_that_run_are_known(self):
        for expect in BARE_EXPECTS:
            self.assertLessEqual(set(run(expect)), set(checks.GROUPS))


class Mutations(unittest.TestCase):
    def test_every_check_is_mutated(self):
        self.assertEqual(set(MUTATIONS), set(checks.GROUPS))

    def test_each_mutation_fails_exactly_its_own_check(self):
        for cid, (expect, override) in MUTATIONS.items():
            with self.subTest(check=cid):
                self.assertEqual(failed(expect, **override), [cid])

    def test_a_negative_on_a_tracker_fixture_that_changes_the_tracker_fails(self):
        self.assertEqual(failed("none", fixture="tracker", after_tracker=moved(T, {2: "done"})), ["show-reads-only"])

    def test_a_negative_that_creates_a_stray_cache_file_fails(self):
        self.assertEqual(failed("none", after_extra={".cache/notes.md": "x"}), ["nothing-else-changed"])
        self.assertEqual(failed("none", fixture="tracker", after_extra={".cache/notes.md": "x"}), ["nothing-else-changed"])

    def test_a_negative_on_a_tracker_fixture_may_change_only_the_code_file(self):
        self.assertEqual(failed("none", fixture="tracker", after_extra={"README.md": "changed\n"}), ["nothing-else-changed"])

    def test_a_negative_that_commits_the_tracker_fails(self):
        self.assertEqual(failed("none", fixture="tracker", committed=[".cache/milestones.json"]), ["tracker-not-committed"])
        self.assertEqual(failed("none", fixture="tracker", after_head="h2"), ["tracker-not-committed"])

    def test_a_negative_that_writes_the_tracker_directly_fails(self):
        self.assertEqual(failed("none", fixture="tracker", calls=[("Write", {"file_path": ".cache/milestones.json"})]), ["no-direct-tracker-write"])

    def test_the_negative_branch_comes_from_the_fixture_not_from_the_snapshot(self):
        # a tracker fixture whose snapshots are missing must not fall back to the bare branch (which has no show-reads-only)
        row, events, _ = helpers.good("none", fixture="tracker")
        ids = [cid for cid, _, _ in checks.run_checks(checks.Case.build(row, events, {}))]
        self.assertIn("show-reads-only", ids)
        self.assertNotIn("no-tracker-when-negative", ids)


class Process(unittest.TestCase):
    def test_a_loaded_skill_in_another_plugin_is_not_this_plugin(self):
        self.assertEqual(failed("show", plugins=("other",)), ["skill-loaded"])

    def test_an_installed_copy_is_not_the_copy_under_test(self):
        # P8: only the installed copy loaded: the plugin under test did not (process) and the environment leaked (precondition)
        self.assertEqual(failed("show", entries=[INSTALLED]), ["plugin-under-test-isolated", "skill-loaded"])

    def test_an_installed_and_an_inline_copy_together_are_a_leak_not_a_plugin_failure(self):
        # R2-6: the plugin under test loaded fine (skill-loaded passes); the extra copy makes the trial INCONCLUSIVE, not FAIL
        for entries in ([INLINE, INSTALLED], [INSTALLED, INLINE], [INLINE, dict(INLINE)]):
            self.assertEqual(failed("show", entries=entries), ["plugin-under-test-isolated"])
        row, events, states = helpers.good("show", entries=[INLINE, INSTALLED])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_the_isolation_precondition_runs_in_every_case(self):
        for expect in BARE_EXPECTS:
            self.assertIn("plugin-under-test-isolated", run(expect), expect)

    def test_an_absent_plugin_is_a_plugin_failure_not_a_leak(self):
        self.assertEqual(failed("show", plugins=("other",)), ["skill-loaded"])

    def test_a_path_equal_to_the_plugin_directory_counts_as_the_copy_under_test(self):
        by_path = [{"name": "milestones", "source": "milestones@local", "path": str(helpers.PLUGIN_DIR)}]
        row, events, states = helpers.good("show", entries=by_path)
        for plugin, ok in ((str(helpers.PLUGIN_DIR), True), ("", False), ("/elsewhere", False)):
            results = {c: o for c, o, _ in checks.run_checks(checks.Case.build(row, events, states, plugin=plugin))}
            self.assertEqual(results["skill-loaded"], ok, plugin)
            self.assertEqual(results["plugin-under-test-isolated"], ok, plugin)

    def test_no_init_event_fails_skill_loaded(self):
        row, events, states = helpers.good("show")
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events[1:], states))}
        self.assertFalse(results["skill-loaded"])

    def test_the_skill_may_arrive_without_a_plugin_prefix(self):
        self.assertEqual(failed("show", skills=("milestones",), calls=[("Skill", {"skill": "milestones"}), ("Bash", {"command": SHOW})]), [])

    def test_a_negative_that_triggers_fails(self):
        self.assertEqual(failed("none", calls=[SKILL_CALL]), ["skill-trigger"])

    def test_the_trigger_is_not_graded_in_the_reminder_cases(self):
        self.assertNotIn("skill-trigger", run("reminded-update"))
        self.assertNotIn("skill-trigger", run("reminded-quiet"))

    def test_the_script_invocations_come_from_the_parsed_argv(self):
        for command, subs in [('python3 "/p/skills/milestones/scripts/milestones.py" show', ["show"]), ("python3 /p/milestones.py show --json", ["show"]),
                              ("cd /w && python3 '/p/milestones.py' set 2 --state done", ["set"]), ("python3 milestones.py init --empty --title X", ["init"]),
                              ("MILESTONES_FILE=x python3 -u /p/milestones.py add --name y", ["add"]),
                              ('python3 /p/milestones.py set 2 --state done && python3 /p/milestones.py show', ["set", "show"]),
                              ("python3.12 /p/milestones.py title Z", ["title"])]:
            self.assertEqual(checks.script_invocations(command), subs, command)
        # a text that only mentions the script is not a call; neither is another subcommand
        for command in ["cat .cache/milestones.json", "python3 other.py show", "python3 /p/milestones.py statusline", "python3 /p/milestones.py",
                        "echo python3 milestones.py set 2", "grep -n 'milestones.py set' notes.txt", "python3 -c \"print('milestones.py set 2')\"",
                        "vim milestones.py"]:
            self.assertEqual(checks.script_invocations(command), [], command)

    def test_a_show_with_a_cd_prefix_and_json_counts(self):
        self.assertEqual(failed("show", calls=[SKILL_CALL, ("Bash", {"command": f'cd /w && python3 "{helpers.SCRIPT}" show --json'})]), [])

    def test_a_show_that_failed_is_not_a_show(self):
        # P6: the script call errors, then the agent cats the JSON and formats a table itself
        failing = {SHOW: ("python3: can't open file", True)}
        self.assertEqual(failed("show", outputs=failing, calls=[SKILL_CALL, ("Bash", {"command": SHOW}), READ_TRACKER]), ["shown-by-script"])

    def test_a_show_that_never_ran_is_not_a_show(self):
        self.assertEqual(failed("show", calls=[SKILL_CALL, READ_TRACKER]), ["shown-by-script"])

    def test_a_show_without_a_tool_result_or_without_the_title_is_not_a_show(self):
        row, events, states = helpers.good("show")
        bare = [e for e in events if not (e.get("type") == "user" and e["message"]["content"][0].get("type") == "tool_result")]
        self.assertFalse({c: o for c, o, _ in checks.run_checks(checks.Case.build(row, bare, states))}["shown-by-script"])
        self.assertEqual(failed("show", outputs={SHOW: ("no milestones yet: /w/.cache/milestones.json", False)}), ["shown-by-script"])

    def test_a_json_show_counts_when_its_output_has_the_title(self):
        command = f'python3 "{helpers.SCRIPT}" show --json'
        self.assertEqual(failed("show", calls=[SKILL_CALL, ("Bash", {"command": command})], outputs={command: ('{"title": "To release 1.0", "items": []}', False)}), [])

    def test_a_subagent_write_is_visible_only_in_the_state(self):
        # the trace holds the main conversation only: a tracker changed by a subagent shows as a changed tracker with no script call
        row, events, states = helpers.good("update", after_tracker=moved(T, {2: "done", 3: "doing"}), calls=[SKILL_CALL])
        sub = helpers.call("Write", file_path=".cache/milestones.json")
        sub["parent_tool_use_id"] = "toolu_parent"
        events.insert(-1, sub)
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertTrue(results["no-direct-tracker-write"])
        self.assertFalse(results["written-by-script"])


class Gates(unittest.TestCase):
    def test_bash_commands_that_write_the_tracker_fail(self):
        for command in ["echo '{}' > .cache/milestones.json", "cat > .cache/milestones.json <<EOF\n{}\nEOF", "tee .cache/milestones.json",
                        "sed -i '' s/todo/done/ .cache/milestones.json", "mv /tmp/x.json .cache/milestones.json", "rm .cache/milestones.json",
                        "python3 -c \"import json; json.dump({}, open('.cache/milestones.json', 'w'))\""]:
            with self.subTest(command=command):
                self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [("Bash", {"command": command})]), ["no-direct-tracker-write"])

    def test_a_compound_command_does_not_excuse_its_write(self):
        # P5: the script call in the first segment must not excuse the redirect in the second
        for command in [f'python3 "{helpers.SCRIPT}" show && cat > .cache/milestones.json <<EOF\n{{}}\nEOF',
                        f'python3 "{helpers.SCRIPT}" show; echo x >> .cache/milestones.json',
                        f'python3 "{helpers.SCRIPT}" show > .cache/milestones.json',
                        "cat .cache/milestones.json | python3 -c 'print(1)' && rm .cache/milestones.json",
                        "cp /tmp/x.json .cache/milestones.json",
                        "python3 -c \"p = '.cache/milestones.json'; import pathlib; pathlib.Path(p).write_text('{}')\""]:
            with self.subTest(command=command):
                self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [("Bash", {"command": command})]), ["no-direct-tracker-write"])

    def test_read_only_python_that_mentions_the_tracker_passes(self):
        # ms_probe2: json.dumps of a loaded tracker is a read; so is copying the tracker somewhere else
        for command in ["python3 -c \"import json; print(json.dumps(json.load(open('.cache/milestones.json')), indent=2))\"",
                        "python3 -c \"import sys; sys.stdout.write(open('.cache/milestones.json').read())\"",
                        "python3 -c \"print(open('.cache/milestones.json', 'r').read())\"",
                        "cp .cache/milestones.json /tmp/backup.json", "cat .cache/milestones.json | python3 -m json.tool"]:
            with self.subTest(command=command):
                self.assertEqual(failed("show", calls=[SKILL_CALL, ("Bash", {"command": SHOW}), ("Bash", {"command": command})]), [])

    def test_a_read_with_a_later_string_argument_is_not_a_write(self):
        # R2-4 / P3: the mode of open() is the string inside that call, not any later string that starts with w, a or x
        for command in ["python3 -c \"import json; d=json.load(open('.cache/milestones.json')); print(d['title'], 'at', d['updated'])\"",
                        "python3 -c \"d=json.load(open('.cache/milestones.json')); print('x', 'y', 'with', 'all')\"",
                        "python3 -c \"print(len(open('.cache/milestones.json').read()), 'words')\""]:
            with self.subTest(command=command):
                self.assertFalse(checks.writes_tracker(command), command)
                self.assertEqual(failed("show", calls=[SKILL_CALL, ("Bash", {"command": SHOW}), ("Bash", {"command": command})]), [])

    def test_python_writes_in_their_spellings_still_fail(self):
        for command in ["python3 -c \"open('.cache/milestones.json', 'w').write('{}')\"",
                        "python3 -c \"open('.cache/milestones.json','wb').write(b'{}')\"",
                        "python3 -c \"open(os.path.join('.cache', 'milestones.json'), 'w').write('{}')\"",
                        "python3 -c \"from pathlib import Path; Path('.cache/milestones.json').open('a').write('x')\"",
                        "python3 -c \"f = open('.cache/milestones.json', mode='w')\""]:
            with self.subTest(command=command):
                self.assertTrue(checks.writes_tracker(command), command)

    def test_a_redirect_into_another_file_is_not_a_tracker_write(self):
        # R2-4: `milestones.json.bak` is not the tracker; the script's own output saved beside it is not a direct write
        for command in [f'python3 "{helpers.SCRIPT}" show --json > .cache/milestones.json.bak', "cat .cache/milestones.json > /tmp/copy.json",
                        "cp .cache/milestones.json .cache/milestones.json.bak", "cat .cache/milestones.json 2>&1"]:
            with self.subTest(command=command):
                self.assertFalse(checks.writes_tracker(command), command)
        for command in ["echo x > .cache/milestones.json", "echo x >> '.cache/milestones.json'", "echo x > ./.cache/milestones.json  # note"]:
            with self.subTest(command=command):
                self.assertTrue(checks.writes_tracker(command), command)

    def test_more_writes_in_their_spellings_fail(self):
        # R3-7: tools that rewrite a file in place, with a script call beside them that would fake the timestamps
        for command in ["perl -pi -e 's/todo/done/' .cache/milestones.json", "perl -i.bak -pe 's/a/b/' .cache/milestones.json",
                        "perl -pie 's/a/b/' .cache/milestones.json", "jq '.title=\"x\"' .cache/milestones.json | sponge .cache/milestones.json",
                        "dd if=/tmp/x.json of=.cache/milestones.json", "awk -i inplace '{print}' .cache/milestones.json",
                        "sed --in-place s/a/b/ .cache/milestones.json", "git rm .cache/milestones.json"]:
            with self.subTest(command=command):
                self.assertTrue(checks.writes_tracker(command), command)
                self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [("Bash", {"command": command})]), ["no-direct-tracker-write"])

    def test_more_reads_pass(self):
        for command in ["python3 -c \"import json, sys; d = json.load(open('.cache/milestones.json')); json.dump(d, sys.stdout, indent=2)\"",
                        "python3 -c \"import json,sys; json.dump(json.load(open('.cache/milestones.json')), sys.stderr)\"",
                        "git rm --cached .cache/milestones.json", "git rm -r --cached -q .cache/milestones.json",
                        "perl -ne 'print if /todo/' .cache/milestones.json", "perl -Mstrict -e 'print 1' .cache/milestones.json",
                        "dd if=.cache/milestones.json of=/tmp/copy.json", "awk '/state/' .cache/milestones.json", "sed -n 1,5p .cache/milestones.json",
                        "jq .title .cache/milestones.json", "wc -l .cache/milestones.json", "git log --oneline -- .cache/milestones.json"]:
            with self.subTest(command=command):
                self.assertFalse(checks.writes_tracker(command), command)

    def test_a_dump_to_a_real_file_is_still_a_write(self):
        self.assertTrue(checks.writes_tracker("python3 -c \"import json; json.dump({}, open('.cache/milestones.json', 'w')); print(1, file=sys.stdout)\""))
        self.assertTrue(checks.writes_tracker("python3 -c \"import json; f = open('.cache/milestones.json', 'w'); json.dump({}, f)\""))

    def test_a_script_text_that_only_mentions_the_script_does_not_excuse_a_write(self):
        command = "python3 -c \"open('.cache/milestones.json', 'w').write('{}')\"  # python3 milestones.py show"
        self.assertTrue(checks.writes_tracker(command))
        self.assertTrue(checks.writes_tracker("echo 'python3 milestones.py show' && echo x > .cache/milestones.json"))

    def test_split_commands_respects_quotes(self):
        self.assertEqual(checks.split_commands("a && b; c | d\ne || f"), ["a ", " b", " c ", " d", "e ", " f"])
        self.assertEqual(checks.split_commands("python3 -c \"x; y | z && w\" ; echo 'a;b'"), ["python3 -c \"x; y | z && w\" ", " echo 'a;b'"])
        self.assertEqual(checks.split_commands("ls 2>&1"), ["ls 2>&1"])

    def test_bash_commands_that_read_the_tracker_pass(self):
        for command in ["cat .cache/milestones.json", "cat .cache/milestones.json 2>&1", "ls -la .cache", "python3 -c \"import json; print(json.load(open('.cache/milestones.json')))\""]:
            with self.subTest(command=command):
                self.assertEqual(failed("show", calls=[SKILL_CALL, ("Bash", {"command": SHOW}), ("Bash", {"command": command})]), [])

    def test_the_script_with_the_tracker_file_in_the_environment_is_not_a_direct_write(self):
        command = f'MILESTONES_FILE=.cache/milestones.json python3 "{helpers.SCRIPT}" set 2 --state done'
        self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [("Bash", {"command": command})]), [])

    def test_edit_and_notebook_edit_of_the_tracker_fail(self):
        for name, key in [("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")]:
            with self.subTest(tool=name):
                self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [(name, {key: "/w/.cache/milestones.json"})]), ["no-direct-tracker-write"])

    def test_a_write_of_another_file_is_not_a_tracker_write(self):
        self.assertEqual(failed("update", calls=helpers.CALLS["update"] + [("Write", {"file_path": "/w/notes.json"})]), [])

    def test_a_moved_head_fails_without_a_cache_path(self):
        self.assertEqual(failed("update", after_head="h2"), ["tracker-not-committed"])

    def test_a_commit_of_the_agents_own_edit_is_not_graded_in_the_reminder_cases(self):
        self.assertEqual(failed("reminded-update", after_head="h2"), [])
        self.assertEqual(failed("reminded-update", after_head="h2", committed=[".cache/milestones.json"]), ["tracker-not-committed"])

    def test_the_reminder_case_may_change_only_textutil(self):
        self.assertEqual(failed("reminded-update", after_extra={"notes.md": "x"}), ["nothing-else-changed"])
        self.assertEqual(failed("reminded-update", after_extra={"README.md": "changed\n"}), ["nothing-else-changed"])

    def test_a_negative_on_bare_may_not_change_a_file(self):
        self.assertEqual(failed("none", after_extra={"README.md": "changed\n"}), ["nothing-else-changed"])

    def test_the_marker_is_not_a_stray_file(self):
        self.assertEqual(failed("reminded-quiet"), [])


class Outcome(unittest.TestCase):
    def test_a_reply_that_calls_the_current_milestone_finished_fails(self):
        for reply in ("Storage layer is finished; nothing is left before you ship.", "Storage layer: done. Sync API is next.", "Storage layer ✅ then Sync API.",
                      "Storage layer has been completed.", "**Storage layer** is done, Sync API is next.", "Storage layer - done", "Storage layer \u2014 completed.",
                      "Storage layer (done)", "| Storage layer | done |", "Storage layer's done and Sync API is next.", "Storage layer is now already done.",
                      "storage layer IS FINISHED"):
            with self.subTest(reply=reply):
                self.assertEqual(failed("show", reply=reply), ["reply-names-current"])

    def test_a_reply_that_says_it_is_finished_in_other_words_fails(self):
        # R3: probes that passed undetected: a verb before the name, "has finished", a check mark after a colon
        for reply in ("Storage layer has finished; Sync API is next.", "Storage layer: \u2705 all tables migrated.", "We finished the Storage layer.",
                      "Storage layer has been shipped.", "Storage layer has wrapped up.", "I completed Storage layer this morning.", "Storage layer is done\nSync API is next.",
                      "Storage layer \u2705\nSync API next"):
            with self.subTest(reply=reply):
                self.assertEqual(failed("show", reply=reply), ["reply-names-current"])

    def test_a_status_list_and_a_subordinate_clause_are_not_a_claim(self):
        # R3-3: the next line or cell starting with "Done:" is a label, not the verb of the line before; "once ... is done" is a plan
        for reply in ("In progress: Storage layer\nDone: Schema design\nNext: Sync API", "**In progress:** Storage layer\n**Done:** Schema design",
                      "Current: Storage layer | Done: Schema design | Next: Sync API", "Sync API starts once Storage layer is done, then Release sign-off.",
                      "After Storage layer is finished, Sync API and Release sign-off remain.", "\U0001f7e1 Storage layer\n\u2705 Schema design",
                      "Storage layer:\nDone: Schema design", "We have not finished the Storage layer yet.", "Until the Storage layer is done nothing ships.",
                      "Storage layer, then Done: nothing yet."):
            with self.subTest(reply=reply):
                self.assertEqual(failed("show", reply=reply), [])

    def test_the_real_show_output_does_not_claim_done(self):
        row, events, states = helpers.good("show", reply=helpers.show_output(tracker("tracker")))
        self.assertTrue({c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}["reply-names-current"])

    def test_a_reply_that_says_it_is_in_progress_passes(self):
        for reply in ("Schema design is done and Storage layer is in progress.", "Storage layer: in progress, not done yet.", "Storage layer is not done; Sync API is next.",
                      "Right now: Storage layer (migrations left).",
                      # R2-1 / P2, P2b: a sentence boundary or a dash before a counter or a partial status is not "it is finished"
                      "Done: Schema design. In progress: Storage layer. Completed 1/4; next is Sync API.",
                      "In progress: Storage layer - done so far: tables; left: migrations.",
                      "Storage layer. Done 1/4.", "Storage layer: done 1/4 tasks.", "Storage layer: completed tables, left: migrations.",
                      "Storage layer (done so far: tables).", "Storage layer is almost done.", "1/4 done. Storage layer is next to finish.",
                      "Finished: Schema design\nDoing: Storage layer\nTodo: Sync API, Release sign-off"):
            with self.subTest(reply=reply):
                self.assertEqual(failed("show", reply=reply), [])

    def test_a_reply_names_the_milestone_in_any_case(self):
        self.assertEqual(failed("show", reply="we are on the STORAGE LAYER"), [])

    def test_a_missing_tracker_fails_the_validity_check(self):
        self.assertIn("tracker-valid", failed("update", after_tracker=NOTHING))

    def test_a_tracker_that_is_not_json_fails_validity(self):
        self.assertIn("tracker-valid", failed("update", after_tracker="{not json"))

    def test_an_invalid_state_and_an_extra_key_fail_validity(self):
        bad = moved(T, {2: "done", 3: "doing"})
        bad["items"][1]["state"] = "finished"
        bad["items"][0]["owner"] = "me"
        results = run("update", after_tracker=bad)
        self.assertFalse(results["tracker-valid"][0])
        self.assertIn("finished", results["tracker-valid"][1])
        self.assertIn("owner", results["tracker-valid"][1])

    def test_needs_must_be_non_empty(self):
        want = "4=blocked,4.needs=set"
        blocked = moved(T, {4: "blocked"})
        self.assertEqual(failed("update", want=want, after_tracker=blocked), ["wanted-state"])
        blocked["items"][3]["needs"] = "you"
        self.assertEqual(failed("update", want=want, after_tracker=blocked), [])

    def test_an_item_the_prompt_names_may_change_its_state_but_not_its_name(self):
        renamed = moved(T, {2: "done", 3: "doing"})
        renamed["items"][1]["name"] = "Something else"
        self.assertEqual(failed("update", after_tracker=renamed), ["others-untouched"])

    def test_needs_must_name_the_user_when_the_prompt_says_so(self):
        # R3-2: the field behind the statusline flag: the plugin's is_you() takes exactly "you" or "author", case aside
        want = "4=blocked,4.needs=you"
        blocked = moved(T, {4: "blocked"})
        for value, bad in (("legal", True), ("", True), ("you", False), ("You", False), ("author", False), (" Author ", False), ("me", True), ("user", True),
                           ("you (the key from legal)", True), ("you: signing key from legal", True), ("Me, signing key from legal", True), ("member", True)):
            blocked["items"][3]["needs"] = value
            self.assertEqual(failed("update", want=want, after_tracker=blocked), ["wanted-state"] if bad else [], value)

    def test_the_wanted_needs_agree_with_the_plugin_is_you(self):
        script = test_schema_check.load_script()
        for value in ("you", "You", "author", "me", "user", "you (x)", "legal", ""):
            self.assertEqual(value.strip().lower() in checks.USER_NEEDS, script.is_you(value, set()), value)

    def test_a_removed_or_renumbered_item_fails_others_untouched(self):
        gone = moved(T, {2: "done", 3: "doing"})
        gone["items"].pop()
        self.assertEqual(failed("update", after_tracker=gone), ["others-untouched"])
        renumbered = moved(T, {2: "done", 3: "doing"})
        renumbered["items"][3]["id"] = 9
        self.assertEqual(failed("update", after_tracker=renumbered), ["others-untouched"])

    def test_a_want_that_only_says_needs_you_does_not_flag_the_item_as_changed(self):
        # R2-5 / P13: `4.needs=you` alone names item 4: setting its needs is not a change to an item the prompt did not name
        after = moved(T, {})
        after["items"][3]["needs"] = "you"
        self.assertEqual(failed("update", want="4.needs=you", after_tracker=after), [])

    def test_a_refreshed_updated_field_is_required(self):
        stale = moved(T, {2: "done", 3: "doing"})
        stale["updated"] = T["updated"]
        self.assertEqual(failed("update", after_tracker=stale), ["timestamps-by-script"])

    def test_an_unchanged_tracker_fails_the_update_outcome(self):
        self.assertEqual(failed("update", after_tracker=SAME, calls=[SKILL_CALL]), ["timestamps-by-script", "wanted-state"])

    def test_a_missing_started_fails(self):
        self.assertEqual(failed("update", after_tracker=without(moved(T, {2: "done", 3: "doing"}), 3, "started")), ["timestamps-by-script"])

    def test_no_tracker_fails_creation_and_validity(self):
        self.assertEqual(failed("create", after_tracker=NOTHING), ["tracker-created", "tracker-valid"])

    def test_create_needs_the_count_and_the_names(self):
        few = {"title": "Importer", "updated": helpers.FUTURE, "items": [{"id": 1, "name": "Design the schema", "state": "todo"}]}
        notes = run("create", after_tracker=few)["tracker-created"][1]
        self.assertIn("1 items", notes)
        self.assertIn("importer", notes)

    def test_each_wanted_name_needs_its_own_item(self):
        # R3-8: one item holding every keyword plus junk items is not three milestones
        junk = {"title": "Importer", "updated": helpers.FUTURE, "items": [{"id": 1, "name": "schema importer migration", "state": "todo"},
                                                                         {"id": 2, "name": "a", "state": "todo"}, {"id": 3, "name": "b", "state": "todo"}]}
        notes = run("create", after_tracker=junk)["tracker-created"]
        self.assertFalse(notes[0])
        self.assertIn("distinct", notes[1])
        self.assertEqual(failed("create", after_tracker=junk), ["tracker-created"])

    def test_unmatched_words_is_a_matching_not_a_greedy_scan(self):
        self.assertEqual(checks.unmatched_words(["schema", "importer"], ["importer schema", "importer"]), [])
        self.assertEqual(len(checks.unmatched_words(["schema", "importer", "migration"], ["schema importer migration", "a", "b"])), 2)
        self.assertEqual(checks.unmatched_words(["a"], []), ["a"])
        self.assertEqual(checks.unmatched_words([], ["x"]), [])

    def test_a_tracker_that_existed_before_is_not_created(self):
        row, events, states = helpers.good("create")
        states["before"] = helpers.state.snapshot_from({".cache/milestones.json": "{}\n"})
        self.assertFalse(dict((c, ok) for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states)))["tracker-created"])

    def test_the_quiet_check_ignores_bookkeeping_fields(self):
        touched = moved(tracker("stale-idle"), {})
        self.assertEqual(failed("reminded-quiet", after_tracker=touched), [])


class Precondition(unittest.TestCase):
    def test_a_denial_may_arrive_as_a_system_event(self):
        row, events, states = helpers.good("update", outputs={SET_2_DONE: ("denied", True)})
        events.insert(1, {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_input": {"command": SET_2_DONE}})
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["not-permission-blocked"])

    def test_denials_without_an_id_and_results_without_is_error_do_not_clear_themselves(self):
        # R2-3 / P5: every script call denied, the denial elements carry no tool_use_id and the tool_results no is_error: still INCONCLUSIVE
        denials = [{"tool_name": "Bash", "tool_input": {"command": c}} for c in (SET_2_DONE, SET_3_DOING)]
        row, events, states = helpers.good("update", denials=denials, after_tracker=SAME)
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["not-permission-blocked"])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_a_denial_whose_result_reads_like_a_denial_is_not_a_success(self):
        denied_text = "Permission to use Bash with command python3 milestones.py set has been denied."
        row, events, states = helpers.good("update", denials=[{"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}],
                                           outputs={SET_2_DONE: (denied_text, False)})
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["not-permission-blocked"])

    def test_a_denial_without_an_id_is_cleared_by_one_more_successful_identical_call(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial], calls=[SKILL_CALL, ("Bash", {"command": SET_2_DONE}), ("Bash", {"command": SET_2_DONE}),
                                                                           ("Bash", {"command": SET_3_DOING})])
        self.assertTrue({c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}["not-permission-blocked"])
        # with one call only, the denial and the call are the same call
        row, events, states = helpers.good("update", denials=[denial])
        self.assertFalse({c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}["not-permission-blocked"])

    def test_a_denied_edit_is_not_a_blocked_script(self):
        self.assertEqual(failed("update", denials=[{"tool_name": "Edit", "tool_input": {"file_path": "x"}}]), [])

    def test_the_wanted_update_proves_delivery_when_the_trace_does_not(self):
        results = run("reminded-update", hook=False)
        self.assertTrue(results["reminder-delivered"][0])
        self.assertIn("attributed by state only", results["reminder-delivered"][1])

    def test_a_delivered_report_without_the_update_fails_the_outcome_only(self):
        self.assertEqual(failed("reminded-update", after_tracker=SAME, calls=[helpers.CALLS["reminded-update"][0]]), ["timestamps-by-script", "wanted-state"])

    def test_no_report_and_no_update_is_a_precondition_failure(self):
        got = failed("reminded-update", hook=False, after_tracker=SAME, calls=[helpers.CALLS["reminded-update"][0]])
        self.assertIn("reminder-delivered", got)
        self.assertIn("wanted-state", got)

    def test_a_blocking_hook_event_alone_is_not_delivery(self):
        # P3: Stop blocked (exit 2) but nothing reached the model: what a headless run that does not continue would show
        row, events, states = helpers.good("reminded-update", hook=False, after_tracker=SAME, calls=[helpers.CALLS["reminded-update"][0]])
        events.insert(1, {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2})
        results = {c: (ok, note) for c, ok, note in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["reminder-delivered"][0])
        self.assertIn("never reached the model", results["reminder-delivered"][1])

    def test_a_wrong_update_without_hook_evidence_is_a_failure_not_inconclusive(self):
        # P2: reminded-quiet, report not in the trace, the agent invents 2=done
        self.assertEqual(failed("reminded-quiet", hook=False, after_tracker=moved(tracker("stale-idle"), {2: "done"})), ["quiet-when-nothing-changed"])
        # P2b: reminded-update, the agent blocks item 2 and finishes item 3
        got = failed("reminded-update", hook=False, after_tracker=moved(tracker("stale-task"), {2: "blocked", 3: "done"}))
        self.assertNotIn("reminder-delivered", got)
        self.assertIn("wanted-state", got)

    def test_a_denied_call_that_was_retried_successfully_is_not_blocked(self):
        denial = {"tool_name": "Bash", "tool_use_id": "denied-1", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial])
        first = helpers.call("Bash", command=SET_2_DONE)
        first["message"]["content"][0]["id"] = "denied-1"
        events.insert(1, first)
        events.insert(2, helpers.tool_result("denied-1", "Permission to use Bash has been denied.", is_error=True))
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertTrue(results["not-permission-blocked"])
        retry_quoted = SET_2_DONE.replace(f'"{helpers.SCRIPT}"', str(helpers.SCRIPT))
        self.assertEqual(checks.script_key(SET_2_DONE), checks.script_key(retry_quoted))

    def test_a_denied_call_with_no_successful_retry_blocks(self):
        denial = {"tool_name": "Bash", "tool_use_id": "denied-1", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial], calls=[SKILL_CALL, ("Bash", {"command": SET_3_DOING})])
        denied = helpers.call("Bash", command=SET_2_DONE)
        denied["message"]["content"][0]["id"] = "denied-1"
        events.insert(1, denied)
        events.insert(2, helpers.tool_result("denied-1", "Permission to use Bash has been denied.", is_error=True))
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["not-permission-blocked"])


class MaskedWrites(unittest.TestCase):
    """R3-1: a failed or refused tracker write must not hide in the quiet, read and negative cases."""

    def verdict(self, expect, **override):
        row, events, states = helpers.good(expect, **override)
        return run_evals.grade_trial(row, events, states)["verdict"]

    def test_a_denied_invented_update_in_a_quiet_run_is_a_fail(self):
        denial = {"tool_name": "Bash", "tool_use_id": "d1", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("reminded-quiet", calls=[("Read", {"file_path": "/w/src/textutil.py"})], denials=[denial])
        call = helpers.call("Bash", command=SET_2_DONE)
        call["message"]["content"][0]["id"] = "d1"
        events.insert(2, call)
        events.insert(3, helpers.tool_result("d1", "Permission to use Bash has been denied.", is_error=True))
        got = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(got["no-write-attempt"])
        self.assertFalse(got["not-permission-blocked"])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

    def test_a_refused_invented_update_in_a_quiet_run_is_a_fail(self):
        self.assertEqual(failed(*MUTATIONS["no-write-attempt"][:1], **MUTATIONS["no-write-attempt"][1]), ["no-write-attempt"])
        self.assertEqual(self.verdict(MUTATIONS["no-write-attempt"][0], **MUTATIONS["no-write-attempt"][1]), "FAIL")

    def test_a_retitle_through_the_script_or_in_the_file_fails_the_quiet_run(self):
        retitle = f'python3 "{helpers.SCRIPT}" title "Other"'
        retitled = {**tracker("stale-idle"), "title": "Other"}
        self.assertEqual(failed("reminded-quiet", after_tracker=retitled), ["quiet-when-nothing-changed"])
        self.assertEqual(failed("reminded-quiet", after_tracker=retitled, calls=[("Bash", {"command": retitle})]), ["no-write-attempt", "quiet-when-nothing-changed"])

    def test_a_denied_init_in_a_negative_run_is_a_fail(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": INIT_EMPTY}}
        self.assertEqual(failed("none", calls=[("Bash", {"command": INIT_EMPTY})], denials=[denial], outputs={INIT_EMPTY: ("denied", True)}),
                         ["no-write-attempt", "not-permission-blocked"])
        self.assertEqual(self.verdict("none", calls=[("Bash", {"command": INIT_EMPTY})], denials=[denial], outputs={INIT_EMPTY: ("denied", True)}), "FAIL")
        self.assertEqual(failed("none", fixture="tracker", calls=[("Bash", {"command": SET_2_DONE})], outputs={SET_2_DONE: ("error", True)}), ["no-write-attempt"])

    def test_a_write_attempt_in_a_read_run_fails(self):
        self.assertEqual(failed("show", calls=helpers.CALLS["show"] + [("Bash", {"command": SET_2_DONE})], outputs={SET_2_DONE: ("error", True)}), ["no-write-attempt"])

    def test_reading_the_tracker_through_the_script_is_not_a_write_attempt(self):
        self.assertEqual(failed("reminded-quiet", calls=[("Bash", {"command": SHOW})]), [])
        self.assertEqual(failed("none", calls=[("Bash", {"command": f'python3 "{helpers.SCRIPT}" statusline'})]), [])

    def test_the_isolation_denial_and_attempt_checks_run_where_the_tracker_must_not_change(self):
        for expect in ("none", "reminded-quiet", "show"):
            self.assertIn("not-permission-blocked", run(expect), expect)
            self.assertIn("no-write-attempt", run(expect), expect)
        for expect in ("update", "create", "reminded-update"):
            self.assertNotIn("no-write-attempt", run(expect), expect)


class DenialShapes(unittest.TestCase):
    """R3-5: a denial that shows only as an is_error tool_result, or as an element without tool_input, is INCONCLUSIVE, not FAIL."""

    def grade(self, expect="update", **override):
        row, events, states = helpers.good(expect, **override)
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        return results, run_evals.grade_trial(row, events, states)["verdict"]

    def test_a_denial_seen_only_as_an_error_result_is_inconclusive(self):
        text = "Claude requested permissions to use Bash, but you haven't granted it yet."
        results, verdict = self.grade(after_tracker=SAME, outputs={SET_2_DONE: (text, True), SET_3_DOING: (text, True)})
        self.assertFalse(results["not-permission-blocked"])
        self.assertEqual(verdict, "INCONCLUSIVE")

    def test_a_denial_element_with_an_id_and_no_input_is_inconclusive(self):
        row, events, states = helpers.good("update", after_tracker=SAME)
        script_call = next(e for e in events if e.get("type") == "assistant" and "set 2" in str(e))
        ident = script_call["message"]["content"][0]["id"]
        events[-1]["permission_denials"] = [{"tool_name": "Bash", "tool_use_id": ident}]
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertFalse(results["not-permission-blocked"])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_a_denial_without_an_id_whose_own_result_is_an_error_and_a_successful_retry_is_a_pass(self):
        # Q5: the element names the command only; the first call's result is the denial (is_error), the identical retry succeeds
        denial = {"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial], calls=[SKILL_CALL, ("Bash", {"command": SET_2_DONE}), ("Bash", {"command": SET_2_DONE}),
                                                                           ("Bash", {"command": SET_3_DOING})])
        first = next(e for e in events if e.get("type") == "user" and e["message"]["content"][0].get("type") == "tool_result")
        first["message"]["content"][0].update(is_error=True, content="Permission to use Bash has been denied.")
        results = {c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}
        self.assertTrue(results["not-permission-blocked"])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "PASS")

    def test_an_error_result_that_is_not_a_denial_is_not_a_permission_block(self):
        results, verdict = self.grade(after_tracker=SAME, outputs={SET_2_DONE: ("error: no such item", True), SET_3_DOING: ("error: no such item", True)})
        self.assertTrue(results["not-permission-blocked"])
        self.assertEqual(verdict, "FAIL")

    def test_a_denied_call_retried_after_an_error_result_denial_is_cleared(self):
        text = "Permission to use Bash has been denied."
        row, events, states = helpers.good("update", calls=[SKILL_CALL, ("Bash", {"command": SET_2_DONE}), ("Bash", {"command": SET_2_DONE}), ("Bash", {"command": SET_3_DOING})])
        first = next(e for e in events if e.get("type") == "user" and e["message"]["content"][0].get("type") == "tool_result")
        first["message"]["content"][0].update(is_error=True, content=text)
        self.assertTrue({c: ok for c, ok, _ in checks.run_checks(checks.Case.build(row, events, states))}["not-permission-blocked"])


class VerdictAttribution(unittest.TestCase):
    """R3-4: a failed precondition masks only what it explains; a hand write of the tracker is the agent's own doing."""

    def grade(self, expect, **override):
        row, events, states = helpers.good(expect, **override)
        trial = run_evals.grade_trial(row, events, states)
        return trial["verdict"], sorted(c["id"] for c in trial["checks"] if not c["pass"])

    def test_a_denied_script_then_a_hand_write_is_a_fail(self):
        denial = {"tool_name": "Bash", "tool_use_id": "d1", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial], calls=[SKILL_CALL, ("Write", {"file_path": ".cache/milestones.json"})],
                                           after_tracker=moved(T, {2: "done", 3: "doing"}))
        denied = helpers.call("Bash", command=SET_2_DONE)
        denied["message"]["content"][0]["id"] = "d1"
        events.insert(2, denied)
        events.insert(3, helpers.tool_result("d1", "Permission to use Bash has been denied.", is_error=True))
        trial = run_evals.grade_trial(row, events, states)
        failing = {c["id"] for c in trial["checks"] if not c["pass"]}
        self.assertEqual(failing, {"not-permission-blocked", "no-direct-tracker-write"})
        self.assertEqual(trial["verdict"], "FAIL")

    def test_a_hand_write_before_stop_with_no_marker_is_a_fail(self):
        by_hand = moved(tracker("stale-task"), {2: "done"})
        by_hand["items"][1].pop("finished")
        by_hand["updated"] = tracker("stale-task")["updated"]
        verdict, failing = self.grade("reminded-update", hook=False, marker=False, after_tracker=by_hand, calls=[("Write", {"file_path": ".cache/milestones.json"})])
        self.assertEqual(verdict, "FAIL")
        self.assertIn("no-direct-tracker-write", failing)
        self.assertIn("reminder-fired", failing)

    def test_a_denial_alone_still_explains_the_missing_outcome(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}
        self.assertEqual(self.grade("update", denials=[denial], after_tracker=SAME, outputs={SET_2_DONE: ("denied", True)}),
                         ("INCONCLUSIVE", ["not-permission-blocked", "timestamps-by-script", "wanted-state"]))

    def test_a_denial_does_not_explain_a_commit_or_a_stray_file(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}
        got = self.grade("update", denials=[denial], after_tracker=SAME, outputs={SET_2_DONE: ("denied", True)}, committed=[".cache/milestones.json"])
        self.assertEqual(got[0], "FAIL")
        got = self.grade("update", denials=[denial], after_tracker=SAME, outputs={SET_2_DONE: ("denied", True)}, after_extra={"notes.md": "x"})
        self.assertEqual(got[0], "FAIL")

    def test_an_incomplete_run_explains_missing_facts_but_not_a_stray_file(self):
        self.assertEqual(self.grade("update", result_error="error_max_budget_usd", calls=[SKILL_CALL], after_tracker=SAME)[0], "INCONCLUSIVE")
        self.assertEqual(self.grade("update", result_error="error_max_budget_usd", calls=[SKILL_CALL], after_tracker=SAME, after_extra={"notes.md": "x"})[0], "FAIL")

    def test_a_missing_snapshot_is_inconclusive_in_every_case(self):
        # the snapshots are the evidence of the state checks: without them nothing is compared
        for expect in BARE_EXPECTS:
            row, events, states = helpers.good(expect)
            self.assertEqual(run_evals.grade_trial(row, events, {})["verdict"], "INCONCLUSIVE", expect)

    def test_every_precondition_has_an_explanation_over_known_checks(self):
        self.assertEqual({c for c, g in checks.GROUPS.items() if g == "precondition"}, set(checks.EXPLAINED_BY))
        for cid, explained in checks.EXPLAINED_BY.items():
            self.assertLessEqual(explained, set(checks.GROUPS), cid)
            self.assertFalse({c for c in explained if checks.GROUPS[c] == "precondition"}, cid)
        # the gates that catch the agent's own misbehaviour are never explained away
        for gate in ("no-direct-tracker-write", "no-write-attempt", "tracker-not-committed", "nothing-else-changed", "show-reads-only", "no-tracker-when-negative"):
            for cid, explained in checks.EXPLAINED_BY.items():
                self.assertNotIn(gate, explained, cid)


class ExplanationIsOnlyMissingEffects(unittest.TestCase):
    """R4-1: a denied call or a hook that never fired leaves changes OUT. It cannot rename or change an item the prompt did not name,
    corrupt a tracker or write a file, so such a failure is the agent's own doing: FAIL, never INCONCLUSIVE."""

    SET_4_DONE = f'python3 "{helpers.SCRIPT}" set 4 --state done'
    DENIED_TEXT = "Claude requested permissions to use Bash, but you haven't granted it yet."

    def denied_set_2(self, after_tracker, calls, expect="update", **override):
        """SET_2_DONE is denied (an element with its id, an is_error result); the other `calls` run."""
        denial = {"tool_name": "Bash", "tool_use_id": "d1", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good(expect, calls=calls, denials=[denial], after_tracker=after_tracker, **override)
        denied = helpers.call("Bash", command=SET_2_DONE)
        denied["message"]["content"][0]["id"] = "d1"
        events.insert(2, denied)
        events.insert(3, helpers.tool_result("d1", self.DENIED_TEXT, is_error=True))
        trial = run_evals.grade_trial(row, events, states)
        return trial["verdict"], sorted(c["id"] for c in trial["checks"] if not c["pass"])

    def test_probe_one_call_denied_another_lands_on_the_wrong_item_is_a_fail(self):
        verdict, failing = self.denied_set_2(moved(T, {3: "doing", 4: "done"}), [SKILL_CALL, ("Bash", {"command": SET_3_DOING}), ("Bash", {"command": self.SET_4_DONE})])
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(failing, ["not-permission-blocked", "others-untouched", "wanted-state"])
        # without the denial the same run is a FAIL too: the denial must not change the verdict
        row, events, states = helpers.good("update", calls=[SKILL_CALL, ("Bash", {"command": SET_3_DOING}), ("Bash", {"command": self.SET_4_DONE})],
                                           after_tracker=moved(T, {3: "doing", 4: "done"}))
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

    def test_probe_the_reminder_finishes_without_a_marker_and_with_a_wrong_write_is_a_fail(self):
        renamed = moved(tracker("stale-task"), {2: "done"})
        renamed["items"][0]["name"] = "Something else"
        row, events, states = helpers.good("reminded-update", hook=False, marker=False, after_tracker=renamed)
        trial = run_evals.grade_trial(row, events, states)
        failing = {c["id"] for c in trial["checks"] if not c["pass"]}
        self.assertLessEqual({"reminder-fired", "others-untouched"}, failing)
        self.assertEqual(trial["verdict"], "FAIL")

    def test_a_pure_denial_with_nothing_changed_stays_inconclusive(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": SET_2_DONE}}
        row, events, states = helpers.good("update", denials=[denial], after_tracker=SAME, outputs={SET_2_DONE: (self.DENIED_TEXT, True)})
        trial = run_evals.grade_trial(row, events, states)
        self.assertEqual(trial["verdict"], "INCONCLUSIVE")
        self.assertEqual(sorted(c["id"] for c in trial["checks"] if not c["pass"]), ["not-permission-blocked", "timestamps-by-script", "wanted-state"])

    def test_a_hook_that_never_fired_with_nothing_changed_stays_inconclusive(self):
        row, events, states = helpers.good("reminded-update", hook=False, marker=False, after_tracker=SAME, calls=[helpers.CALLS["reminded-update"][0]])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")

    def test_a_denied_create_that_made_no_tracker_is_inconclusive_but_a_bad_one_is_a_fail(self):
        denial = {"tool_name": "Bash", "tool_input": {"command": INIT_EMPTY}}
        calls = [SKILL_CALL, ("Bash", {"command": INIT_EMPTY})]
        row, events, states = helpers.good("create", calls=calls, denials=[denial], after_tracker=NOTHING, outputs={INIT_EMPTY: (self.DENIED_TEXT, True)})
        trial = run_evals.grade_trial(row, events, states)
        self.assertEqual(sorted(c["id"] for c in trial["checks"] if not c["pass"]), ["not-permission-blocked", "tracker-created", "tracker-valid"])
        self.assertEqual(trial["verdict"], "INCONCLUSIVE")
        # a tracker that exists but is wrong or invalid was written: not explained by the denial
        for bad in ({**NEW_TRACKER, "title": "Wrong"}, "not json"):
            row, events, states = helpers.good("create", calls=calls, denials=[denial], after_tracker=bad, outputs={INIT_EMPTY: (self.DENIED_TEXT, True)})
            self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL", bad)

    def test_a_denied_update_that_corrupted_a_valid_tracker_is_a_fail(self):
        corrupt = moved(T, {3: "doing"})
        corrupt["items"][0].pop("name")
        verdict, failing = self.denied_set_2(corrupt, [SKILL_CALL, ("Bash", {"command": SET_3_DOING})])
        self.assertEqual(verdict, "FAIL")
        self.assertIn("tracker-valid", failing)

    def test_the_table_explains_only_missing_effects(self):
        # the failures that need an actual wrong write are explained by no precondition, with or without a case
        needs_a_write = {"others-untouched", "nothing-else-changed", "no-direct-tracker-write", "no-write-attempt", "tracker-not-committed",
                         "written-by-script", "quiet-when-nothing-changed", "show-reads-only", "no-tracker-when-negative"}
        for cid, explained in checks.EXPLAINED_BY.items():
            self.assertFalse(needs_a_write & explained, cid)
        for cid in ("not-permission-blocked", "reminder-fired", "reminder-delivered", "plugin-under-test-isolated"):
            self.assertEqual(checks.EXPLAINED_BY[cid] & checks.SNAPSHOT_CHECKS, set(), cid)
            self.assertFalse(checks.EXPLAINED_BY[cid] - {"wanted-state", "shown-by-script", "skill-loaded", "skill-trigger"}, cid)

    def test_the_conditional_explanations_need_an_unchanged_tracker_or_a_missing_snapshot(self):
        row, events, states = helpers.good("update", after_tracker=SAME)
        unchanged = checks.Case.build(row, events, states)
        row, events, states = helpers.good("update")
        written = checks.Case.build(row, events, states)
        for pre in (["not-permission-blocked"], ["reminder-fired"], ["reminder-delivered"], ["plugin-under-test-isolated"], ["agent-completed"]):
            self.assertLessEqual(checks.IF_UNCHANGED, checks.explained_failures(pre, unchanged), pre)
            self.assertFalse(checks.IF_UNCHANGED & checks.explained_failures(pre, written), pre)
            self.assertFalse(checks.IF_UNCHANGED & checks.explained_failures(pre), pre)         # no case: the strict reading
            self.assertNotIn("others-untouched", checks.explained_failures(pre, unchanged), pre)
        incomplete = checks.Case.build(row, events, {})
        self.assertLessEqual(checks.SNAPSHOT_CHECKS, checks.explained_failures(["agent-completed"], incomplete))
        self.assertFalse(checks.SNAPSHOT_CHECKS - checks.IF_UNCHANGED & checks.explained_failures(["not-permission-blocked"], incomplete))


class AgentCompleted(unittest.TestCase):
    def grade(self, expect, mutate, **override):
        row, events, states = helpers.good(expect, **override)
        events, states, note = mutate(events, states, "")
        return {c: (ok, n) for c, ok, n in checks.run_checks(checks.Case.build(row, events, states, note))}

    def keep(self, events, states, note):
        return events, states, note

    def test_a_good_run_completed(self):
        self.assertTrue(self.grade("update", self.keep)["agent-completed"][0])

    def test_a_trace_with_only_the_init_event_did_not_complete_in_any_negative_case(self):
        # M1: [init] alone, before == after: every other check of a none case passes on it
        for fixture in ("bare", "tracker"):
            with self.subTest(fixture=fixture):
                results = self.grade("none", lambda e, s, n: (e[:1], s, n), fixture=fixture)
                self.assertFalse(results["agent-completed"][0])
                self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), ["agent-completed"])

    def test_an_error_result_did_not_complete(self):
        results = self.grade("none", self.keep, result_error="error_max_budget_usd")
        self.assertFalse(results["agent-completed"][0])
        self.assertIn("error_max_budget_usd", results["agent-completed"][1])

    def test_a_missing_result_event_and_a_run_note_did_not_complete(self):
        self.assertIn("no result event", self.grade("none", lambda e, s, n: (e[:-1], s, n))["agent-completed"][1])
        self.assertIn("exited 124", self.grade("none", lambda e, s, n: (e, s, "agent command exited 124: timed out"))["agent-completed"][1])

    def test_an_empty_final_reply_is_a_completed_run(self):
        # R2-2 / P1: the Stop hook asks for a silent update and "if nothing changed, do nothing": the last turn may carry no text
        for expect in ("reminded-update", "reminded-quiet", "update", "none"):
            with self.subTest(expect=expect):
                results = self.grade(expect, self.keep, reply="")
                self.assertTrue(results["agent-completed"][0], expect)
                self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), [], expect)

    def test_a_real_failure_is_not_hidden_behind_an_empty_reply(self):
        # R2-2 / Q6: reminded-quiet, the agent invents `set 2 --state done`, reply ''
        invented = moved(tracker("stale-idle"), {2: "done"})
        results = self.grade("reminded-quiet", self.keep, reply="", after_tracker=invented, calls=[("Bash", {"command": SET_2_DONE})])
        self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), ["no-write-attempt", "quiet-when-nothing-changed"])
        # Q7: reminded-update, the agent writes the tracker by hand without timestamps, reply ''
        by_hand = moved(tracker("stale-task"), {2: "done"})
        by_hand["items"][1].pop("finished")
        by_hand["updated"] = tracker("stale-task")["updated"]
        results = self.grade("reminded-update", self.keep, reply="", after_tracker=by_hand, calls=[("Write", {"file_path": ".cache/milestones.json"})])
        self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), ["no-direct-tracker-write", "timestamps-by-script", "written-by-script"])
        # Q8: the same for the update case
        results = self.grade("update", self.keep, reply="", after_tracker=moved(T, {2: "done", 3: "doing"}),
                             calls=[SKILL_CALL, ("Write", {"file_path": ".cache/milestones.json"})])
        self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), ["no-direct-tracker-write", "written-by-script"])
        row, events, states = helpers.good("reminded-quiet", reply="", after_tracker=invented, calls=[("Bash", {"command": SET_2_DONE})])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "FAIL")

    def test_a_missing_or_empty_snapshot_did_not_complete(self):
        # M2: no state.json: both snapshots are absent and every state check would compare None with None
        for states in ({}, {"before": {}, "after": {}}):
            results = self.grade("none", lambda e, s, n, states=states: (e, states, n), fixture="tracker")
            self.assertFalse(results["agent-completed"][0])
            self.assertIn("snapshot", results["agent-completed"][1])


class Soft(unittest.TestCase):
    def test_the_prose_check_is_the_only_soft_check(self):
        self.assertEqual({c for c, g in checks.GROUPS.items() if g == "soft"}, {"reply-names-current"})

    def test_a_wrong_reply_alone_is_a_pass_with_a_warning(self):
        for reply in ("Here is the tracker.", "Storage layer is done."):
            row, events, states = helpers.good("show", reply=reply)
            trial = run_evals.grade_trial(row, events, states)
            self.assertEqual(trial["verdict"], "PASS", reply)
            self.assertEqual(trial["warnings"], ["reply-names-current"], reply)
            self.assertFalse(next(c for c in trial["checks"] if c["id"] == "reply-names-current")["pass"])

    def test_a_clean_run_has_no_warning(self):
        row, events, states = helpers.good("show")
        self.assertEqual(run_evals.grade_trial(row, events, states)["warnings"], [])

    def test_a_warning_never_masks_or_makes_a_failure(self):
        row, events, states = helpers.good("show", reply="Here is the tracker.", after_tracker=moved(T, {2: "done"}))
        trial = run_evals.grade_trial(row, events, states)
        self.assertEqual((trial["verdict"], trial["warnings"]), ("FAIL", ["reply-names-current"]))
        row, events, states = helpers.good("show", reply="Here is the tracker.", entries=[INLINE, INSTALLED])
        self.assertEqual(run_evals.grade_trial(row, events, states)["verdict"], "INCONCLUSIVE")


class Groups(unittest.TestCase):
    def test_every_group_is_used_and_known(self):
        self.assertEqual(set(checks.GROUPS.values()), {"precondition", "process", "gate", "outcome", "soft"})


if __name__ == "__main__":
    unittest.main()
