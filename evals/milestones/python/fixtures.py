"""Fixture repositories for the evals, built in code.

They are generated at run time, never stored as files: a committed `.cache/milestones.json` or CLAUDE.md under evals/ would be
read by the plugin's hook and statusline and by Claude Code for anyone who works in this repository.

    bare        a git repository with README.md, src/textutil.py and a .gitignore holding `.cache/`; no tracker
    tracker     bare plus .cache/milestones.json: 1 Schema design done, 2 Storage layer doing, 3 Sync API todo, 4 Release sign-off todo
    stale-task  bare plus a tracker whose item 2 ("Add slugify to textutil") is doing; the file is two hours old
    stale-idle  the tracker fixture with the same two-hour age (nothing in a prompt will finish a milestone)

The tracker is gitignored, so the fixture commit (`git add -A`) leaves it untracked. state.prepare backdates the mtime of the stale
fixtures with os.utime after that commit, so the commit is newer than the tracker (the hook's activity condition); staleness comes from
the mtime, not from MILESTONES_STALE_MINUTES. Times inside the trackers are relative to the moment this module was imported.
"""
import json
from datetime import datetime, timedelta, timezone

TRACKER = ".cache/milestones.json"
MARKER = ".cache/.milestones-reminded"       # touched by the hook just before it exits 2
AGE_MINUTES = {"stale-task": 120, "stale-idle": 120}   # tracker mtime age set by state.prepare; the others keep a fresh file

README = "# textutil\n\nSmall text helpers.\n"
TEXTUTIL = 'def collapse_spaces(text):\n    return " ".join(text.split())\n'


# one reading of the clock per process: every build() of a fixture is identical (a second boundary between two builds would otherwise
# differ in the times inside the tracker)
CLOCK = datetime.now(timezone.utc).replace(microsecond=0)


def _ago(hours):
    return (CLOCK - timedelta(hours=hours)).isoformat(timespec="seconds")


def _text(tracker):
    return json.dumps(tracker, ensure_ascii=False, indent=2) + "\n"


def _bare():
    return {"README.md": README, "src/textutil.py": TEXTUTIL, ".gitignore": ".cache/\n"}


def _tracker():
    items = [
        {"id": 1, "name": "Schema design", "state": "done", "started": _ago(6), "finished": _ago(5)},
        {"id": 2, "name": "Storage layer", "state": "doing", "left": "migrations", "started": _ago(3)},
        {"id": 3, "name": "Sync API", "state": "todo"},
        {"id": 4, "name": "Release sign-off", "state": "todo"},
    ]
    return {**_bare(), TRACKER: _text({"title": "To release 1.0", "updated": _ago(3), "items": items})}


def _stale_task():
    items = [
        {"id": 1, "name": "Create the textutil module", "state": "done", "started": _ago(6), "finished": _ago(5)},
        {"id": 2, "name": "Add slugify to textutil", "state": "doing", "left": "the function", "started": _ago(3)},
        {"id": 3, "name": "Document textutil", "state": "todo"},
    ]
    return {**_bare(), TRACKER: _text({"title": "textutil", "updated": _ago(3), "items": items})}


FIXTURES = {"bare": _bare, "tracker": _tracker, "stale-task": _stale_task, "stale-idle": _tracker}


def build(name):
    """{relative path: text} of fixture `name`; raises KeyError for an unknown name."""
    return FIXTURES[name]()
