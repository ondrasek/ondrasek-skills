"""Builders of invented traces and workspace snapshots. No model call, no network.

`good(expect, **override)` returns (row, events, states) of a run that passes every check for that `expect`; each override breaks
one thing, so a test can show that a given check fails (see test_checks.MUTATIONS).
"""
import copy
import json
import itertools
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import cases  # noqa: E402
import fixtures  # noqa: E402
import state  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO / "plugins" / "milestones"
SCRIPT = PLUGIN_DIR / "skills" / "milestones" / "scripts" / "milestones.py"
SKILL_ID = "milestones:milestones"
FUTURE = "2030-01-01T12:00:00+00:00"       # what the invented script writes as updated, started and finished
HOOK_REPORT = "Stop hook feedback:\n\nMILESTONES HOOK: the milestone tracker may be out of date."
NOTHING = object()                         # an override value: "no tracker file after the run"
SAME = object()                            # an override value: "the tracker is as before"
_ids = itertools.count(1)

SHOW = f'python3 "{SCRIPT}" show'
SET_2_DONE = f'python3 "{SCRIPT}" set 2 --state done --left ""'
SET_3_DOING = f'python3 "{SCRIPT}" set 3 --state doing'
INIT_EMPTY = f'python3 "{SCRIPT}" init --empty --title Importer'
ADDS = [f'python3 "{SCRIPT}" add --name "{name}"' for name in ("Design the schema", "Build the importer", "Write the migration guide")]

WANT = {"update": "2=done,3=doing", "create": "title=Importer,count=3,names=schema|importer|migration", "reminded-update": "2=done"}
FIXTURE = {"show": "tracker", "update": "tracker", "create": "bare", "none": "bare", "reminded-update": "stale-task", "reminded-quiet": "stale-idle"}
CALLS = {
    "show": [("Skill", {"skill": SKILL_ID}), ("Bash", {"command": SHOW})],
    "update": [("Skill", {"skill": SKILL_ID}), ("Bash", {"command": SET_2_DONE}), ("Bash", {"command": SET_3_DOING})],
    "create": [("Skill", {"skill": SKILL_ID}), ("Bash", {"command": INIT_EMPTY})] + [("Bash", {"command": c}) for c in ADDS],
    "none": [("Read", {"file_path": "/w/README.md"})],
    "reminded-update": [("Edit", {"file_path": "/w/src/textutil.py"}), ("Skill", {"skill": SKILL_ID}), ("Bash", {"command": SET_2_DONE})],
    "reminded-quiet": [("Read", {"file_path": "/w/src/textutil.py"})],
}
REPLY = {"show": "Storage layer is in progress; Sync API is next.", "update": "Done.", "create": "Created the tracker.", "none": "A milestone marks a point in a project.",
         "reminded-update": "Added slugify.", "reminded-quiet": "It collapses runs of spaces."}


def tracker(name):
    """The parsed tracker of fixture `name`."""
    return json.loads(fixtures.build(name)[fixtures.TRACKER])


def moved(data, changes, left_cleared=()):
    """A copy of tracker `data` with {id: state} applied as milestones.py would: started, finished and updated written."""
    out = copy.deepcopy(data)
    out["updated"] = FUTURE
    for ident, new in changes.items():
        item = next(i for i in out["items"] if i["id"] == ident)
        item["state"] = new
        if new == "doing":
            item.setdefault("started", FUTURE)
        if new == "done":
            item["finished"] = FUTURE
        else:
            item.pop("finished", None)
        if ident in left_cleared:
            item.pop("left", None)
    return out


def init(plugins=("milestones",), skills=(SKILL_ID,), session="stub-session", entries=None):
    """`entries`: the raw plugins[] list, instead of one inline entry per name in `plugins`."""
    listed = entries if entries is not None else [{"name": p, "source": f"{p}@inline"} for p in plugins]
    return {"type": "system", "subtype": "init", "plugins": listed, "skills": list(skills), "session_id": session}


def call(name, mid=None, **tool_input):
    block = {"type": "tool_use", "id": f"t{next(_ids)}", "name": name, "input": tool_input}
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"id": mid or f"m{next(_ids)}", "content": [block]}}


def tool_result(ident, text, is_error=False):
    """The user event that carries a tool's output (recorded shape: content is a string)."""
    block = {"type": "tool_result", "tool_use_id": ident, "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": [block]}}


def show_output(data):
    """What `milestones.py show` prints for tracker `data`, abridged: the table's milestone names, then the title line with the bar."""
    names = "\n".join(f"{i['id']}  {i['name']}" for i in data["items"])
    return f"#  Milestone\n{names}\n\n{data['title']}  ██▌░░░  1/{len(data['items'])} done"


def user_text(text):
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": text}]}}


def hook_response(exit_code, outcome, output="", event="Stop"):
    """The system event of a finished hook, in the shape recorded from Claude Code 2.1.330 (--include-hook-events)."""
    return {"type": "system", "subtype": "hook_response", "hook_name": event, "hook_event": event, "output": output, "stdout": "", "stderr": output,
            "exit_code": exit_code, "outcome": outcome}


# a Stop reminder that fires and asks for work: exit 2, outcome 'error' (NOT an infrastructure error)
STOP_REMINDER = hook_response(2, "error", HOOK_REPORT)
# a hook that never ran: seen when Claude Code itself ran inside a sandbox
EPERM = "Failed to run: EPERM: operation not permitted, mkdir '/Users/x/.claude/plugins/data/milestones-inline'"
HOOK_EPERM = hook_response(1, "error", EPERM)


def result(text, denials=(), session="stub-session"):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 3, "total_cost_usd": 0.5, "duration_ms": 1000,
            "permission_denials": list(denials), "session_id": session}


def text_of_tracker(data):
    return data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"


def good(expect, fixture=None, want=None, plugins=("milestones",), skills=(SKILL_ID,), calls=None, reply=None, denials=(), hook=True,
         after_tracker=None, after_extra=None, marker=None, after_head="h1", committed=(), src_edit=None, entries=None, outputs=None,
         result_error=None, hook_events=()):
    """A passing run. `after_tracker`: a dict, a raw text, NOTHING, SAME; default: what the case wants. `hook`: the report reaches the model.
    `entries`: the raw plugins[] of the init event. `outputs`: {command: (text, is_error)} replacing the tool_result of a Bash call.
    `hook_events`: system events put right after init (hook_started/hook_response). `result_error`: a result subtype (such as error_max_budget_usd) for a result event with is_error set."""
    fixture = fixture or FIXTURE[expect]
    row = cases.Row(f"t-{expect}", expect, fixture, cases.parse_want(WANT.get(expect, "") if want is None else want), "p")
    before_files = fixtures.build(fixture)
    reminded = expect.startswith("reminded")
    before_tracker = json.loads(before_files[fixtures.TRACKER]) if fixtures.TRACKER in before_files else None
    events = [init(plugins, skills, entries=entries), *hook_events]
    if reminded and hook:
        events.append(user_text(HOOK_REPORT))
    for name, tool_input in (CALLS[expect] if calls is None else calls):
        event = call(name, **tool_input)
        events.append(event)
        if name == "Bash":      # the script's output: the table for show, a short note for the rest
            command = tool_input["command"]
            default = (show_output(before_tracker), False) if re.search(r"milestones\.py\"?\s+show", command) and before_tracker else ("ok", False)
            events.append(tool_result(event["message"]["content"][0]["id"], *(outputs or {}).get(command, default)))
    events.append(result(REPLY[expect] if reply is None else reply, denials))
    if result_error:
        events[-1].update(subtype=result_error, is_error=True)
    if after_tracker is None:
        after_tracker = {"update": moved(before_tracker, {2: "done", 3: "doing"}) if before_tracker else None,
                         "reminded-update": moved(before_tracker, {2: "done"}, left_cleared=(2,)) if before_tracker else None,
                         "create": {"title": "Importer", "updated": FUTURE, "items": [{"id": n, "name": name, "state": "todo"} for n, name in
                                                                                       enumerate(("Design the schema", "Build the importer", "Write the migration guide"), 1)]}}.get(expect, SAME)
    after_files = dict(before_files)
    if after_tracker is NOTHING:
        after_files.pop(fixtures.TRACKER, None)
    elif after_tracker is not SAME:
        after_files[fixtures.TRACKER] = text_of_tracker(after_tracker)
    if (reminded and marker is None) or marker:
        after_files[fixtures.MARKER] = ""
    if src_edit or (src_edit is None and expect == "reminded-update"):
        after_files["src/textutil.py"] = before_files["src/textutil.py"] + "\n\ndef slugify(text):\n    return text\n"
    after_files.update(after_extra or {})
    before = state.snapshot_from(before_files, head="h1")
    committed = list(committed)
    base = before["tracked"]
    after = state.snapshot_from(after_files, head=after_head, tracked=sorted(base + committed), all_paths=sorted(base + committed))
    return row, events, {"before": before, "after": after}
