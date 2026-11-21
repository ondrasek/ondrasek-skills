"""Workspace state: the ground truth of the evals.

A trace says what the agent claims to have done; the files say what it did. `snapshot` records every file under the
workspace (not `.git`): its sha256, line count and, for a small UTF-8 file, its text, plus the git HEAD and `git status`.
A run takes three: before the agent, after turn 1 (the proposal: nothing may have changed) and after turn 2 (the apply).
"""
import hashlib
import os
import subprocess
from pathlib import Path

import fixtures

MAX_TEXT_BYTES = 100_000
# Claude Code's own write-tracking marker; nothing else under .claude/ is ignored, so an unapproved .claude/settings.json still counts.
# Whether a headless run creates it in the workspace is unconfirmed until the first real run.
IGNORED_PREFIX = ".claude/.cc-writes/"
GIT = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "-c", "commit.gpgsign=false",
       "-c", f"core.hooksPath={os.devnull}", "-c", "init.defaultBranch=main"]


def _git(root, *args):
    done = subprocess.run(GIT + list(args), cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def prepare(name, dest):
    """Write fixture `name` into directory `dest`, make it a git repository, commit everything (tracked and clean)."""
    root = Path(dest)
    for rel, content in fixtures.build(name).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def entry(data):
    """The snapshot record of one file's bytes."""
    record = {"sha256": hashlib.sha256(data).hexdigest(), "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)}
    if len(data) <= MAX_TEXT_BYTES:
        try:
            record["text"] = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return record


def snapshot_from(files, head="", status=""):
    """A snapshot built from {path: text}; the same shape `snapshot` returns (used by the unit tests)."""
    return {"files": {path: entry(text.encode("utf-8")) for path, text in files.items()}, "head": head, "status": status}


def snapshot(root):
    """The state of the workspace at `root`."""
    root = Path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/") or not path.is_file() or rel.startswith(IGNORED_PREFIX):
            continue
        files[rel] = entry(path.read_bytes())
    try:
        head = _git(root, "rev-parse", "HEAD").strip()
        status = _git(root, "status", "--porcelain")
    except RuntimeError:
        head, status = "", ""
    return {"files": files, "head": head, "status": status}
