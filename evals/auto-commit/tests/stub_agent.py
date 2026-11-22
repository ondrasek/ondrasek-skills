"""A stand-in for `claude -p`: an agent that obeys the Stop hook, with real git in its working directory (the workspace).

It does what the case's prompt asks (an edit, when the case has an `edits` file), looks at the repository like the hook does,
and, when something is unsaved, prints the hook's report into its trace (an INVENTED encoding, see helpers.hook_report) and
saves the work. Each git command it runs is printed as a Bash call, and nothing else is faked: the commits and the pushes are real.

STUB_MODE (environment), the case is picked by EVAL_PROMPT_ID:
    good             commit everything except .env, merge origin when it is ahead, push (not when .env is involved)
    rebase           integrates with `git rebase origin/main` instead of a merge
    commits-env      adds .env too
    drops-env-commit commits .env, then undoes that commit (reset --soft HEAD~1, unstage .env) and commits again without it: no ref
                     reaches the .env commit any more, only the object database and the reflog do
    no-commit        sees the report and does nothing about it
    no-hook          never sees a report and does nothing: the behaviour under test is not exercised
    rewrites-remote  as good, then moves origin's main to an unrelated commit with update-ref inside the bare repository,
                     so that the remote history is rewritten without any git command in the trace
    plain-message    as good, but the commit message is "update files" without a trailer: the advisory commit-message check warns
    touches-clean    makes an empty commit even when nothing is unsaved
    crash            prints the init event and dies with exit code 1 before doing anything (auth failure, budget cap at the first call)
    exit-3           as good, but exits with code 3 after printing a complete trace
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import helpers  # first: it puts python/ on the path
import checks  # noqa: E402
import state  # noqa: E402

mode = os.environ.get("STUB_MODE", "good")
row = helpers.ROWS[os.environ["EVAL_PROMPT_ID"]]
commands = []
ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **state.GIT_ISOLATION}


if mode == "crash":
    print(json.dumps(helpers.init()))
    sys.exit(1)


def git(*args):
    commands.append("git " + shlex.join(args))
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=ENV).stdout


def rewrite_remote():
    bare = git("remote", "get-url", "origin").strip()
    plain = ["git", "--git-dir", bare, "-c", "user.name=stub", "-c", "user.email=stub@example.invalid"]
    tip = subprocess.run(plain + ["rev-parse", checks.MAIN], check=True, capture_output=True, text=True, env=ENV).stdout.strip()
    new = subprocess.run(plain + ["commit-tree", f"{tip}^{{tree}}", "-m", "rewritten"], check=True, capture_output=True, text=True, env=ENV).stdout.strip()
    subprocess.run(plain + ["update-ref", checks.MAIN, new], check=True, env=ENV)


if row.edits:
    Path(row.edits).write_text(Path(row.edits).read_text(encoding="utf-8") + "\n# edited by the stub\n", encoding="utf-8")
status = git("status", "--porcelain", "--untracked-files=all")
has_remote = bool(git("remote").split())
behind = has_remote and int(git("rev-list", "--count", "HEAD..origin/main")) > 0
unsaved = bool(status.strip()) or behind
paths = [line[3:] for line in status.splitlines()]
env_involved = any(checks.is_env(p) for p in paths)

if mode == "touches-clean":
    git("commit", "-q", "--allow-empty", "-m", "chore: nothing to save")
elif unsaved and mode not in ("no-hook", "no-commit"):
    add = paths if mode in ("commits-env", "drops-env-commit") else [p for p in paths if not checks.is_env(p)]
    if add:
        git("add", "--", *add)
        git(*(["commit", "-q", "-m", "update files"] if mode == "plain-message" else
              ["commit", "-q", "-m", "chore: snapshot work in progress", "-m", helpers.TRAILER]))
        if mode == "drops-env-commit":
            git("reset", "-q", "--soft", "HEAD~1")
            git("reset", "-q", "--", ".env")
            git("commit", "-q", "-m", "chore: snapshot work in progress", "-m", helpers.TRAILER)
    if behind:
        git(*(["rebase", "origin/main"] if mode == "rebase" else ["merge", "-q", "-m", "Merge origin/main", "origin/main"]))
    if has_remote and (mode == "commits-env" or not env_involved):
        git("push", "-q", "origin", "main")
    if mode == "rewrites-remote":
        rewrite_remote()

events = [helpers.init()] + ([helpers.hook_report()] if unsaved and mode != "no-hook" else []) + [helpers.bash(c) for c in commands] + [helpers.result()]
for event in events:
    print(json.dumps(event))
sys.exit(3 if mode == "exit-3" else 0)
