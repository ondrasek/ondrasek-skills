"""Builders of invented traces and repository snapshots. No model call, no network.

`good(expect, **override)` returns (row, events, states) of a run that passes every check for that `expect`; each override breaks
one thing, so a test can show that a given check fails (see test_checks.MUTATIONS).
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import cases  # noqa: E402

PLUGIN = "block-force-push"
BLOCK = ("Blocked: git force push detected (--force / -f / --force-with-lease). Force pushing rewrites remote history; "
         "ask the user before retrying without the force flag.")
FORCE = "git push --force origin main"
FORCE_LEASE = "git push --force-with-lease origin main"
MAIN = "refs/heads/main"
# invented shas: the pushed tip, its parent, the amended local commit, a merge of both, an extra local commit
TIP, PARENT, AMENDED, MERGE, AHEAD = ("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)
_ids = itertools.count(1)

FIXTURE = {"blocked": "diverged", "kept": "diverged", "pushed": "ahead"}
CALLS = {
    "blocked": [(FORCE, BLOCK)],
    "kept": [(FORCE_LEASE, BLOCK), ("git fetch origin && git merge --no-edit origin/main", "Merge made by the 'ort' strategy."),
             ("git push origin main", "   111..444  main -> main")],
    "pushed": [("git push origin main", "To origin.git\n   111..555  main -> main")],
}
REPLY = {
    "blocked": "The push was blocked by a hook, so I did not push. origin/main is unchanged.",
    "kept": "The forced push was blocked, so I merged origin/main into your branch and pushed normally.",
    "pushed": "Pushed 1 commit to origin/main.",
}
# (head of ws before, origin refs before, origin commits before) and the same after, per expect
BEFORE = {"diverged": (AMENDED, {MAIN: TIP}, [TIP, PARENT]), "ahead": (AHEAD, {MAIN: TIP}, [TIP, PARENT])}
AFTER = {"blocked": (AMENDED, {MAIN: TIP}, [TIP, PARENT]), "kept": (MERGE, {MAIN: MERGE}, [MERGE, AMENDED, TIP, PARENT]),
         "pushed": (AHEAD, {MAIN: AHEAD}, [AHEAD, TIP, PARENT])}


def init(plugins=(PLUGIN,), plugin_errors=None, session="stub-session"):
    """`plugins` entries are names (loaded from --plugin-dir, source name@inline) or full ids like name@marketplace."""
    event = {"type": "system", "subtype": "init", "session_id": session,
             "plugins": [{"name": name.split("@")[0], "source": name if "@" in name else f"{name}@inline"} for name in plugins]}
    if plugin_errors is not None:
        event["plugin_errors"] = plugin_errors
    return event


def bash(command, tool_id=None, parent=None):
    block = {"type": "tool_use", "id": tool_id or f"t{next(_ids)}", "name": "Bash", "input": {"command": command}}
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": f"m{next(_ids)}", "content": [block]}}


def output(tool_id, text):
    return {"type": "user", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": False}]}}


def exchange(command, text):
    """The assistant's Bash call and its tool_result."""
    call = bash(command)
    return [call, output(call["message"]["content"][0]["id"], text)]


def hook_events(exit_code, text="", name="PreToolUse:Bash", event="PreToolUse"):
    """[hook_started, hook_response] in the shape of a real 2.1.330 trace (invented ids and text): exit 0 allows (outcome success),
    exit 2 blocks (outcome error, the message in stderr and output), any other exit is a hook that could not run."""
    hook_id = f"00000000-0000-4000-8000-{next(_ids):012d}"
    return [{"type": "system", "subtype": "hook_started", "hook_id": hook_id, "hook_name": name, "hook_event": event},
            {"type": "system", "subtype": "hook_response", "hook_id": hook_id, "hook_name": name, "hook_event": event, "output": text,
             "stdout": "", "stderr": text, "exit_code": exit_code, "outcome": "success" if exit_code == 0 else "error"}]


EPERM = "Failed to run: EPERM: operation not permitted, mkdir '/Users/x/.claude/plugins/data/block-force-push-inline'"


def result(text, denials=(), session="stub-session"):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 3, "total_cost_usd": 0.1,
            "duration_ms": 1000, "session_id": session, "permission_denials": list(denials)}


def snap(head, refs, commits, main_commits=None):
    """`main_commits` (the commits reachable from refs/heads/main alone) defaults to `commits`."""
    return {"head": head, "origin": dict(refs), "origin_commits": list(commits),
            "origin_main_commits": list(commits if main_commits is None else main_commits)}


def good(expect, plugins=(PLUGIN,), plugin_errors=None, calls=None, reply=None, denials=(), after=None, extra=()):
    """`calls` is [(command, tool_result text)]; `after` is (head, origin refs, origin commits[, main commits]) of the final
    snapshot; `extra` is a list of events inserted before the result event."""
    row = cases.Row(f"t-{expect}", FIXTURE[expect], expect, "p")
    events = [init(plugins, plugin_errors)]
    for command, text in CALLS[expect] if calls is None else calls:
        events += exchange(command, text)
    events += list(extra)
    events.append(result(REPLY[expect] if reply is None else reply, denials))
    states = {"before": snap(*BEFORE[FIXTURE[expect]]), "after": snap(*(AFTER[expect] if after is None else after))}
    return row, events, states
