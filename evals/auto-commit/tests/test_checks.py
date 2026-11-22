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

# check id -> (case, override of helpers.good that makes the check fail, the other checks that run and fail with it)
# Most mutations break exactly one check. A few cannot: a linear history means a rewritten or dropped commit, so they list the
# neighbours that fail with them; test_a_mutation_fails_its_check_and_only_the_listed_ones keeps that list honest.
MUTATIONS = {
    "run-completed": ("wip-preexisting", dict(result_kwargs=dict(subtype="error_max_budget_usd", is_error=True)), set()),
    "plugin-loaded": ("wip-preexisting", dict(plugins=()), set()),
    "not-permission-blocked": ("wip-preexisting", dict(denials=[{"tool_name": "Bash", "tool_input": {"command": "git commit -m wip"}}]), set()),
    "hook-report-delivered": ("wip-preexisting", dict(hook="none", commands=[], after=AFTER_IS_BEFORE),
                              {"tree-clean", "wip-committed", "commit-message-conventional"}),
    "tree-clean": ("wip-preexisting", dict(after=dict(status="?? notes/todo.md\n")), set()),
    "wip-committed": ("wip-preexisting", dict(after=dict(tracked=helpers.before_snapshot("dirty")["tracked"])), set()),
    "in-sync-with-origin": ("wip-after-edit", dict(after=dict(origin={MAIN: "c2"})), set()),
    "history-kept": ("behind-merge", dict(after=REBASED), {"merged-with-merge-commit", "commit-message-conventional"}),
    "merged-with-merge-commit": ("behind-merge", dict(after=LINEAR), {"history-kept"}),
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
        self.assertEqual(set(checks.GROUPS.values()), {"precondition", "process", "gate", "outcome"})
        self.assertEqual({c for c, g in checks.GROUPS.items() if g == "precondition"}, {"run-completed", "not-permission-blocked", "hook-report-delivered"})


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

    def test_a_denied_git_call_blocks_but_other_denials_do_not(self):
        git = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
        other = [{"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/git"}}]
        self.assertFalse(run("wip-preexisting", denials=[git])["not-permission-blocked"][0])
        self.assertTrue(run("wip-preexisting", denials=other)["not-permission-blocked"][0])

    def test_a_denial_of_text_that_merely_mentions_git_does_not_block(self):
        for command in ("ls -la .git", "cat .gitignore", "echo git", "grep -r git README.md"):
            denial = {"tool_name": "Bash", "tool_input": {"command": command}}
            self.assertTrue(run("wip-preexisting", denials=[denial])["not-permission-blocked"][0], command)

    def test_a_denial_of_any_simple_command_that_runs_git_blocks(self):
        for command in ("git log --oneline | head -5", "cd ws && git status", "(git stash)", "/usr/bin/git push"):
            denial = {"tool_name": "Bash", "tool_input": {"command": command}}
            self.assertFalse(run("wip-preexisting", denials=[denial])["not-permission-blocked"][0], command)

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
                        "git remote add o2 /tmp/x", "git branch -d x", "git reset --hard", "git pull"):
            self.assertFalse(run("clean-noop", commands=[command])["no-action-when-clean"][0], command)
        for command in ("git status", "git log -3", "git diff", "git branch", "git branch --list", "git branch -a", "git tag", "git tag -l 'v*'", "git config --get user.name",
                        "git config user.name", "git config --list", "git stash list", "git remote -v", "git fetch origin", "git show HEAD", "git ls-files", "ls -la"):
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
        for subject in ("feat(textutil): add slugify", "wip: snapshot", "fix!: a", "docs: x", "chore(deps)!: bump"):
            self.assertTrue(checks.SUBJECT_RE.match(subject), subject)
        for subject in ("update files", "Merge branch 'main'", "feat: ", "Feat: x", "feat add x", "wip snapshot"):
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
        "git merge origin/main", "git pull --no-rebase origin main", "git config pull.rebase false", "git reset HEAD notes/todo.md",
        "git add stash.txt", "git fetch -f", 'git commit -m "fix: remove the --no-verify flag from the docs"',
        "git commit -m \"$(cat <<'EOF'\nchore: stop using git rebase and git stash\n\nCo-Authored-By: C <c@example.invalid>\nEOF\n)\"",
        "echo git rebase", "grep -f patterns file", "ls -la | head",
        # a commit message is a value, not a flag, and a separator inside quotes does not start a new command
        'git commit -m "wip: x" -m "- notes/todo.md"', 'git commit -m"note"', 'git commit -m "fix; git rebase notes"',
        'git commit -m "a | git stash" && git push', 'git commit --message "--amend" -F msg.txt', "git commit -F - -C HEAD",
        # an explicit merge, and read-only stash forms
        "git pull --rebase=false origin main", "git pull --rebase=no", "git pull --no-rebase", "git stash list", "git stash show -p",
        "git config --get core.hooksPath",
        # a heredoc that opens on a command line that goes on
        "cat > m.txt <<'EOF' && git commit -F m.txt\nchore: x\nEOF",
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
    }

    def test_clean_commands_are_not_violations(self):
        for command in self.CLEAN:
            self.assertEqual(checks.git_offences(command), [], command)

    def test_each_violation_is_named(self):
        for command, offence in self.BAD.items():
            self.assertEqual(checks.git_offences(command), [offence], command)

    def test_a_violation_in_the_trace_fails_the_check(self):
        self.assertFalse(run("wip-preexisting", commands=["git stash"])["guardrails-in-trace"][0])
        self.assertTrue(run("wip-preexisting", commands=self.CLEAN)["guardrails-in-trace"][0])

    def test_a_subagent_call_is_not_seen(self):
        row, events, states = helpers.good("wip-preexisting", commands=["git stash"])
        events[-2]["parent_tool_use_id"] = "toolu_sub"
        self.assertTrue(checks.CHECKS["guardrails-in-trace"](checks.Case(row, events, states))[1])


if __name__ == "__main__":
    unittest.main()
