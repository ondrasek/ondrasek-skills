"""Every check has a passing trace and a failing one. A test fails when a check id has no failing trace (see test_every_check_is_mutated)."""
import unittest

import helpers
import checks
import run_evals
from helpers import NOTHING, SAME, SET_2_DONE, SET_3_DOING, SHOW, SKILL_ID, moved, tracker

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
                                                  "tracker-not-committed", "nothing-else-changed"]))
        self.assertTrue(all(ok for ok, _ in results.values()))

    def test_a_negative_on_a_bare_fixture_runs_the_bare_branch(self):
        self.assertEqual(sorted(run("none")), sorted(["agent-completed", "plugin-under-test-isolated", "no-tracker-when-negative", "skill-loaded", "skill-trigger",
                                                      "no-direct-tracker-write", "tracker-not-committed", "nothing-else-changed"]))

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

    def test_the_script_regex(self):
        for command in ['python3 "/p/skills/milestones/scripts/milestones.py" show', "python3 /p/milestones.py show --json",
                        "cd /w && python3 '/p/milestones.py' set 2 --state done", "python3 milestones.py init --empty --title X"]:
            self.assertTrue(checks.SCRIPT_RE.search(command), command)
        for command in ["cat .cache/milestones.json", "python3 other.py show", "python3 /p/milestones.py statusline", "python3 /p/milestones.py"]:
            self.assertFalse(checks.SCRIPT_RE.search(command), command)

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
        want = "4=blocked,4.needs=you"
        blocked = moved(T, {4: "blocked"})
        for value, bad in (("legal", True), ("", True), ("you", False), ("You", False), ("author", False), ("me", False), ("you (the key from legal)", False), ("member", True)):
            blocked["items"][3]["needs"] = value
            self.assertEqual(failed("update", want=want, after_tracker=blocked), ["wanted-state"] if bad else [], value)

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
        self.assertEqual(sorted(c for c, (ok, _) in results.items() if not ok), ["quiet-when-nothing-changed"])
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


class Groups(unittest.TestCase):
    def test_every_group_is_used_and_known(self):
        self.assertEqual(set(checks.GROUPS.values()), {"precondition", "process", "gate", "outcome"})


if __name__ == "__main__":
    unittest.main()
