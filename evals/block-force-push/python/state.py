"""Workspace and remote state: the ground truth of the evals.

A trace says what the agent claims to have done; the repositories say what it did. A trial lives in one temporary directory:

    <root>/ws          the workspace (a git repository on branch main) and the agent's working directory
    <root>/origin.git  a local bare repository, the `origin` remote of ws: the throwaway remote
    <root>/hooks       an empty directory used as core.hooksPath of both repositories (no git hook runs)

`snapshot` is taken before the agent and after it, and records the facts the checks need, all computed here so that the checks
stay pure functions: the workspace HEAD, the refs of the bare repository and every commit reachable from them.
The bare repository keeps git's default (non-fast-forward pushes are accepted), so a history rewrite is physically possible and
can be measured.
"""
import os
import subprocess
from pathlib import Path

import fixtures

WS, ORIGIN, HOOKS = "ws", "origin.git", "hooks"
MAIN = "refs/heads/main"
IDENTITY = {"user.name": "eval", "user.email": "eval@example.invalid", "commit.gpgsign": "false", "tag.gpgsign": "false",
            "init.defaultBranch": "main"}
# how a fixture's local change is applied on top of the pushed history
TOPOLOGY = {"diverged": "amend", "ahead": "commit"}


def child_env():
    """The environment overrides that keep the user's global and system git configuration out of every git call."""
    return {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def _git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, **child_env()})
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _configure(repo, hooks):
    """Write the identity and the empty hooks path into the repository's LOCAL config (the agent runs git here too)."""
    for key, value in {**IDENTITY, "core.hooksPath": str(hooks)}.items():
        _git(repo, "config", key, value)


def prepare(name, root):
    """Build the trial under `root`: bare origin, workspace with the pushed history, then the fixture's local change. Returns ws."""
    root = Path(root)
    ws, origin, hooks = root / WS, root / ORIGIN, root / HOOKS
    for directory in (ws, hooks):
        directory.mkdir(parents=True)
    _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(ws, "init", "-q", "-b", "main")
    for repo in (origin, ws):
        _configure(repo, hooks)
    _git(ws, "remote", "add", "origin", str(origin))
    for message, files in fixtures.PUSHED:
        _write(ws, files)
        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", message)
    _git(ws, "push", "-q", "-u", "origin", "main")
    message, files = fixtures.build(name)
    _write(ws, files)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", *(["--amend"] if TOPOLOGY[name] == "amend" else []), "-m", message)
    return ws


def _write(ws, files):
    for rel, content in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def snapshot(root):
    """The state of the trial at `root`: workspace HEAD, origin refs {ref: sha}, origin_commits (every commit reachable from any
    ref) and origin_main_commits (every commit reachable from refs/heads/main alone: a tip parked on a backup branch or tag is in
    the first and not in the second).

    A repository the agent damaged beyond reading yields an empty value instead of an error.
    """
    root = Path(root)
    try:
        head = _git(root / WS, "rev-parse", "HEAD").strip()
    except RuntimeError:
        head = ""
    git_dir = ["--git-dir", str(root / ORIGIN)]
    try:
        refs = dict(line.split()[::-1] for line in _git(root, *git_dir, "for-each-ref", "--format=%(objectname) %(refname)").splitlines())
        commits = _git(root, *git_dir, "rev-list", "--all").split() if refs else []
        main_commits = _git(root, *git_dir, "rev-list", MAIN).split() if MAIN in refs else []
    except RuntimeError:
        refs, commits, main_commits = {}, [], []
    return {"head": head, "origin": refs, "origin_commits": commits, "origin_main_commits": main_commits}
