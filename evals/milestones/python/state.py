"""Workspace state: the ground truth of the evals.

A trace says what the agent claims to have done; the files say what it did. `snapshot` records every file under the workspace
(not `.git`): its sha256, line count and, for a small UTF-8 file, its text, plus the git facts the checks need (HEAD, the tracked
paths, every path any commit ever touched). A run takes two: before the agent and after it. All git facts are computed here, at
snapshot time, so that the checks stay pure functions of the snapshots.

A trial lives in a temporary directory:

    <root>/ws      the workspace and the agent's cwd, a git repository
    <root>/hooks   an empty directory that core.hooksPath points at (outside the workspace)
"""
import hashlib
import os
import subprocess
import time
from pathlib import Path

import fixtures

MAX_TEXT_BYTES = 100_000
# Claude Code's own write-tracking marker; nothing else under .claude/ is ignored. Whether a headless run creates it in the workspace
# is unconfirmed until the first real run.
IGNORED_PREFIX = ".claude/.cc-writes/"
# Whatever runs git here (the harness, a stub, the agent) must not read the user's global or system git config.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def local_config(hooks_dir):
    """The fixture repository's local git config. The agent runs git itself, so identity passed with -c would not be enough."""
    return {"user.name": "eval", "user.email": "eval@example.invalid", "commit.gpgsign": "false", "tag.gpgsign": "false",
            "core.hooksPath": str(hooks_dir)}


# Variables that would point git at another repository: a harness started from a git hook has them set. Neither the harness's git nor the
# agent may inherit them (the agent command unsets them with `env -u`, see run_evals.DEFAULT_AGENT_CMD).
GIT_UNSET = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX")
# Options for the harness's own git calls AFTER the agent ran: the agent can write the workspace's .git/config, and a config key can name a
# program (core.fsmonitor, core.hooksPath, log.showSignature with gpg.program). Overriding them with -c wins over the file. Not a complete
# defence (an include.path or another exec key could exist), only the known keys; the agent's own Bash has the same rights anyway.
SAFE_GIT = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-c", "log.showSignature=false", "-c", "core.pager=cat",
            "-c", "diff.external=")


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if k not in GIT_UNSET}
    done = subprocess.run(["git", *SAFE_GIT, *args], cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, env={**env, **GIT_ENV})
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def prepare(name, root):
    """Build fixture `name` under `root` (see the module docstring); returns the workspace path.

    The files are committed (what is not gitignored is tracked and clean), then the mtime of a stale fixture's tracker is set back.
    """
    root = Path(root)
    ws, hooks = root / "ws", root / "hooks"
    ws.mkdir()
    hooks.mkdir()
    for rel, content in fixtures.build(name).items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    _git(ws, "init", "-q", "-b", "main")
    for key, value in local_config(hooks).items():
        _git(ws, "config", key, value)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "fixture")
    if name in fixtures.AGE_MINUTES:
        old = time.time() - fixtures.AGE_MINUTES[name] * 60
        os.utime(ws / fixtures.TRACKER, (old, old))
    return ws


def entry(data):
    """The snapshot record of one file's bytes."""
    record = {"sha256": hashlib.sha256(data).hexdigest(), "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)}
    if len(data) <= MAX_TEXT_BYTES:
        try:
            record["text"] = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return record


def snapshot_from(files, head="", tracked=None, all_paths=None):
    """A snapshot built from {path: text}; the same shape `snapshot` returns (used by the unit tests).

    `tracked` and `all_paths` default to the files outside .cache/, as a fixture commit would hold them.
    """
    committed = sorted(p for p in files if not p.startswith(".cache/"))
    return {"files": {path: entry(text.encode("utf-8")) for path, text in files.items()}, "head": head,
            "tracked": committed if tracked is None else list(tracked), "all_paths": committed if all_paths is None else list(all_paths)}


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
        tracked = sorted(_git(root, "ls-files").splitlines())
        all_paths = sorted({p for p in _git(root, "log", "--all", "--name-only", "--pretty=format:").splitlines() if p})
    except RuntimeError:
        head, tracked, all_paths = "", [], []
    return {"files": files, "head": head, "tracked": tracked, "all_paths": all_paths}
