"""Repository state: the ground truth of the evals.

A trace says what the agent claims to have done; git says what it did. `snapshot` records the workspace (every file outside
`.git`: sha256, line count and, for a small UTF-8 file, its text) and the git facts the checks need, all computed here so the
checks stay pure functions of two snapshots (before the agent, after it):

    refs, config             {ref: sha} of every workspace ref (branches, tags, remote-tracking) and the text of .git/config: a
                             branch, tag or config change on a clean repository shows even when HEAD does not move
    head, status, log        HEAD sha, a porcelain-shaped status (XY path lines, `??` for untracked files, one per file; computed by
                             `_status` without `git status`, see below), the commits reachable from HEAD
    tracked                  {path: blob id} of the HEAD tree (committed content; staged-only work does not count)
    all_paths                every path in the tree of any commit object in the workspace repository, reachable or not (a commit
                             the agent dropped with `reset --soft` still counts)
    remotes                  names of the configured remotes
    origin, origin_commits, origin_paths
                             the same facts read from the bare remote: {ref: sha}, `rev-list --all`, every path of every commit
                             object in it (a branch the agent pushed and deleted still counts, as long as the objects are not pruned)

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


# The repositories' .git/config and .gitattributes are writable by the agent, and the snapshots run git on the host after it (outside
# any sandbox). Two defences. 1: these -c options (they beat every config file) switch off the settings that make git run a program
# named in the config. 2: the snapshot never runs `git status`, `diff`, `ls-files --modified` or anything else that compares the
# worktree with the index through git's clean filters (a `filter.X.clean` command named in .git/config and selected by .gitattributes
# runs on the host when git re-reads a stat-dirty file; a test plants one): `_status` hashes the worktree files in Python and uses
# only plumbing that does not look at file contents (ls-files -s, ls-files --others, ls-tree, cat-file, log, for-each-ref, rev-list).
# Other config-named programs were not probed one by one; .git/config is read as text and never executed.
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
    """Every path in the tree of every commit object of the repository (loose or packed), reachable from a ref or not.

    `git log --all` would miss a commit the agent dropped (`reset --soft HEAD~1`, a deleted branch on the bare remote); the
    object database still has it until a prune.
    """
    listing = _git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objecttype) %(objectname)").splitlines()
    paths = set()
    for line in listing:
        kind, _, sha = line.partition(" ")
        if kind == "commit":
            paths.update(_git(repo, "ls-tree", "-r", "--name-only", "-z", sha).split("\0"))
    return sorted(paths - {""})


def _index(repo):
    """{path: [(mode, blob id, stage)]} of the index (`ls-files -s`: reads the index, never a file's contents)."""
    entries = {}
    for item in _git(repo, "ls-files", "-s", "-z").split("\0"):
        if item:
            meta, path = item.split("\t", 1)
            mode, sha, stage = meta.split()
            entries.setdefault(path, []).append((mode, sha, int(stage)))
    return entries


def _worktree_blob(path):
    """The git blob id of the file or symlink at `path` as it is on disk (no clean filter, no eol conversion), None when there is none."""
    try:
        if os.path.islink(path):
            return blob_id(os.fsencode(os.readlink(path)))
        return blob_id(Path(path).read_bytes()) if os.path.isfile(path) else None
    except OSError:
        return None


def _status(repo, tracked):
    """A `git status --porcelain --untracked-files=all` text computed without `git status` (see SAFE_GIT for why).

    X compares the index with the HEAD tree (A, M, D), Y the worktree with the index (M, D); a path with a conflict stage is `UU`;
    untracked files (`ls-files --others --exclude-standard`) are `??`, one line per file, after the tracked changes. No rename
    detection (a staged rename is a D and an A), and file modes, clean filters and eol conversion are ignored: a file counts as
    modified when its raw bytes differ from the index blob.
    """
    index, lines = _index(repo), []
    for path in sorted(set(index) | set(tracked)):
        versions = index.get(path)
        if versions and any(stage for _, _, stage in versions):
            lines.append(f"UU {path}")
            continue
        x = " " if tracked.get(path) == (versions[0][1] if versions else None) else "D" if not versions else "A" if path not in tracked else "M"
        y = " "
        if versions and versions[0][0] != "160000":          # a gitlink is a directory on disk: not compared
            actual = _worktree_blob(Path(repo) / path)
            y = "D" if actual is None else " " if actual == versions[0][1] else "M"
        if (x, y) != (" ", " "):
            lines.append(f"{x}{y} {path}")
    untracked = sorted(p for p in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p)
    return "".join(line + "\n" for line in lines + [f"?? {p}" for p in untracked])


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
        snap.update(head=_git(root, "rev-parse", "HEAD").strip(), log=_log(root), tracked=_tracked(root), all_paths=_paths(root),
                    remotes=_git(root, "remote").split(), refs=_refs(root), config=_config_text(root))
        snap["status"] = _status(root, snap["tracked"])
        if origin:
            snap.update(origin=_refs(origin), origin_commits=_git(origin, "rev-list", "--all").split(),
                        origin_paths=_paths(origin))
    except RuntimeError:
        return _empty() | {"files": snap["files"]}
    return snap
