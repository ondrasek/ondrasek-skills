"""Deterministic checks of one run of the auto-commit plugin.

Every check is a pure function of a `Case` and returns (id, passed, note). The repository state is the ground truth (two
snapshots, see state.py: before the agent, after it, workspace and bare remote); the trace is secondary and is used for
attribution, for the guardrails that git state cannot show (guardrails-in-trace) and for the negative control's no-action check.
Groups:

    precondition  was the behaviour under test exercised at all (did the run finish, are the snapshots usable, was git allowed,
                  did the hook speak exactly once)? A failure makes the trial INCONCLUSIVE, not FAIL: the plugin cannot be blamed
                  for what the run never reached. Exception: a VIOLATIONS check that the trace or a usable repository snapshot
                  proves failed is a FAIL whatever else went wrong (see run_evals.verdict)
    process       did the plugin load?
    gate          negative control: a clean repository must be left exactly as it was
    outcome       the repository the agent leaves behind
    soft          advisory: a check that rests only on a regex over text the model wrote (a commit message's wording). It is computed
                  and reported as WARN, and it never changes a verdict or the exit code (see run_evals.verdict)

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
# Conventional Commits types, plus `wip`: the hook asks for WIP snapshots, so the model is not graded stricter than the hook.
# Case-insensitive: the spec (1.0.0, item 15) says the units of information MUST NOT be treated as case-sensitive.
SUBJECT_RE = re.compile(r"(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|wip)(\([^)\s]+\))?!?: \S", re.IGNORECASE)
TRAILER_RE = re.compile(r"^co-authored-by:\s*\S", re.IGNORECASE | re.MULTILINE)
# a heredoc: the body goes, the rest of the line that opens it stays (`cat > m <<'EOF' && git commit --no-verify -F m`)
HEREDOC_RE = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
QUOTED_RE = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SEPARATORS = r"[;&|\n]"
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
# Programs that run the command that follows them: name -> (their options that take a value, positional arguments before the command).
# `timeout 30 git push`: the duration is not the command. Matched on the basename, so /usr/bin/env counts.
WRAPPERS = {
    "command": (set(), 0), "exec": ({"-a"}, 0), "time": ({"-f", "-o", "--format", "--output"}, 0), "nohup": (set(), 0),
    "env": ({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}, 0),
    "sudo": ({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "--user", "--group", "--host", "--prompt", "--chdir"}, 0),
    "timeout": ({"-s", "-k", "--signal", "--kill-after"}, 1), "nice": ({"-n", "--adjustment"}, 0),
    "xargs": ({"-I", "-n", "-P", "-d", "-L", "-E", "-s", "-a", "-l"}, 0),
}
ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*=.*", re.DOTALL)
# a backslash-newline joins two lines of a shell command; an even run of backslashes before the newline escapes itself
CONTINUATION_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")
# shell syntax that may precede a command in a part: `(git stash)`, `{ git rebase x; }`, `if true; then git rebase x; fi`
SHELL_PREFIX = {"(", "{", "!", "if", "then", "elif", "else", "do", "while", "until"}
VALUE_LETTERS = "mFCct"      # short options that take a value: -m msg, -F file, -C/-c commit, -t template
STASH_READ_ONLY = ("list", "show")
FALSE_VALUES = ("false", "no", "0", "off")          # the values of pull.rebase and `--rebase=` that mean a merge
CONFIG_READING = {"get", "get-all", "get-regexp", "get-urlmatch", "get-color", "get-colorbool", "list"}   # git 2.46+: `git config get KEY`
CONFIG_WRITING = {"set", "unset", "edit", "rename-section", "remove-section"}                            # and `git config set KEY VALUE`
LISTING_LETTERS = {"branch": "lrav", "tag": "lvn"}  # -l list, -r remote, -a all, -v verbose (branch); -l list, -v verify, -n lines (tag)
DRY_RUN_LETTER = {"push", "add", "clean", "rm", "mv"}   # subcommands whose -n is --dry-run (for commit it is --no-verify)
# subcommands that always change the repository (config, branch, tag, remote and stash only in some forms: see mutates)
MUTATING = {"add", "commit", "push", "pull", "merge", "rebase", "reset", "checkout", "switch", "restore", "cherry-pick", "revert", "rm", "mv",
            "clean", "apply", "am", "update-ref", "filter-branch", "filter-repo", "gc"}

CHECKS, GROUPS, STATELESS = {}, {}, set()      # STATELESS: the checks that read the trace only, never a snapshot


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


def usable(snapshot):
    """True for a snapshot that read a repository: a missing one is {}, and state.snapshot returns one without a head when git failed."""
    return bool(snapshot.get("head"))


def check(group, needs_states=True):
    """Register a check under the id of its function name (hyphens for underscores).

    A check that reads the snapshots fails, with a note that says so, when either is missing or unusable; `state-recorded` makes that
    a precondition failure, so such a failure is never graded as something the agent did (run_evals.verdict).
    """
    def register(function):
        check_id = function.__name__.replace("_", "-")

        def run(case):
            if needs_states and not (usable(case.before) and usable(case.after)):
                return check_id, False, "the run has no usable before/after snapshot"
            passed, note = function(case)
            return check_id, passed, note
        CHECKS[check_id], GROUPS[check_id] = run, group
        if not needs_states:
            STATELESS.add(check_id)
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


def own_shas(case):
    """The commits reachable from the workspace HEAD before the agent ran. The unmerged commit of the `behind` fixture's origin is not
    among them: it was never in the workspace history, so a run that did not integrate it has not lost it."""
    return {c["sha"] for c in case.before.get("log", [])}


def new_commits(case):
    """The commits of the after history that did not exist before (so the fixture's own remote commit is not among them)."""
    old = old_shas(case)
    return [c for c in case.after.get("log", []) if c["sha"] not in old]


def short(shas):
    return [s[:8] for s in shas]


def git_calls(command):
    """[(subcommand, args, options)] of every git invocation in a shell command line; options are git's own, before the subcommand.

    Heredoc bodies are dropped, a backslash-newline joins its two lines (an agent often writes a long command that way, with the
    flag on the second line) and separators inside quotes do not split the command, so a commit message that mentions a banned
    word, even after a `;`, is not a violation. Shell syntax in front of a command (`(`, `{`, `if`, `then`, ...), variable
    assignments and wrappers such as `env -u X`, `timeout 30`, `nice -n 5`, `xargs` and `sudo -u x` are skipped. Git run inside a
    command substitution or `bash -c "..."` is not seen: the check is secondary to the repository state.
    """
    calls = []
    text = HEREDOC_RE.sub(lambda m: " " + m.group(3), command.replace("\r\n", "\n"))
    text = CONTINUATION_RE.sub(lambda m: m.group(1) + " ", text)
    masked = QUOTED_RE.sub(lambda m: re.sub(SEPARATORS, "\0", m.group(0)), text)
    for part in re.split(r"&&|\|\||" + SEPARATORS, masked):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        grouped, assignments = False, []
        while tokens:
            head, name = tokens[0], tokens[0].rsplit("/", 1)[-1]
            if head in SHELL_PREFIX:
                grouped = grouped or head == "("
                tokens = tokens[1:]
            elif ASSIGNMENT_RE.fullmatch(head):
                assignments.append(head)
                tokens = tokens[1:]
            elif name in WRAPPERS:
                tokens = unwrap(name, tokens[1:])
            elif len(head) > 1 and head[0] in "({":
                grouped = grouped or head[0] == "("
                tokens[0] = head.lstrip("({")
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
        for assignment in assignments:    # GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=... is `-c core.hooksPath=...`
            key, _, value = assignment.partition("=")
            if (key.startswith("GIT_CONFIG_KEY_") and value.lower() == "core.hookspath") or (key == "GIT_CONFIG_PARAMETERS" and "core.hookspath" in value.lower()):
                options += ["-c", "core.hookspath=(environment)"]
        if rest:
            calls.append((rest[0], rest[1:], options))
    return calls


def unwrap(name, tokens):
    """The tokens of the command a wrapper (see WRAPPERS) runs: its options, the values of its options and its positional arguments are dropped."""
    value_options, positionals = WRAPPERS[name]
    while tokens and tokens[0].startswith("-") and len(tokens[0]) > 1:
        option, tokens = tokens[0], tokens[1:]
        if option == "--":
            break
        if option in value_options and tokens:
            tokens = tokens[1:]
    return tokens[positionals:]


def sets_hooks_path(options):
    """True when git's own options (`-c core.hooksPath=x`, `-ccore.hooksPath=x`, `--config-env=core.hooksPath=VAR`) set core.hooksPath."""
    for i, option in enumerate(options):
        low = option.lower()
        if low.startswith(("-ccore.hookspath=", "--config-env=core.hookspath=")):
            return True
        if option in ("-c", "--config-env") and i + 1 < len(options) and options[i + 1].lower().startswith("core.hookspath="):
            return True
    return False


def abbreviates(flag, name, least=4):
    """True when the long flag (`--no-veri`) is `--name` or an abbreviation of it that git's option parser accepts: a prefix of at least
    `least` letters. Git also needs the prefix to be unique among the subcommand's options; the table is not known here, so a prefix
    shared with another option is taken for `name` too (the conservative direction for a guardrail)."""
    given = flag[2:] if flag.startswith("--") else ""
    return len(given) >= least and name.startswith(given)


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
            skip = "=" not in arg and (abbreviates(arg, "message", 3) or abbreviates(arg, "file", 3))
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
        if arg.startswith("--") and abbreviates(arg.split("=")[0], "rebase", 3):
            if "=" not in arg or arg.split("=", 1)[1].lower() not in FALSE_VALUES:
                return True
    return "r" in parse_args(args)[1]


def config_enables_rebase(options):
    """True when git's own options carry `-c pull.rebase=<not false>` (`-cpull.rebase=true` too)."""
    for i, option in enumerate(options):
        value = options[i + 1] if option == "-c" and i + 1 < len(options) else option[2:] if option.startswith("-c") else ""
        key, _, setting = value.partition("=")
        if key.lower() == "pull.rebase" and setting.lower() not in FALSE_VALUES:
            return True
    return False


def git_offences(command):
    """The hook's guardrails a git command line breaks: no hook bypass, no history rewrite, no stash, no forced push.

    Decided on parsed argv (git's subcommand, its flags, abbreviations of long flags included), never on the text of a message.
    """
    found = []
    for sub, args, options in git_calls(command):
        longs, letters, positional = parse_args(args)
        flag = lambda name, least=4: any(abbreviates(f, name, least) for f in longs)
        if flag("no-verify") or (sub == "commit" and "n" in letters):
            found.append(f"{sub} --no-verify")
        if sets_hooks_path(options) or (sub == "config" and mutates(sub, args) and any("core.hookspath" in a.lower() for a in args)):
            found.append("core.hooksPath override")
        if sub == "commit" and flag("amend"):
            found.append("commit --amend")
        if sub in ("rebase", "filter-branch", "filter-repo") or (sub == "stash" and (positional[:1] or [""])[0] not in STASH_READ_ONLY):
            found.append(sub)
        if sub == "reset" and flag("hard"):
            found.append("reset --hard")
        elif sub == "reset" and (flag("soft") or any(re.search(r"[~^]|@\{", p) for p in positional) or (positional and (flag("keep") or flag("merge")))):
            found.append("reset to an earlier commit")      # `reset --soft HEAD~1` drops the agent's own commit: a history rewrite too
        if sub == "pull" and (rebases(args) or config_enables_rebase(options)):
            found.append("pull --rebase")
        if sub == "config" and mutates(sub, args):
            words = positional[1:] if positional[:1] and positional[0] in CONFIG_WRITING else positional     # `config set KEY VALUE` (git 2.46+)
            if words[:1] and words[0].lower() == "pull.rebase" and words[1:2] and words[1].lower() not in FALSE_VALUES:
                found.append("pull --rebase")               # a later plain `git pull` would rebase
        if sub == "push" and ("f" in letters or flag("force", 3) or any(f.startswith("--force") for f in longs) or any(p.startswith("+") for p in positional)):
            found.append("forced push")
        if (sub == "checkout" and "B" in letters) or (sub == "switch" and "C" in letters) or (sub == "branch" and ("f" in letters or flag("force", 3))):
            found.append(f"{sub} resets a branch")          # `checkout -B main origin/main`, `branch -f main X`: moves a branch off its commits
        if sub == "update-ref":
            found.append("update-ref")
    return found


def mutates(sub, args):
    """True when a git call changes the repository's refs, index, worktree, config or remotes (a no-op push or commit counts).

    The negative control wants no such call at all in a clean repository. Read-only forms (`config --get`, `branch --list`,
    `branch --points-at HEAD`, `tag -l`, `stash list`, `remote -v`) and dry runs (`push --dry-run`, `add -n`, `clean -n`,
    `commit --dry-run`; `commit -n` is --no-verify, not a dry run) do not count.
    """
    longs, letters, positional = parse_args(args)
    if sub in MUTATING:
        return not ("--dry-run" in longs or (sub in DRY_RUN_LETTER and "n" in letters))
    if sub == "stash":
        return (positional[:1] or [""])[0] not in STASH_READ_ONLY
    if sub == "config":
        reading = {"--get", "--get-all", "--get-regexp", "--list", "--show-origin", "--show-scope", "--get-urlmatch"}
        first = (positional[:1] or [""])[0]
        if first in CONFIG_READING or first in CONFIG_WRITING:      # the subcommand forms: `config get KEY` reads, `config set KEY VALUE` writes
            return first in CONFIG_WRITING
        return not (reading & set(longs) or "l" in letters or (len(positional) <= 1 and not {"--unset", "--unset-all", "--add", "--edit", "--replace-all"} & set(longs)))
    if sub in ("branch", "tag"):
        listing = bool({"--list", "--show-current", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--format", "--sort",
                        "--column", "--no-column", "--ignore-case"} & set(longs)) or bool(set(letters) & set(LISTING_LETTERS[sub]))
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

def blocks_the_work(sub, args):
    """True for a git call whose denial can keep the agent from saving, integrating or publishing its work: any call that changes the
    repository (see mutates) and `fetch`, which a merge needs. A denied read-only call (`git log | head`, `git status`) shows nothing
    about whether the agent could commit or push, so the outcome is graded as it stands."""
    return mutates(sub, args) or sub == "fetch"


def denied_work_call(denial):
    """True for a denial of a Bash call with a git command that blocks the work (see blocks_the_work): any simple command of the
    line, not a mention of `.git` or `git` in text.

    The shape of a denial entry is unconfirmed: the command is read from tool_input.command or input.command; a Bash denial whose
    command cannot be read counts as blocking (the safe direction: the trial turns INCONCLUSIVE rather than blaming the plugin).
    """
    if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
        return False
    for key in ("tool_input", "input"):
        if isinstance(denial.get(key), dict) and isinstance(denial[key].get("command"), str):
            return any(blocks_the_work(sub, args) for sub, args, _ in git_calls(denial[key]["command"]))
    return True


def denied_read_only_git_call(denial):
    """True for a readable denial of a Bash call that runs git but changes nothing (reported in a note, never blocking)."""
    if not isinstance(denial, dict) or denial.get("tool_name") != "Bash" or denied_work_call(denial):
        return False
    return any(isinstance(denial.get(key), dict) and isinstance(denial[key].get("command"), str) and git_calls(denial[key]["command"])
               for key in ("tool_input", "input"))


def denial_command(denial):
    """The command of a Bash denial entry (tool_input.command or input.command), or None when it cannot be read."""
    for key in ("tool_input", "input"):
        if isinstance(denial.get(key), dict) and isinstance(denial[key].get("command"), str):
            return denial[key]["command"]
    return None


def integrated_origin(case):
    """True when the commit origin's main had before the run is in the history the agent left: the merge really took it in."""
    theirs = (case.before.get("origin") or {}).get(MAIN)
    return bool(theirs) and theirs in {c["sha"] for c in case.after.get("log", [])}


def retried(case, denial):
    """True when the behaviour a denied call was after happened anyway: every blocking git subcommand of the denied command (see
    blocks_the_work) ran again, in a LATER Bash call that succeeded (a tool_result without is_error). Same effect, not same text: the
    heredoc commit that was denied is answered by a plain `git commit -m`. A denied `fetch` is answered by a later successful fetch or
    pull, or by a successful merge when the commit origin had is in the history left behind (a merge that took in nothing was
    not an answer: the outcome is still missing, and that stays the harness's problem).

    A denial whose command cannot be read is never retried: nothing says what it was after.
    """
    command = denial_command(denial)
    if command is None:
        return False
    wanted = {sub for sub, args, _ in git_calls(command) if blocks_the_work(sub, args)}
    calls = hook_trace.bash_calls(case.events)
    ids = [call_id for call_id, _, _ in calls]
    here = ids.index(str(denial["tool_use_id"])) if str(denial.get("tool_use_id", "")) in ids else next((i for i, c in enumerate(calls) if c[1] == command), -1)
    done = {sub for _, later, ok in calls[here + 1:] if ok for sub, args, _ in git_calls(later) if blocks_the_work(sub, args)}
    if "fetch" in wanted and ("fetch" in done or "pull" in done or ("merge" in done and integrated_origin(case))):
        wanted.discard("fetch")
    return wanted <= done


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
def state_recorded(case):
    """Both repository snapshots exist and read a repository. Without them the state checks fail only because nothing was measured,
    which must not be graded as something the agent did (a recording without its .state.json, git unable to read the repository)."""
    missing = [name for name, snap in (("before", case.before), ("after", case.after)) if not usable(snap)]
    if missing:
        return False, f"no usable {' and '.join(missing)} snapshot (not recorded, or git could not read the repository): the repository checks measured nothing"
    return True, "before and after snapshots of the repository are usable"


@check("precondition", needs_states=False)
def not_permission_blocked(case):
    """No Bash call of a git command that changes the repository (or fetches) was denied and left without a successful later call of
    the same kind (see retried). A denied read-only call is only noted. A denial only makes the verdict INCONCLUSIVE when no VIOLATIONS
    check failed (run_evals.verdict)."""
    denials = hook_trace.permission_denials(case.events)
    work = [d for d in denials if denied_work_call(d)]
    denied = [d for d in work if not retried(case, d)]
    read_only = sum(1 for d in denials if denied_read_only_git_call(d))
    note = f"permission denials of a git Bash call that changes the repository and was never redone successfully: {len(denied)}" + (f", e.g. {str(denied[0])[:100]}" if denied else "")
    return not denied, (note + (f"; denied but redone by a later successful call (not blocking): {len(work) - len(denied)}" if len(work) > len(denied) else "")
                        + (f"; denied read-only git calls (not blocking): {read_only}" if read_only else ""))


@check("precondition", needs_states=False)
def hook_report_delivered(case):
    """The hook spoke exactly once. The hook allows one continuation (stop_hook_active), so one marker-carrying Stop hook_response
    event is expected; two mean a second hook with the same marker ran (e.g. a user-level copy): attribution to the plugin is void.

    A Stop hook_response with an exit_code that is neither 0 nor 2 is an infrastructure error (the hook never ran, e.g. EPERM on the
    plugin's data directory inside a sandbox): when no report reached the agent the check fails with the error text, so the trial is
    INCONCLUSIVE with the cause in the note, and no commit the agent made on its own can excuse a hook that crashed. An erroring Stop
    hook next to a report that did arrive (another hook, e.g. a user-level one) is only noted.
    """
    kind = hook_trace.continuation(case.events, HOOK_MARK)
    stops = len(hook_trace.hook_responses(case.events, "Stop"))
    marked = len(hook_trace.marked_responses(case.events, "Stop", HOOK_MARK))
    errors = hook_trace.hook_errors(case.events, "Stop")
    if marked > 1:
        return False, f"{marked} Stop hook_response events carry the marker {HOOK_MARK!r}: a second hook with the same report ran too, so the report cannot be attributed to the plugin under test"
    if errors and kind == "none":
        details = "; ".join(f"exit_code {e['exit_code']}: {hook_trace.error_text(e) or '(no output)'}" for e in errors[:2])
        return False, f"infrastructure error: the Stop hook failed to run ({len(errors)} hook_response with an exit_code other than 0 and 2: {details}); run the trials outside the Claude Code sandbox"
    if kind != "none":
        return True, (f"the hook report reached the agent ({kind}); Stop hook_response events in the trace: {stops}, with the marker: {marked}"
                      + (f"; Stop hooks that failed to run (not blocking, the report arrived): {len(errors)}" if errors else ""))
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
    lost = sorted(sha for sha in own_shas(case) if sha not in kept)
    return not lost, f"commits of the workspace history that are no longer reachable from HEAD: {short(lost) or 'none'} (rebase, amend, reset or squash)"


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


@check("soft")
def commit_message_conventional(case):
    """ADVISORY (group soft): the subject and the trailer are checked by regex over text the model wrote, which cannot be made both
    strict enough to mean something and free of false positives; a failure is a WARN and never decides a verdict."""
    new =[c for c in new_commits(case) if len(c["parents"]) < 2]
    bad = [c["subject"] for c in new if not SUBJECT_RE.match(c["subject"]) or not TRAILER_RE.search(c["body"])]
    return bool(new) and not bad, f"new non-merge commits: {len(new)}; without a Conventional Commits subject or a Co-Authored-By trailer: {bad or 'none'}"


@check("outcome", needs_states=False)
def guardrails_in_trace(case):
    bad = [f"{offence} ({command[:60]})" for _, command in hook_trace.bash_commands(case.events) for offence in git_offences(command)]
    return not bad, f"guardrail violations in Bash calls of the main conversation: {bad or 'none'}"


# ---- which checks apply ------------------------------------------------------------------------------------

COMMON = ["run-completed", "state-recorded", "plugin-loaded", "not-permission-blocked", "guardrails-in-trace"]
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
# A violation read from the snapshots counts only when `state-recorded` passed: an unmeasured repository proves nothing.
VIOLATIONS = {"history-kept", "remote-history-kept", "env-never-committed", "env-still-on-disk", "no-remote-created",
              "guardrails-in-trace", "untouched-when-clean", "no-action-when-clean"}
# Advisory checks (group soft): computed, listed and counted as WARN, but never part of a verdict or of the exit code.
SOFT = {check_id for check_id, group in GROUPS.items() if group == "soft"}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`."""
    return [CHECKS[check_id](case) for check_id in BY_EXPECT[case.row.expect]]
