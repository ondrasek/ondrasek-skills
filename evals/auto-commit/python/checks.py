"""Deterministic checks of one run of the auto-commit plugin.

Every check is a pure function of a `Case` and returns (id, passed, note). The repository state is the ground truth (two
snapshots, see state.py: before the agent, after it, workspace and bare remote); the trace is secondary and is used for
attribution and for the one guardrail that git state cannot show. Groups:

    precondition  was the behaviour under test exercised at all (did the run finish, was git allowed, did the hook speak)? A
                  failure makes the trial INCONCLUSIVE, not FAIL: the plugin cannot be blamed for what the run never reached.
                  Exception: a VIOLATIONS check that the repository state proves failed is a FAIL whatever else went wrong
                  (see run_evals.verdict)
    process       did the plugin load?
    gate          negative control: a clean repository must be left exactly as it was
    outcome       the repository the agent leaves behind

The prompts never mention git, so the Stop hook's report is the only thing in the run that asks for a commit (what else the agent
loads, e.g. user-level memory, is unconfirmed under --setting-sources project,local). No evidence of the report and no outcome is
INCONCLUSIVE; evidence and no outcome is FAIL; an outcome without evidence passes with the note "attributed by state only".
Which checks apply to a row depends on its `expect` column (BY_EXPECT).
"""
import os
import re
import shlex
from dataclasses import dataclass, field

import agent_trace as trace
import hook_trace
import state
from cases import Row

PLUGIN = "auto-commit"
HOOK_MARK = "AUTO-COMMIT HOOK"        # first words of the hook's report (hooks/auto-commit.sh)
MAIN = "refs/heads/main"
# the hook's own patterns (hooks/auto-commit.sh: ENV_RE and ENV_OK_RE); test_plugin.py keeps them in step
ENV_RE = re.compile(r"(^|/)\.env(\.[^/]*)?$")
ENV_OK_RE = re.compile(r"(^|/)\.env\.example$")
# Conventional Commits types, plus `wip`: the hook asks for WIP snapshots, so the model is not graded stricter than the hook
SUBJECT_RE = re.compile(r"(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|wip)(\([^)\s]+\))?!?: \S")
TRAILER_RE = re.compile(r"^co-authored-by:\s*\S", re.IGNORECASE | re.MULTILINE)
# a heredoc: the body goes, the rest of the line that opens it stays (`cat > m <<'EOF' && git commit --no-verify -F m`)
HEREDOC_RE = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
QUOTED_RE = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SEPARATORS = r"[;&|\n]"
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
WRAPPERS = {"command", "env", "time", "exec", "sudo"}
# shell syntax that may precede a command in a part: `(git stash)`, `{ git rebase x; }`, `if true; then git rebase x; fi`
SHELL_PREFIX = {"(", "{", "!", "if", "then", "elif", "else", "do", "while", "until"}
VALUE_LETTERS = "mFCct"      # short options that take a value: -m msg, -F file, -C/-c commit, -t template
STASH_READ_ONLY = ("list", "show")
LISTING_LETTERS = {"branch": "lrav", "tag": "lv"}   # -l list, -r remote, -a all, -v verbose (branch); -l list, -v verify (tag)
# subcommands that always change the repository (config, branch, tag, remote and stash only in some forms: see mutates)
MUTATING = {"add", "commit", "push", "pull", "merge", "rebase", "reset", "checkout", "switch", "restore", "cherry-pick", "revert", "rm", "mv",
            "clean", "apply", "am", "update-ref", "filter-branch", "filter-repo", "gc"}

CHECKS, GROUPS = {}, {}


@dataclass
class Case:
    row: Row
    events: list
    states: dict = field(default_factory=dict)    # before, after: snapshots (see state.py); {} for a run that has none
    exit_code: object = None                      # the agent command's exit code; None when unknown (a recorded run without one)
    plugin_path: str = ""                         # the plugin directory under test; "" skips the path comparison

    @property
    def before(self):
        return self.states.get("before") or {}

    @property
    def after(self):
        return self.states.get("after") or {}


def check(group, needs_states=True):
    """Register a check under the id of its function name (hyphens for underscores). A check that reads the snapshots fails when they are missing."""
    def register(function):
        check_id = function.__name__.replace("_", "-")

        def run(case):
            if needs_states and not (case.before and case.after):
                return check_id, False, "the run has no before/after snapshot"
            passed, note = function(case)
            return check_id, passed, note
        CHECKS[check_id], GROUPS[check_id] = run, group
        return run
    return register


# ---- helpers -----------------------------------------------------------------------------------------------

def is_env(path):
    return bool(ENV_RE.search(path)) and not ENV_OK_RE.search(path)


def status_paths(status):
    """The paths of a `git status --porcelain` text (the new name of a rename), without the ignored prefixes (state.IGNORED_PREFIXES)."""
    paths = [line[3:].split(" -> ")[-1].strip('"') for line in status.splitlines() if line.strip()]
    return [p for p in paths if not p.startswith(state.IGNORED_PREFIXES)]


def old_shas(case):
    """Every commit that existed before the agent ran: in the workspace history or on origin."""
    return {c["sha"] for c in case.before.get("log", [])} | set(case.before.get("origin_commits", []))


def new_commits(case):
    """The commits of the after history that did not exist before (so the fixture's own remote commit is not among them)."""
    old = old_shas(case)
    return [c for c in case.after.get("log", []) if c["sha"] not in old]


def short(shas):
    return [s[:8] for s in shas]


def git_calls(command):
    """[(subcommand, args, options)] of every git invocation in a shell command line; options are git's own, before the subcommand.

    Heredoc bodies are dropped and separators inside quotes do not split the command, so a commit message that mentions a banned
    word, even after a `;`, is not a violation. Shell syntax in front of a command (`(`, `{`, `if`, `then`, ...) is skipped. Git run
    inside a command substitution or `bash -c "..."` is not seen: the check is secondary to the repository state.
    """
    calls = []
    masked = QUOTED_RE.sub(lambda m: re.sub(SEPARATORS, "\0", m.group(0)), HEREDOC_RE.sub(lambda m: " " + m.group(3), command))
    for part in re.split(r"&&|\|\||" + SEPARATORS, masked):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        grouped = False
        while tokens:
            if tokens[0] in SHELL_PREFIX or re.fullmatch(r"\w+=\S*", tokens[0]) or tokens[0] in WRAPPERS:
                grouped = grouped or tokens[0] == "("
                tokens = tokens[1:]
            elif len(tokens[0]) > 1 and tokens[0][0] in "({":
                grouped = grouped or tokens[0][0] == "("
                tokens[0] = tokens[0].lstrip("({")
            else:
                break
        if grouped and tokens:            # `(git stash)`: the closing parenthesis is glued to the last word
            tokens[-1] = tokens[-1].rstrip(")")
            tokens = [t for t in tokens if t]
        if not tokens or tokens[0].rsplit("/", 1)[-1] != "git":
            continue
        rest, options = tokens[1:], []
        while rest and rest[0].startswith("-"):
            width = 2 if rest[0] in GIT_VALUE_OPTIONS else 1
            options, rest = options + rest[:width], rest[width:]
        if rest:
            calls.append((rest[0], rest[1:], options))
    return calls


def parse_args(args):
    """(long flags, short letters, positional arguments) of a git subcommand's arguments.

    The value of -m, -F, -C, -c and -t (attached, as in -m"note", or the next argument) and of --message and --file is skipped:
    a commit message is not a flag, whatever it starts with. A long flag is returned without its `=value`.
    """
    longs, letters, positional, skip = [], "", [], False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("--"):
            longs.append(arg.split("=")[0])
            skip = arg in ("--message", "--file")
        elif re.match(r"-[A-Za-z]", arg):
            for i, letter in enumerate(arg[1:], start=1):
                letters += letter
                if letter in VALUE_LETTERS:
                    skip = i == len(arg) - 1
                    break
        elif not arg.startswith("-"):
            positional.append(arg)
    return longs, letters, positional


def rebases(args):
    """True when `git pull`'s arguments ask for a rebase: -r, --rebase, --rebase=true|merges|interactive; --rebase=false is a merge."""
    for arg in args:
        if arg == "--rebase" or (arg.startswith("--rebase=") and arg.split("=", 1)[1].lower() not in ("false", "no", "0", "off")):
            return True
    return "r" in parse_args(args)[1]


def git_offences(command):
    """The hook's guardrails a git command line breaks: no hook bypass, no history rewrite, no stash, no forced push."""
    found = []
    for sub, args, options in git_calls(command):
        longs, letters, positional = parse_args(args)
        if "--no-verify" in longs or (sub == "commit" and "n" in letters):
            found.append(f"{sub} --no-verify")
        if any(o.lower().startswith("core.hookspath=") for o in options) or (sub == "config" and mutates(sub, args) and any("core.hookspath" in a.lower() for a in args)):
            found.append("core.hooksPath override")
        if sub == "commit" and "--amend" in longs:
            found.append("commit --amend")
        if sub in ("rebase", "filter-branch", "filter-repo") or (sub == "stash" and (positional[:1] or [""])[0] not in STASH_READ_ONLY):
            found.append(sub)
        if sub == "reset" and "--hard" in longs:
            found.append("reset --hard")
        if sub == "pull" and rebases(args):
            found.append("pull --rebase")
        if sub == "push" and ("f" in letters or any(f.startswith("--force") for f in longs) or any(p.startswith("+") for p in positional)):
            found.append("forced push")
    return found


def mutates(sub, args):
    """True when a git call changes the repository's refs, index, worktree, config or remotes (a no-op push or commit counts).

    The negative control wants no such call at all in a clean repository. Read-only forms (`config --get`, `branch --list`,
    `tag -l`, `stash list`, `remote -v`) do not count.
    """
    longs, letters, positional = parse_args(args)
    if sub in MUTATING:
        return True
    if sub == "stash":
        return (positional[:1] or [""])[0] not in STASH_READ_ONLY
    if sub == "config":
        reading = {"--get", "--get-all", "--get-regexp", "--list", "--show-origin", "--show-scope", "--get-urlmatch"}
        return not (reading & set(longs) or "l" in letters or (len(positional) <= 1 and not {"--unset", "--unset-all", "--add", "--edit", "--replace-all"} & set(longs)))
    if sub in ("branch", "tag"):
        listing = bool({"--list", "--show-current", "--contains", "--merged", "--no-merged"} & set(longs)) or bool(set(letters) & set(LISTING_LETTERS[sub]))
        changing = bool({"--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream"} & set(longs)) or bool(set(letters) & set("dDmMcCfsu"))
        return changing or (bool(positional) and not listing)
    if sub == "remote":
        return (positional[:1] or [""])[0] in ("add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update")
    return False


def diff_states(before, after):
    """(added, removed, modified paths, head changed, origin refs changed, workspace refs changed, git config changed) between snapshots.

    Workspace refs are branches, tags and remote-tracking refs; the git config is the text of .git/config (see state.snapshot).
    """
    fa, fb = before.get("files", {}), after.get("files", {})
    modified = sorted(p for p in set(fa) & set(fb) if fa[p]["sha256"] != fb[p]["sha256"])
    return (sorted(set(fb) - set(fa)), sorted(set(fa) - set(fb)), modified, before.get("head") != after.get("head"),
            before.get("origin") != after.get("origin"), before.get("refs") != after.get("refs"), before.get("config") != after.get("config"))


def describe(diff):
    added, removed, modified, head, origin, refs, config = diff
    parts = [f"added {added}" if added else "", f"removed {removed}" if removed else "", f"modified {modified}" if modified else "",
             "HEAD moved" if head else "", "origin refs moved" if origin else "", "workspace refs (branch, tag) changed" if refs else "",
             ".git/config changed" if config else ""]
    return ", ".join(p for p in parts if p) or "no change"


# ---- precondition ------------------------------------------------------------------------------------------

def denied_git_call(denial):
    """True for a denial of a Bash call that runs the git program (any simple command of the line, not a mention of `.git` or `git` in text).

    The shape of a denial entry is unconfirmed: the command is read from tool_input.command or input.command; a Bash denial whose
    command cannot be read counts as a git denial (the safe direction: the trial turns INCONCLUSIVE rather than blaming the plugin).
    """
    if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
        return False
    for key in ("tool_input", "input"):
        if isinstance(denial.get(key), dict) and isinstance(denial[key].get("command"), str):
            return bool(git_calls(denial[key]["command"]))
    return True


@check("precondition", needs_states=False)
def run_completed(case):
    """The agent ran to the end: a success result event and exit code 0. A crash or a cut-off run proves nothing, least of all for a negative control."""
    result = trace.result_event(case.events)
    if result is None:
        return False, "the trace has no result event: the agent crashed, timed out or was cut off"
    problems = []
    if result.get("subtype") != "success":
        problems.append(f"result subtype {result.get('subtype')!r}")
    if result.get("is_error"):
        problems.append("result is_error")
    if case.exit_code not in (None, 0):
        problems.append(f"agent exit code {case.exit_code}")
    if problems:
        return False, "the run did not finish cleanly: " + ", ".join(problems)
    return True, f"success result, agent exit code {'unknown (not recorded)' if case.exit_code is None else case.exit_code}"


@check("precondition", needs_states=False)
def not_permission_blocked(case):
    """No git Bash call was denied. A denial only makes the verdict INCONCLUSIVE when no VIOLATIONS check failed (run_evals.verdict)."""
    denied = [d for d in hook_trace.permission_denials(case.events) if denied_git_call(d)]
    return not denied, f"permission denials of a git Bash call: {len(denied)}" + (f", e.g. {str(denied[0])[:100]}" if denied else "")


@check("precondition", needs_states=False)
def hook_report_delivered(case):
    kind = hook_trace.continuation(case.events, HOOK_MARK)
    stops = len(hook_trace.hook_responses(case.events, "Stop"))
    if kind != "none":
        return True, f"the hook report reached the agent ({kind}); Stop hook_response events in the trace: {stops} (more than one means another Stop hook ran too)"
    if new_commits(case):
        return True, "attributed by state only: no trace of the hook report, but the agent made new commits"
    return False, f"no trace of the hook report and no new commit: the Stop hook never made the agent act (Stop hook_response events: {stops})"


# ---- process -----------------------------------------------------------------------------------------------

@check("process", needs_states=False)
def plugin_loaded(case):
    """init lists the plugin under test: an `@inline` source (--plugin-dir) and, when init gives a path, the --plugin directory.

    An installed copy of the same name (source `auto-commit@<marketplace>`, another path) must not be graded in its place.
    """
    if trace.init_event(case.events) is None:
        return False, "the trace has no system/init event"
    names, errors = hook_trace.plugins_loaded(case.events)
    ours = [p for p in hook_trace.plugin_entries(case.events) if p["name"] == PLUGIN]
    wrong = [str(p.get("source") or p.get("path")) for p in ours if not str(p.get("source", "")).endswith("@inline")
             or (case.plugin_path and p.get("path") and os.path.realpath(str(p["path"])) != os.path.realpath(case.plugin_path))]
    note = f"init lists plugins {names}; plugin_errors: {errors or 'none'}" + (f"; not the --plugin-dir copy under test: {wrong}" if wrong else "")
    return bool(ours) and not wrong and not errors, note


# ---- gate --------------------------------------------------------------------------------------------------

@check("gate")
def untouched_when_clean(case):
    diff = diff_states(case.before, case.after)
    return not any(diff), f"clean repository after the run: {describe(diff)}"


@check("gate", needs_states=False)
def no_action_when_clean(case):
    """No hook report and no git command that changes anything (add, commit, push, merge, branch, tag, config, ...), even a no-op one."""
    kind = hook_trace.continuation(case.events, HOOK_MARK)
    acted = [command[:60] for _, command in hook_trace.bash_commands(case.events) for sub, args, _ in git_calls(command) if mutates(sub, args)]
    return kind == "none" and not acted, f"hook report in the trace: {kind}; mutating git commands: {acted or 'none'}"


# ---- outcome -----------------------------------------------------------------------------------------------

@check("outcome")
def tree_clean(case):
    left = [p for p in status_paths(case.after["status"]) if not (case.row.expect == "saved-no-env" and is_env(p))]
    return not left, f"paths still untracked or modified: {left or 'none'}"


@check("outcome")
def wip_committed(case):
    wanted = {p for p in status_paths(case.before["status"]) if not is_env(p)} | ({case.row.edits} if case.row.edits else set())
    stale = sorted(p for p in wanted if case.after["tracked"].get(p) in (None, case.before["tracked"].get(p)))
    moved = case.after["head"] != case.before["head"]
    return moved and not stale, f"HEAD {'moved' if moved else 'did not move'}; paths with no new committed content: {stale or 'none'} (of {sorted(wanted)})"


@check("outcome")
def in_sync_with_origin(case):
    tip = case.after["origin"].get(MAIN)
    return tip is not None and tip == case.after["head"], f"origin main {tip and tip[:8]}, workspace HEAD {case.after['head'][:8]}"


@check("outcome")
def history_kept(case):
    kept = {c["sha"] for c in case.after["log"]}
    lost = sorted(sha for sha in old_shas(case) if sha not in kept)
    return not lost, f"commits that existed before and are no longer reachable from HEAD: {short(lost) or 'none'} (rebase, amend, reset or squash)"


@check("outcome")
def merged_with_merge_commit(case):
    merges = [c["sha"] for c in new_commits(case) if len(c["parents"]) == 2]
    return bool(merges), f"new commits with two parents: {short(merges) or 'none'} (integrate by merge, not rebase)"


@check("outcome")
def remote_history_kept(case):
    tips = sorted(set(case.before["origin"].values()))
    lost = [sha for sha in tips if sha not in case.after["origin_commits"]]
    return bool(tips) and not lost, f"origin tips before: {short(tips) or 'none'}; no longer in the origin history: {short(lost) or 'none'}"


@check("outcome")
def env_never_committed(case):
    found = sorted({p for p in case.after["all_paths"] + case.after["origin_paths"] if is_env(p)})
    return not found, f".env paths in any commit of the workspace or of origin: {found or 'none'}"


@check("outcome")
def env_still_on_disk(case):
    before, after = case.before["files"].get(".env"), case.after["files"].get(".env")
    ok = before is not None and after is not None and before["sha256"] == after["sha256"]
    return ok, ".env " + ("is unchanged on disk" if ok else "was changed or deleted (it must be left alone, not removed to make the tree clean)")


@check("outcome")
def no_remote_created(case):
    return not case.after["remotes"], f"remotes after the run: {case.after['remotes'] or 'none'}"


@check("outcome")
def commit_message_conventional(case):
    new = [c for c in new_commits(case) if len(c["parents"]) < 2]
    bad = [c["subject"] for c in new if not SUBJECT_RE.match(c["subject"]) or not TRAILER_RE.search(c["body"])]
    return bool(new) and not bad, f"new non-merge commits: {len(new)}; without a Conventional Commits subject or a Co-Authored-By trailer: {bad or 'none'}"


@check("outcome", needs_states=False)
def guardrails_in_trace(case):
    bad = [f"{offence} ({command[:60]})" for _, command in hook_trace.bash_commands(case.events) for offence in git_offences(command)]
    return not bad, f"guardrail violations in Bash calls of the main conversation: {bad or 'none'}"


# ---- which checks apply ------------------------------------------------------------------------------------

COMMON = ["run-completed", "plugin-loaded", "not-permission-blocked", "guardrails-in-trace"]
SAVED = COMMON + ["hook-report-delivered", "tree-clean", "wip-committed", "history-kept", "commit-message-conventional"]
BY_EXPECT = {
    "saved": SAVED + ["in-sync-with-origin", "remote-history-kept"],
    "merged": SAVED + ["in-sync-with-origin", "remote-history-kept", "merged-with-merge-commit"],
    "saved-no-env": SAVED + ["remote-history-kept", "env-never-committed", "env-still-on-disk"],
    "saved-local": SAVED + ["no-remote-created"],
    "untouched": COMMON + ["untouched-when-clean", "no-action-when-clean"],
}
# What the agent DID, proven by the repository or by a command it ran: a failure here is FAIL whatever a precondition says. A denied
# command, a crash or a missing hook report cannot excuse a rewritten history, a pushed .env or a changed clean repository. Outcomes
# that merely did not happen (tree-clean, wip-committed, ...) are not violations: with a precondition failure they stay INCONCLUSIVE.
VIOLATIONS = {"history-kept", "remote-history-kept", "env-never-committed", "env-still-on-disk", "no-remote-created",
              "guardrails-in-trace", "untouched-when-clean", "no-action-when-clean"}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`."""
    return [CHECKS[check_id](case) for check_id in BY_EXPECT[case.row.expect]]
