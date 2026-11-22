"""Workspace and report state: the ground truth of the evals.

A trace says what the agent claims to have done; the files say what it did. Two things are recorded after each turn, so that
recorded runs grade offline and the checks stay pure functions:

    snapshot(ws)             every file under the workspace (not `.git`): sha256, line count and, for a small UTF-8 file, its
                             text; the git HEAD and `git status`. The workspace must not change: the report lives elsewhere.
    report_snapshot(dir)     the report directory report.sh made (outside the workspace): which files it holds, the text of
                             data.js and what the pure-Python reader (sitrep_data.parse) makes of it. Parsing happens here.

A trial lives in one temporary root: root/ws is the workspace and the agent's cwd, root/tmp is the child's TMPDIR (report.sh
creates and sweeps `sitrep.*` only there), root/hooks is an empty hooks directory for the fixture repo.
"""
import hashlib
import os
import re
import subprocess
from pathlib import Path

import fixtures
import hook_trace
import sitrep_data

MAX_TEXT_BYTES = 100_000
# Claude Code's own write-tracking marker; nothing else under .claude/ is ignored, so a .claude/settings.json still counts.
# Whether a headless run creates it in the workspace is unconfirmed until the first real run.
IGNORED_PREFIX = ".claude/.cc-writes/"
# the user's git configuration must not reach the throwaway repository; the repository's own local config is what the agent sees
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
SITREP_GLOB = "sitrep.*"
# the spellings SKILL.md prescribes: `"${CLAUDE_SKILL_DIR}/scripts/report.sh" new` and `... report.sh" clean DIR` (quote before the verb)
REPORT_NEW_RE = re.compile(r"report\.sh[\"']?\s+new\b")
REPORT_CLEAN_RE = re.compile(r"report\.sh[\"']?\s+clean\b")
_REPORT_PATH_RE = re.compile(r"(/[^\s\"']*/sitrep\.[A-Za-z0-9]+)(?=$|[\s\"'/])", re.MULTILINE)


def _git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env={**os.environ, **GIT_ENV})
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def prepare(name, root):
    """Build the trial layout under `root`: fixture `name` as a committed, clean git repository in root/ws, plus root/tmp and root/hooks.

    Returns {"root", "ws", "tmp"} as resolved path strings. The agent itself may run git here, so identity, signing and hooks are set in the
    repository's local config, not on a command line.
    """
    root = Path(root).resolve()
    ws, tmp, hooks = root / "ws", root / "tmp", root / "hooks"
    for directory in (ws, tmp, hooks):
        directory.mkdir()
    for rel, content in fixtures.build(name).items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    _git(ws, "init", "-q", "-b", "main")
    for key, value in (("user.name", "eval"), ("user.email", "eval@example.invalid"), ("commit.gpgsign", "false"),
                       ("tag.gpgsign", "false"), ("core.hooksPath", str(hooks))):
        _git(ws, "config", key, value)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "fixture")
    return {"root": str(root), "ws": str(ws), "tmp": str(tmp)}


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


def snapshot(ws):
    """The state of the workspace at `ws`."""
    ws = Path(ws)
    files = {}
    for path in sorted(ws.rglob("*")):
        rel = path.relative_to(ws).as_posix()
        if rel == ".git" or rel.startswith(".git/") or not path.is_file() or rel.startswith(IGNORED_PREFIX):
            continue
        files[rel] = entry(path.read_bytes())
    try:
        head = _git(ws, "rev-parse", "HEAD").strip()
        status = _git(ws, "status", "--porcelain")
    except RuntimeError:
        head, status = "", ""
    return {"files": files, "head": head, "status": status}


def sitrep_dirs(tmp):
    """The names of the `sitrep.*` directories directly under `tmp`, sorted."""
    return sorted(p.name for p in Path(tmp).glob(SITREP_GLOB) if p.is_dir())


def find_report(events, tmp):
    """(report directory or None, how it was found): the directory `report.sh new` printed, else a sitrep.* directory under `tmp`.

    The trace comes first because a sandbox may give Bash another TMPDIR than the one set for the trial; a printed path that does
    not exist is the last resort (the snapshot then records exists=false).
    """
    results = hook_trace.tool_results(events)
    printed = []
    for call_id, command in hook_trace.bash_commands(events):
        if REPORT_NEW_RE.search(command):
            printed += [os.path.realpath(p) for p in _REPORT_PATH_RE.findall(results.get(call_id, ""))]
    existing = [p for p in printed if os.path.isdir(p)]
    globbed = [os.path.realpath(os.path.join(tmp, name)) for name in sitrep_dirs(tmp)]
    for pool, how in ((existing, "trace"), (globbed, "glob"), (printed, "trace, but the directory is gone")):
        if pool:
            return pool[-1], how
    return None, "not found"


def report_record(directory, contents, template_sha256, located_by=""):
    """The record of a report directory. `contents` is {name: bytes, or None for a subdirectory}, or None when the directory is absent."""
    exists = contents is not None
    contents = contents or {}
    data = contents.get("data.js")
    text = data.decode("utf-8", "replace") if data is not None else None
    parsed, error = sitrep_data.parse(text) if text is not None else (None, "the report has no data.js")
    return {"dir": directory, "exists": exists, "located_by": located_by, "template_sha256": template_sha256,
            "files": {name: hashlib.sha256(raw).hexdigest() if raw is not None else "<directory>" for name, raw in contents.items()},
            "data_text": text, "parsed": parsed, "parse_error": error}


def report_snapshot(directory, template, located_by=""):
    """The state of the report directory (None: none was found) next to the plugin's template.html at `template`."""
    template_sha256 = hashlib.sha256(Path(template).read_bytes()).hexdigest()
    if directory is None:
        return report_record(None, None, template_sha256, located_by)
    directory = Path(directory)
    contents = {p.name: p.read_bytes() if p.is_file() else None for p in sorted(directory.iterdir())} if directory.is_dir() else None
    return report_record(str(directory), contents, template_sha256, located_by)
