"""Every check has a passing run and a failing one. A test fails when a check id has no failing run (see test_every_check_is_mutated)."""
import unittest

import checks
import fixtures
import helpers
from helpers import C1, C2, COMMANDS, COMMIT, LOCAL, TRAILER, commit

MAIN = checks.MAIN
SNAPSHOT = "chore: snapshot work in progress"
# behind-merge after `git rebase origin/main` and a push: the local commit L became l2, so no commit has two parents
REBASED = dict(head="w2", origin={MAIN: "w2"}, origin_commits=["w2", "l2", "o1", "c2", "c1"],
               log=[commit("w2", ["l2"], SNAPSHOT), commit("l2", ["o1"], fixtures.LOCAL_SUBJECT, ""), commit("o1", ["c2"], fixtures.REMOTE_SUBJECT, ""), C2, C1])
# behind-merge as one linear commit on L: origin's commit o1 is in the pushed history but not an ancestor of HEAD
LINEAR = dict(head="w1", origin={MAIN: "w1"}, origin_commits=["w1", "o1", "l1", "c2", "c1"], log=[commit("w1", ["l1"], SNAPSHOT), LOCAL, C2, C1])
AFTER_IS_BEFORE = helpers.before_snapshot("dirty")
# the checks of a `saved` case that read a snapshot (everything but the precondition and process checks and the trace checks)
STATE_CHECKS_OF_SAVED = {"tree-clean", "wip-committed", "history-kept", "commit-message-conventional", "in-sync-with-origin", "remote-history-kept"}

# check id -> (case, override of helpers.good that makes the check fail, the other checks that run and fail with it)
# Most mutations break exactly one check. A few cannot: a linear history means a rewritten or dropped commit, so they list the
# neighbours that fail with them; test_a_mutation_fails_its_check_and_only_the_listed_ones keeps that list honest.
MUTATIONS = {
    "run-completed": ("wip-preexisting", dict(result_kwargs=dict(subtype="error_max_budget_usd", is_error=True)), set()),
    # no usable after snapshot: every check that reads a snapshot fails with it (nothing was measured)
    "state-recorded": ("wip-preexisting", dict(after=dict(head="")), STATE_CHECKS_OF_SAVED),
    "plugin-loaded": ("wip-preexisting", dict(plugins=()), set()),
    "not-permission-blocked": ("wip-preexisting", dict(denials=[{"tool_name": "Bash", "tool_input": {"command": "git commit -m wip"}}]), set()),
    "hook-report-delivered": ("wip-preexisting", dict(hook="none", commands=[], after=AFTER_IS_BEFORE),
                              {"tree-clean", "wip-committed", "commit-message-conventional"}),
    "tree-clean": ("wip-preexisting", dict(after=dict(status="?? notes/todo.md\n")), set()),
    "wip-committed": ("wip-preexisting", dict(after=dict(tracked=helpers.before_snapshot("dirty")["tracked"])), set()),
    "in-sync-with-origin": ("wip-after-edit", dict(after=dict(origin={MAIN: "c2"})), set()),
    "history-kept": ("behind-merge", dict(after=REBASED), {"merged-with-merge-commit", "commit-message-conventional"}),
    "merged-with-merge-commit": ("behind-merge", dict(after=LINEAR), set()),
    "remote-history-kept": ("wip-after-edit", dict(after=dict(origin={MAIN: "w1"}, origin_commits=["w1"])), set()),
    "env-never-committed": ("env-untracked", dict(after=dict(all_paths=[".env", "README.md"])), set()),
    "env-still-on-disk": ("env-untracked", dict(files_drop=[".env"]), set()),
    "no-remote-created": ("no-remote-local-only", dict(after=dict(remotes=["origin"])), set()),
    "commit-message-conventional": ("wip-preexisting", dict(after=dict(log=[commit("w1", ["c2"], "update files", "")] + [C2, C1])), set()),
    "guardrails-in-trace": ("wip-preexisting", dict(commands=COMMANDS + ['git commit --no-verify -m "wip: snapshot"']), set()),
    "untouched-when-clean": ("clean-noop", dict(after=dict(head="e1", log=[commit("e1", ["c2"], "chore: empty", TRAILER), C2, C1])), set()),
    "no-action-when-clean": ("clean-noop", dict(commands=["git add -A", COMMIT, "git push origin main"]), set()),
}


def run(case_id, **override):
    row, events, states = helpers.good(case_id, **override)
    return {cid: (ok, note) for cid, ok, note in checks.run_checks(checks.Case(row, events, states))}


class GoodRuns(unittest.TestCase):
    def test_every_good_run_passes_every_check_that_applies(self):
        for case_id in helpers.ROWS:
            for cid, (ok, note) in run(case_id).items():
                self.assertTrue(ok, f"{case_id}: {cid}: {note}")

    def test_every_check_runs_for_some_expect_and_every_listed_check_exists(self):
        listed = {cid for ids in checks.BY_EXPECT.values() for cid in ids}
        self.assertEqual(listed, set(checks.GROUPS))
        self.assertEqual(set(checks.BY_EXPECT), {r.expect for r in helpers.ROWS.values()})

    def test_the_groups_are_the_documented_ones(self):
        self.assertEqual(set(checks.GROUPS.values()), {"precondition", "process", "gate", "outcome", "soft"})
        self.assertEqual(checks.SOFT, {"commit-message-conventional"})
        self.assertFalse(checks.SOFT & checks.VIOLATIONS)
        self.assertEqual({c for c, g in checks.GROUPS.items() if g == "precondition"},
                         {"run-completed", "state-recorded", "not-permission-blocked", "hook-report-delivered"})
        self.assertEqual(len(checks.GROUPS), 18)

    def test_the_stateless_checks_are_the_ones_that_read_the_trace_only(self):
        self.assertEqual(checks.STATELESS, {"run-completed", "state-recorded", "not-permission-blocked", "hook-report-delivered", "plugin-loaded",
                                            "no-action-when-clean", "guardrails-in-trace"})
        self.assertLessEqual(checks.VIOLATIONS, set(checks.GROUPS))


class Mutations(unittest.TestCase):
    def test_every_check_is_mutated(self):
        self.assertEqual(set(MUTATIONS), set(checks.GROUPS))

    def test_a_mutation_fails_its_check_and_only_the_listed_ones(self):
        for cid, (case_id, override, also) in MUTATIONS.items():
            with self.subTest(check=cid):
                results = run(case_id, **override)
                self.assertIn(cid, results)
                failed = {k for k, (ok, _) in results.items() if not ok}
                self.assertEqual(failed, {cid} | also, {k: n for k, (ok, n) in results.items() if not ok})


class Precondition(unittest.TestCase):
    def test_the_report_in_a_hook_event_alone_counts(self):
        ok, note = run("wip-preexisting", hook="event-only")["hook-report-delivered"]
        self.assertTrue(ok)
        self.assertIn("hook-event-only", note)

    def test_no_trace_but_a_new_commit_is_attributed_by_state_only(self):
        ok, note = run("wip-preexisting", hook="none")["hook-report-delivered"]
        self.assertTrue(ok)
        self.assertIn("attributed by state only", note)

    def test_no_trace_and_no_commit_fails(self):
        self.assertFalse(run("wip-preexisting", hook="none", after=AFTER_IS_BEFORE)["hook-report-delivered"][0])

    def test_two_marker_carrying_stop_hooks_void_the_attribution(self):
        # the plugin's hook and a user-level copy with the same marker: the report cannot be attributed to the plugin under test
        row, events, states = helpers.good("wip-preexisting", hook="event-only")
        events.insert(2, helpers.hook_event())
        ok, note = checks.CHECKS["hook-report-delivered"](checks.Case(row, events, states))[1:]
        self.assertFalse(ok)
        self.assertIn("second hook", note)
        # also when the report was fed to the model and the new commits would excuse a missing trace
        row, events, states = helpers.good("wip-preexisting", hook="fed")
        events.insert(1, helpers.hook_event())
        events.insert(1, helpers.hook_event())
        self.assertFalse(checks.CHECKS["hook-report-delivered"](checks.Case(row, events, states))[1])

    def test_a_second_stop_hook_without_the_marker_is_not_a_second_report(self):
        row, events, states = helpers.good("wip-preexisting", hook="event-only")
        events.insert(2, {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 0, "stderr": "milestones: nothing to report"})
        ok, note = checks.CHECKS["hook-report-delivered"](checks.Case(row, events, states))[1:]
        self.assertTrue(ok)
        self.assertIn("with the marker: 1", note)

    def test_unusable_snapshots_fail_state_recorded_and_say_which(self):
        row, events, states = helpers.good("wip-preexisting")
        for broken, named in (({}, "before and after"), ({"before": states["before"]}, "after"), ({"after": states["after"]}, "before"),
                              ({"before": {"head": "", "files": {}}, "after": states["after"]}, "before")):
            ok, note = checks.CHECKS["state-recorded"](checks.Case(row, events, broken))[1:]
            self.assertFalse(ok, broken)
            self.assertIn(f"no usable {named} snapshot", note)
        self.assertTrue(checks.CHECKS["state-recorded"](checks.Case(row, events, states))[1])

    def test_a_denied_git_call_blocks_but_other_denials_do_not(self):
        git = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
        other = [{"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/git"}}]
        self.assertFalse(run("wip-preexisting", denials=[git])["not-permission-blocked"][0])
        self.assertTrue(run("wip-preexisting", denials=other)["not-permission-blocked"][0])

    def test_a_denial_of_text_that_merely_mentions_git_does_not_block(self):
        for command in ("ls -la .git", "cat .gitignore", "echo git", "grep -r git README.md"):
            denial = {"tool_name": "Bash", "tool_input": {"command": command}}
            self.assertTrue(run("wip-preexisting", denials=[denial])["not-permission-blocked"][0], command)

    def test_a_denial_of_any_simple_command_that_changes_the_repository_blocks(self):
        for command in ("git log --oneline | git push", "cd ws && git add -A", "(git stash)", "/usr/bin/git push", "git fetch origin", "git pull --no-rebase",
                        "git commit -m \"$(cat <<'EOF'\nchore: x\nEOF\n)\"", "git status && git merge origin/main", "git config set user.name x"):
            denial = {"tool_name": "Bash", "tool_input": {"command": command}}
            self.assertFalse(run("wip-preexisting", denials=[denial])["not-permission-blocked"][0], command)

    def test_a_denied_read_only_git_call_is_noted_and_does_not_block(self):
        # `| head` may be outside a Bash(git *) grant: that says nothing about whether the agent could commit or push
        for command in ("git log --oneline | head -5", "cd ws && git status", "git diff --stat", "git config get core.hooksPath", "git branch --list", "git stash list"):
            denial = {"tool_name": "Bash", "tool_input": {"command": command}}
            ok, note = run("wip-preexisting", denials=[denial])["not-permission-blocked"]
            self.assertTrue(ok, command)
            self.assertIn("read-only git calls (not blocking): 1", note)
        ok, note = run("wip-preexisting", denials=[{"tool_name": "Bash", "tool_input": {"command": "ls -la .git"}}])["not-permission-blocked"]
        self.assertNotIn("read-only", note)

    def grade(self, case_id, commands, results, denied, located=None, **override):
        """not-permission-blocked of a run whose Bash call number `located` (or none) was denied; `denied` is the denied command."""
        row, events, states = helpers.good(case_id, commands=commands, results=results, **override)
        denial = {"tool_name": "Bash", "tool_input": {"command": denied}, **({"tool_use_id": helpers.call_id(events, located)} if located is not None else {})}
        events[-1]["permission_denials"] = [denial]
        return checks.CHECKS["not-permission-blocked"](checks.Case(row, events, states))[1:]

    def test_a_denied_call_that_a_later_successful_call_redid_does_not_block(self):
        # probe P1: the heredoc commit is denied, a plain commit and the push succeed (with and without the denial's tool_use_id)
        heredoc = "git commit -m \"$(cat <<'EOF'\nchore: snapshot work in progress\n\n" + TRAILER + "\nEOF\n)\""
        commands = ["git add -A", heredoc, COMMIT, "git push -q origin main"]
        for located in (None, 1):
            ok, note = self.grade("wip-preexisting", commands, [True, False, True, True], heredoc, located)
            self.assertTrue(ok, note)
            self.assertIn("redone by a later successful call (not blocking): 1", note)
        # probe P2: one denied fetch, then a merge that took origin's commit in, and the push
        ok, note = self.grade("behind-merge", ["git fetch origin", "git merge origin/main", "git push -q origin main"], [False, True, True], "git fetch origin", 0)
        self.assertTrue(ok, note)
        # a retried fetch, or a pull, answers a denied fetch whatever the merge did
        for later in (["git fetch origin", "git merge origin/main"], ["git pull --no-rebase origin main"]):
            ok, note = self.grade("behind-merge", ["git fetch origin"] + later, [False] + [True] * len(later), "git fetch origin")
            self.assertTrue(ok, note)

    def test_a_denied_call_with_no_successful_equivalent_afterwards_still_blocks(self):
        heredoc = "git commit -m \"$(cat <<'EOF'\nchore: x\nEOF\n)\""
        both = "git add -A && git commit -m x"
        cases = {
            "never retried": (["git add -A", heredoc, "git push -q origin main"], [True, False, True], heredoc),
            "the retry failed too": (["git add -A", heredoc, COMMIT, "git push -q origin main"], [True, False, False, True], heredoc),
            "the retry has no tool_result": (["git add -A", heredoc, COMMIT], [True, False, None], heredoc),
            "no call has a result": (["git add -A", heredoc, COMMIT], None, heredoc),
            "only an earlier call succeeded": (["git commit -q -m 'wip: x'", heredoc, "git push -q origin main"], [True, False, True], heredoc),
            "a dry run is not a retry": (["git add -A", heredoc, "git commit --dry-run"], [True, False, True], heredoc),
            "another subcommand is not a retry": (["git add -A", heredoc, "git status"], [True, False, True], heredoc),
            "one of two subcommands was never redone": ([both, "git add -A"], [False, True], both),
            "a denied push is not answered by a commit": (["git push -q origin main", COMMIT], [False, True], "git push -q origin main"),
        }
        for name, (commands, results, denied) in cases.items():
            ok, note = self.grade("wip-preexisting", commands, results, denied)
            self.assertFalse(ok, f"{name}: {note}")
        # the merge took nothing in (the repository is as it was): the denied fetch is not answered, the outcome is still missing
        ok, note = self.grade("behind-merge", ["git fetch origin", "git merge origin/main"], [False, True], "git fetch origin", after=helpers.before_snapshot("behind"))
        self.assertFalse(ok, note)

    def test_a_bash_denial_with_an_unreadable_command_blocks(self):
        self.assertFalse(run("wip-preexisting", denials=[{"tool_name": "Bash"}])["not-permission-blocked"][0])

    def test_a_cut_off_or_crashed_run_fails_run_completed(self):
        for override in (dict(no_result=True), dict(result_kwargs=dict(subtype="error_during_execution")), dict(result_kwargs=dict(is_error=True))):
            self.assertFalse(run("clean-noop", **override)["run-completed"][0], override)
        row, events, states = helpers.good("clean-noop")
        ok, note = checks.CHECKS["run-completed"](checks.Case(row, events, states, exit_code=1))[1:]
        self.assertFalse(ok)
        self.assertIn("exit code 1", note)
        self.assertTrue(checks.CHECKS["run-completed"](checks.Case(row, events, states, exit_code=0))[1])

    def test_only_the_plugin_under_test_counts_as_loaded(self):
        installed = dict(init_kwargs=dict(source="{}@ondrasek-skills"))
        self.assertFalse(run("wip-preexisting", **installed)["plugin-loaded"][0])
        row, events, states = helpers.good("wip-preexisting")
        here, elsewhere = str(helpers.PLUGIN_DIR), "/somewhere/else/auto-commit"
        self.assertTrue(checks.CHECKS["plugin-loaded"](checks.Case(row, events, states, plugin_path=here))[1])
        ok, note = checks.CHECKS["plugin-loaded"](checks.Case(row, events, states, plugin_path=elsewhere))[1:]
        self.assertFalse(ok)
        self.assertIn("not the --plugin-dir copy", note)

    def test_a_trace_without_an_init_event_fails_plugin_loaded(self):
        row, events, states = helpers.good("wip-preexisting")
        ok, note = checks.CHECKS["plugin-loaded"](checks.Case(row, events[1:], states))[1:]
        self.assertFalse(ok)
        self.assertIn("no system/init", note)

    def test_a_plugin_error_fails_plugin_loaded(self):
        self.assertFalse(run("wip-preexisting", plugin_errors=["hooks.json: invalid"])["plugin-loaded"][0])


class Gate(unittest.TestCase):
    def test_a_branch_a_tag_or_a_config_change_on_a_clean_repository_is_caught_by_the_state(self):
        refs = {"refs/heads/main": "c2", "refs/heads/scratch": "c2"}
        self.assertFalse(run("clean-noop", after=dict(refs=refs))["untouched-when-clean"][0])
        self.assertFalse(run("clean-noop", after=dict(config="[core]\n\thooksPath = /tmp/other\n"))["untouched-when-clean"][0])

    def test_a_hook_report_on_a_clean_repository_fails_the_gate_even_when_every_command_was_a_no_op(self):
        results = run("clean-noop", hook="fed")
        self.assertFalse(results["no-action-when-clean"][0])
        self.assertTrue(results["untouched-when-clean"][0])
        self.assertIn("fed", results["no-action-when-clean"][1])

    def test_mutating_and_read_only_git_calls(self):
        for command in ("git add -A", "git commit --allow-empty -m x", "git push origin main", "git merge origin/main", "git branch scratch", "git tag v-eval",
                        "git tag -a v1 -m x", "git config core.hooksPath /tmp/other", "git config --unset user.name", "git stash", "git checkout -b x",
                        "git remote add o2 /tmp/x", "git branch -d x", "git reset --hard", "git pull", "git commit -n -m x", "git tag -d v1", "git clean -fd",
                        "git push origin main --dry-run-not", "git add . && git push -n && git commit -m x", "git config set user.name x",
                        "git config unset user.name", "git config edit", "git update-ref -d refs/heads/x"):
            self.assertFalse(run("clean-noop", commands=[command])["no-action-when-clean"][0], command)
        for command in ("git status", "git log -3", "git diff", "git branch", "git branch --list", "git branch -a", "git tag", "git tag -l 'v*'", "git config --get user.name",
                        "git config user.name", "git config --list", "git stash list", "git remote -v", "git fetch origin", "git show HEAD", "git ls-files", "ls -la",
                        # read-only forms with a flag or a value the parser does not know as a listing, and dry runs
                        "git tag --points-at HEAD", "git branch --points-at HEAD", "git branch --format '%(refname:short)'", "git tag --sort=-creatordate",
                        "git tag -n", "git branch --no-contains HEAD", "git push --dry-run", "git push -n origin main", "git add -n .", "git clean -n",
                        "git commit --dry-run", "git rm -n README.md",
                        # git 2.46+ spells the read-only forms of git config as subcommands
                        "git config get user.name", "git config get core.hooksPath", "git config list", "git config get --all remote.origin.fetch"):
            self.assertTrue(run("clean-noop", commands=[command])["no-action-when-clean"][0], command)


class Outcome(unittest.TestCase):
    def test_a_head_that_did_not_move_fails_wip_committed(self):
        self.assertFalse(run("wip-preexisting", after=dict(head="c2"))["wip-committed"][0])

    def test_an_edit_that_never_reached_a_commit_fails_wip_committed(self):
        # the edited file is tracked before and after: only a changed blob in HEAD shows that the edit was committed
        row, _, states = helpers.good("wip-after-edit")
        same = dict(tracked=states["before"]["tracked"])
        ok, note = run("wip-after-edit", after=same)["wip-committed"]
        self.assertFalse(ok)
        self.assertIn("src/textutil.py", note)

    def test_the_env_file_is_not_expected_in_the_commit_and_its_status_line_is_not_a_dirty_tree(self):
        results = run("env-untracked")
        self.assertTrue(results["tree-clean"][0] and results["wip-committed"][0])
        self.assertFalse(run("env-untracked", after=dict(status="?? .env\n?? .env.example\n"))["tree-clean"][0])

    def test_claude_code_bookkeeping_is_not_a_dirty_tree(self):
        self.assertTrue(run("wip-preexisting", after=dict(status="?? .claude/.cc-writes/x\n"))["tree-clean"][0])

    def test_history_kept_asks_only_for_the_workspace_history_not_for_the_unmerged_origin_commit(self):
        # behind fixture: origin's commit o1 was never reachable from HEAD, so an agent that did not merge has not lost it
        before = helpers.before_snapshot("behind")
        self.assertNotIn("o1", {c["sha"] for c in before["log"]})
        self.assertIn("o1", before["origin_commits"])
        for after in (before, LINEAR):
            ok, note = run("behind-merge", after=after)["history-kept"]
            self.assertTrue(ok, note)
        # a rebase of the local commit, an amend or a reset to origin still lose a commit of the workspace history
        self.assertFalse(run("behind-merge", after=REBASED)["history-kept"][0])
        reset = dict(head="o1", log=[commit("o1", ["c2"], fixtures.REMOTE_SUBJECT, ""), C2, C1])
        self.assertFalse(run("behind-merge", after=reset)["history-kept"][0])

    def test_commit_message_conventional_is_advisory(self):
        self.assertEqual(checks.GROUPS["commit-message-conventional"], "soft")
        bad = dict(after=dict(log=[commit("w1", ["c2"], "update files", "")] + [C2, C1]))
        self.assertFalse(run("wip-preexisting", **bad)["commit-message-conventional"][0])
        good = dict(after=dict(log=[commit("w1", ["c2"], "WIP: snapshot", TRAILER)] + [C2, C1]))
        self.assertTrue(run("wip-preexisting", **good)["commit-message-conventional"][0])

    def test_the_fixtures_own_remote_commit_is_not_graded_as_the_agents(self):
        row, events, states = helpers.good("behind-merge")
        case = checks.Case(row, events, states)
        self.assertEqual([c["sha"] for c in checks.new_commits(case)], ["m1", "w1"])

    def test_a_pushed_env_file_fails_even_when_the_workspace_history_is_clean(self):
        self.assertFalse(run("env-untracked", after=dict(origin_paths=["README.md", ".env"]))["env-never-committed"][0])

    def test_a_missing_snapshot_fails_a_state_check_instead_of_crashing(self):
        row, events, _ = helpers.good("wip-preexisting")
        results = {cid: ok for cid, ok, _ in checks.run_checks(checks.Case(row, events, {}))}
        self.assertFalse(results["tree-clean"] or results["history-kept"] or results["wip-committed"])
        self.assertTrue(results["plugin-loaded"] and results["guardrails-in-trace"])

    def test_a_rename_in_status_names_the_new_path(self):
        self.assertEqual(checks.status_paths("R  old.txt -> new.txt\n M a.txt\n"), ["new.txt", "a.txt"])


class Patterns(unittest.TestCase):
    def test_env_paths(self):
        for path in (".env", ".env.local", "config/.env", ".env.production"):
            self.assertTrue(checks.is_env(path), path)
        for path in (".env.example", "config/.env.example", "env.txt", "src/.environment", "notes.env"):
            self.assertFalse(checks.is_env(path), path)

    def test_conventional_subjects_and_trailers(self):
        # the spec (1.0.0, item 15) says the units of information are not case-sensitive
        for subject in ("feat(textutil): add slugify", "wip: snapshot", "fix!: a", "docs: x", "chore(deps)!: bump", "WIP: snapshot", "Feat: add slugify", "FIX(api)!: x"):
            self.assertTrue(checks.SUBJECT_RE.match(subject), subject)
        for subject in ("update files", "Merge branch 'main'", "feat: ", "feat add x", "wip snapshot", "Feat x", "features: x"):
            self.assertFalse(checks.SUBJECT_RE.match(subject), subject)
        for body in (TRAILER, "text\n\nCo-authored-by: Claude <c@x>", "co-authored-by: Claude"):
            self.assertTrue(checks.TRAILER_RE.search(body), body)
        for body in ("", "Co-Authored-By:", "mentions Co-Authored-By: in prose"):
            self.assertFalse(checks.TRAILER_RE.search(body), body)

    def test_a_merge_commit_needs_neither_subject_nor_trailer(self):
        self.assertTrue(run("behind-merge")["commit-message-conventional"][0])


class GitGuardrails(unittest.TestCase):
    CLEAN = [
        "git status --porcelain", "git -C /tmp/x status", "git log -n 3", "git diff --stat && git push origin main", "git push -u origin main",
        'git add -A && git commit -m "chore: snapshot" -m "Co-Authored-By: Claude <noreply@anthropic.com>"', "git commit -am wip",
        "git merge origin/main", "git pull --no-rebase origin main", "git config pull.rebase false", "git config set pull.rebase false", "git reset HEAD notes/todo.md",
        "git add stash.txt", "git fetch -f", 'git commit -m "fix: remove the --no-verify flag from the docs"',
        "git commit -m \"$(cat <<'EOF'\nchore: stop using git rebase and git stash\n\nCo-Authored-By: C <c@example.invalid>\nEOF\n)\"",
        "echo git rebase", "grep -f patterns file", "ls -la | head",
        # a commit message is a value, not a flag, and a separator inside quotes does not start a new command
        'git commit -m "wip: x" -m "- notes/todo.md"', 'git commit -m"note"', 'git commit -m "fix; git rebase notes"',
        'git commit -m "a | git stash" && git push', 'git commit --message "--amend" -F msg.txt', "git commit -F - -C HEAD",
        # an explicit merge, and read-only stash forms
        "git pull --rebase=false origin main", "git pull --rebase=no", "git pull --no-rebase", "git stash list", "git stash show -p",
        "git config --get core.hooksPath", "git config get core.hooksPath", "git config get user.name", "git config list", "git config pull.rebase",
        "git config pull.rebase false", "git config --unset pull.rebase", "git -c pull.rebase=false pull", "git -cpull.rebase=no pull origin main",
        # ordinary branch work and options that merely look like a flagged one
        "git checkout -b scratch", "git checkout main", "git switch main", "git switch -c scratch", "git branch --format '%(refname)'", "git branch -d scratch",
        "git reset --merge", "git reset --keep", "git push --follow-tags", "git commit --no-edit", "git commit --no-status -m x", "git log --no-walk",
        "git diff --no-index a b", "git pull --ff-only", "git pull --no-rebase", "git commit -m x --no-status",
        # a heredoc that opens on a command line that goes on
        "cat > m.txt <<'EOF' && git commit -F m.txt\nchore: x\nEOF",
        # a backslash continuation that joins ordinary arguments, or sits inside a message, is not a flag
        'git commit -m "chore: wip" \\\n  -m "Co-Authored-By: C <c@example.invalid>"', "git push \\\n  origin main", 'git commit -m "fix: docs \\\n  --no-verify"',
        # wrappers in front of an innocent command, and read-only uses of reset and command
        "timeout 30 echo git rebase", "nice -n 5 ls", "env -u FOO ls", "command -v git", "git reset -- notes/todo.md", "git reset --mixed", "xargs echo git stash",
        "git -c core.editor=true commit -m x", "GIT_EDITOR=true git commit -m x", "env GIT_AUTHOR_NAME=x git status",
        # sentences that mention a banned form are values, not commands
        'git commit -m "fix: do not run reset --soft HEAD~1 or timeout 30 git push --force"', 'git log --grep "reset HEAD~1"', "git checkout HEAD~1 -- README.md",
    ]
    BAD = {
        "git commit --no-verify -m wip": "commit --no-verify", "git commit -nm wip": "commit --no-verify",
        'git commit -m "x" --no-verify': "commit --no-verify", 'git commit -am "x" -n': "commit --no-verify",
        "git push --no-verify": "push --no-verify", "git merge --no-verify origin/main": "merge --no-verify",
        "git commit --amend --no-edit": "commit --amend", "git rebase origin/main": "rebase",
        "git -c core.editor=true rebase -i main": "rebase", "GIT_EDITOR=true git rebase --continue": "rebase",
        "git pull --rebase": "pull --rebase", "git pull -r origin main": "pull --rebase", "git stash": "stash", "git stash pop": "stash",
        "/usr/bin/git stash": "stash", "git reset --hard HEAD~1": "reset --hard", "git filter-branch --tree-filter x": "filter-branch",
        "git push --force origin main": "forced push", "git push -f": "forced push", "git push origin main --force-with-lease": "forced push",
        "git push origin +main": "forced push", "git push -fu origin main": "forced push", "cd ws && git add -A && git push -f": "forced push",
        "git pull --rebase=true": "pull --rebase", "git pull --rebase=merges origin main": "pull --rebase", "git stash push -m x": "stash",
        "git stash drop": "stash",
        # the shell syntax around a command does not hide it
        "(git stash)": "stash", "{ git rebase origin/main; }": "rebase", "if true; then git rebase origin/main; fi": "rebase",
        "for b in a; do git rebase $b; done": "rebase", "! git stash": "stash",
        # a heredoc that opens on a line that goes on: the command after it is still a command
        "cat > m.txt <<'EOF' && git commit --no-verify -F m.txt\nchore: x\nEOF": "commit --no-verify",
        "git -c core.hooksPath=/dev/null commit -m x": "core.hooksPath override", "git config core.hooksPath /tmp/other": "core.hooksPath override",
        # the same flags on a continuation line: the commit leaves no trace in an empty hooks directory, so the trace is the only evidence
        'git commit -m "chore: wip" \\\n  --no-verify': "commit --no-verify", "git push origin main \\\n  --force": "forced push",
        "git commit -m x \\\n  -n": "commit --no-verify", "git add -A && \\\n  git rebase origin/main": "rebase",
        "git push origin main \\\r\n  --force": "forced push",
        # a wrapper does not hide the command, and its options and their values are not the command
        "timeout 30 git push --force origin main": "forced push", "timeout -s KILL 30 git rebase main": "rebase", "env -u FOO git rebase origin/main": "rebase",
        "nice -n 5 git stash": "stash", "sudo -u root git push -f": "forced push", "time git rebase x": "rebase", "nohup git stash": "stash",
        "xargs git stash": "stash", "/usr/bin/env git rebase x": "rebase", "env FOO=1 git stash": "stash", "timeout 5 sudo git rebase x": "rebase",
        # core.hooksPath by an attached -c, by --config-env, and by the environment
        "git -ccore.hooksPath=/dev/null commit -m x": "core.hooksPath override", "git --config-env=core.hooksPath=HP commit -m x": "core.hooksPath override",
        "git --config-env core.hooksPath=HP commit -m x": "core.hooksPath override",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x": "core.hooksPath override",
        "env GIT_CONFIG_PARAMETERS=\"'core.hookspath=/dev/null'\" git commit -m x": "core.hooksPath override",
        # dropping the agent's own commit is a history rewrite too (a .env commit undone this way is gone from every ref)
        "git reset --soft HEAD~1": "reset to an earlier commit", "git reset HEAD~1": "reset to an earlier commit", "git reset --mixed HEAD^": "reset to an earlier commit",
        "git reset HEAD@{1}": "reset to an earlier commit",
        # git accepts a unique abbreviation of a long option, and an abbreviated hook bypass leaves no trace in the repository
        "git commit --no-veri -m 'wip: x'": "commit --no-verify", "git commit --no-v -m x": "commit --no-verify", "git push --no-verif": "push --no-verify",
        "git push --forc origin main": "forced push", "git push --for origin main": "forced push", "git push --force-with-lea origin main": "forced push",
        "git commit --amen -m x": "commit --amend", "git commit -m x --amend": "commit --amend", "git pull --reb origin main": "pull --rebase",
        "git pull --rebas=true": "pull --rebase", "git reset --hard": "reset --hard",
        # other ways to move a branch off its commits, or to make the next pull a rebase
        "git checkout -B main origin/main": "checkout resets a branch", "git switch -C main origin/main": "switch resets a branch",
        "git branch -f main HEAD~2": "branch resets a branch", "git branch --force main abc123": "branch resets a branch",
        "git update-ref refs/heads/main abc123": "update-ref", "git reset --keep origin/main": "reset to an earlier commit",
        "git reset --merge ORIG_HEAD": "reset to an earlier commit", "git config pull.rebase true": "pull --rebase",
        "git config set pull.rebase true": "pull --rebase", "git config set --global pull.rebase merges": "pull --rebase",
        "git -c pull.rebase=true pull": "pull --rebase", "git -cpull.rebase=merges pull origin main": "pull --rebase",
    }

    def test_clean_commands_are_not_violations(self):
        for command in self.CLEAN:
            self.assertEqual(checks.git_offences(command), [], command)

    def test_each_violation_is_named(self):
        for command, offence in self.BAD.items():
            self.assertEqual(checks.git_offences(command), [offence], command)

    def test_an_escaped_backslash_before_a_newline_is_not_a_continuation(self):
        # `\\` + newline is a literal backslash and then a new command: the next line stands alone
        self.assertEqual(checks.git_offences("echo \\\\\ngit stash"), ["stash"])
        self.assertEqual(checks.git_offences("git push origin main \\\\\n--force"), [])
        self.assertEqual(checks.git_offences("git push origin main \\\\\\\n--force"), ["forced push"])      # three: escaped one, then a continuation

    def test_wrappers_hide_nothing_and_their_values_are_not_the_command(self):
        self.assertEqual(checks.git_calls("timeout 30 git status"), [("status", [], [])])
        self.assertEqual(checks.git_calls("timeout 30"), [])
        self.assertEqual(checks.git_calls("env -u A -u B git log"), [("log", [], [])])
        self.assertEqual(checks.git_calls("nice -n 5 echo git log"), [])

    def test_a_violation_in_the_trace_fails_the_check(self):
        self.assertFalse(run("wip-preexisting", commands=["git stash"])["guardrails-in-trace"][0])
        self.assertTrue(run("wip-preexisting", commands=self.CLEAN)["guardrails-in-trace"][0])

    def test_a_subagent_call_is_not_seen(self):
        row, events, states = helpers.good("wip-preexisting", commands=["git stash"])
        events[-2]["parent_tool_use_id"] = "toolu_sub"
        self.assertTrue(checks.CHECKS["guardrails-in-trace"](checks.Case(row, events, states))[1])


if __name__ == "__main__":
    unittest.main()
