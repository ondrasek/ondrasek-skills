"""Repository state: the ground truth of the evals.

A trace says what the agent claims to have done; git says what it did. `snapshot` records the workspace (every file outside
`.git`: sha256, line count and, for a small UTF-8 file, its text) and the git facts the checks need, all computed here so the
checks stay pure functions of two snapshots (before the agent, after it):

    refs, config             {ref: sha} of every workspace ref (branches, tags, remote-tracking) and the text of .git/config: a
                             branch, tag or config change on a clean repository shows even when HEAD does not move
    head, status, log        HEAD sha, `git status --porcelain --untracked-files=all`, the commits reachable from HEAD
    tracked                  {path: blob id} of the HEAD tree (committed content; staged-only work does not count)
    all_paths                every path that appears in any commit of the workspace repository (`git log --all`)
    remotes                  names of the configured remotes
    origin, origin_commits, origin_paths
                             the same facts read from the bare remote: {ref: sha}, `rev-list --all`, every path in its history

Trial layout (see prepare): <root>/ws is the workspace and the agent's cwd, <root>/origin.git the bare remote.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import fixtures

MAX_TEXT_BYTES = 100_000
# Paths the snapshot and the checks do not see: Claude Code's own bookkeeping under .claude/ (the .cc-writes marker may or may not
# appear in a headless run; unconfirmed) and .cache/, which the hook itself always ignores.
IGNORED_PREFIXES = (".claude/", ".cache/")
# git must read neither the user's nor the system config, and no GIT_* variable of the caller. The agent gets the same two.
GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
IDENTITY = ("eval", "eval@example.invalid")


# The repositories' .git/config is writable by the agent, and the snapshots run git on the host after it (outside any sandbox): these
# -c options (they beat every config file) switch off the settings that make git run a program named in the config. Residual risk,
# not probed: other config-named programs. The snapshot reads .git/config as text and never runs it.
SAFE_GIT = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-c", "core.untrackedCache=false",
            "-c", "log.showSignature=false")


def _git(cwd, *args):
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **GIT_ISOLATION}
    done = subprocess.run(["git", *SAFE_GIT, *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _configure(repo, root):
    """The repository's LOCAL config: the agent itself runs git here, so identity passed with -c would not be enough.

    No signing, no git hooks (an empty directory), and an empty excludes file: the user's global gitignore must not hide the .env
    fixture. (Checked with git 2.56.1: the default $XDG_CONFIG_HOME/git/ignore is still read when GIT_CONFIG_GLOBAL is /dev/null,
    and a local core.excludesFile overrides it.)
    """
    hooks, excludes = Path(root) / "hooks", Path(root) / "excludes"
    hooks.mkdir(exist_ok=True)
    excludes.write_text("", encoding="utf-8")
    for key, value in (("user.name", IDENTITY[0]), ("user.email", IDENTITY[1]), ("commit.gpgsign", "false"), ("tag.gpgsign", "false"),
                       ("core.hooksPath", str(hooks)), ("core.excludesFile", str(excludes)), ("init.defaultBranch", "main")):
        _git(repo, "config", "--local", key, value)


def _write(repo, files):
    for rel, content in files.items():
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _commit(repo, subject, files):
    _write(repo, files)
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "-q", "-m", subject)


def prepare(name, root):
    """Build fixture `name` under `root`; returns (workspace, bare origin or None when the fixture has no remote).

    The history is built in the order the evals need: base commits, push, the local-only commit, the commit someone else put on
    origin (made in a throwaway clone), a fetch in the workspace so that the hook sees it, and the loose working-tree changes.
    """
    root, spec = Path(root), fixtures.FIXTURES[name]
    ws, origin = root / "ws", root / "origin.git"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    _configure(ws, root)
    for subject, paths in fixtures.BASE_COMMITS:
        _commit(ws, subject, {p: fixtures.BASE[p] for p in paths})
    if spec.remote:
        _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        _git(ws, "remote", "add", "origin", str(origin))
        _git(ws, "push", "-q", "-u", "origin", "main")
    if spec.local:
        _commit(ws, fixtures.LOCAL_SUBJECT, spec.local)
    if spec.remote_commit:
        clone = root / "clone"
        _git(root, "clone", "-q", str(origin), str(clone))
        _configure(clone, root)
        _commit(clone, fixtures.REMOTE_SUBJECT, spec.remote_commit)
        _git(clone, "push", "-q", "origin", "main")
        shutil.rmtree(clone)
        _git(ws, "fetch", "-q", "origin")
    _write(ws, spec.loose)
    return ws, (origin if spec.remote else None)


def blob_id(data):
    """The id git gives a blob with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def entry(data):
    """The snapshot record of one file's bytes."""
    record = {"sha256": hashlib.sha256(data).hexdigest(), "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)}
    if len(data) <= MAX_TEXT_BYTES:
        try:
            record["text"] = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return record


def _empty():
    return {"files": {}, "head": "", "status": "", "log": [], "tracked": {}, "all_paths": [], "remotes": [], "refs": {}, "config": "",
            "origin": {}, "origin_commits": [], "origin_paths": []}


def snapshot_from(files, **fields):
    """A snapshot built from {path: text}: every file committed, nothing else known. `fields` override the other keys.

    The same shape `snapshot` returns (used by the unit tests to invent runs).
    """
    snap = _empty()
    unknown = set(fields) - set(snap)
    if unknown:
        raise KeyError(f"unknown snapshot fields {sorted(unknown)}")
    snap["files"] = {path: entry(text.encode("utf-8")) for path, text in files.items()}
    snap["tracked"] = {path: blob_id(text.encode("utf-8")) for path, text in files.items()}
    snap["all_paths"] = sorted(files)
    snap.update(fields)
    return snap


def _log(repo):
    out = _git(repo, "log", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", "HEAD")
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, parents, subject, body = record.split("\x1f", 3)
            commits.append({"sha": sha, "parents": parents.split(), "subject": subject, "body": body.strip()})
    return commits


def _tracked(repo):
    tracked = {}
    for item in _git(repo, "ls-tree", "-r", "-z", "HEAD").split("\0"):
        if item:
            meta, path = item.split("\t", 1)
            tracked[path] = meta.split()[2]
    return tracked


def _paths(repo):
    return sorted(set(_git(repo, "-c", "core.quotePath=false", "log", "--all", "--name-only", "--format=").split("\n")) - {""})


def _refs(repo):
    refs = (line.split(" ", 1) for line in _git(repo, "for-each-ref", "--format=%(objectname) %(refname)").splitlines())
    return {ref: sha for sha, ref in refs}


def _config_text(repo):
    try:
        return (Path(repo) / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def snapshot(root, origin=None):
    """The state of the workspace at `root` and, when `origin` is the bare remote, of the remote."""
    root = Path(root)
    real = os.path.realpath(root)
    snap = _empty()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        # a symlink, or a file reached through one, is skipped: the agent could point one at a file outside the workspace and the
        # snapshot would store its text
        if rel == ".git" or rel.startswith(".git/") or path.is_symlink() or not path.is_file() or rel.startswith(IGNORED_PREFIXES) \
                or os.path.realpath(path) != os.path.join(real, *rel.split("/")):
            continue
        snap["files"][rel] = entry(path.read_bytes())
    try:
        snap.update(head=_git(root, "rev-parse", "HEAD").strip(), status=_git(root, "status", "--porcelain", "--untracked-files=all"),
                    log=_log(root), tracked=_tracked(root), all_paths=_paths(root), remotes=_git(root, "remote").split(),
                    refs=_refs(root), config=_config_text(root))
        if origin:
            snap.update(origin=_refs(origin), origin_commits=_git(origin, "rev-list", "--all").split(),
                        origin_paths=_paths(origin))
    except RuntimeError:
        return _empty() | {"files": snap["files"]}
    return snap
