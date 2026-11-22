"""Builders of invented traces and repository snapshots. No model call, no network.

`good(case_id, **override)` returns (row, events, states) of a run of a shipped case that passes every check that applies to it.
Each override breaks one thing, so a test can show that a given check fails (see test_checks.MUTATIONS):

    hook         'fed' (default; 'none' for the untouched case) | 'event-only' | 'none': how the hook report shows in the trace
    commands     the Bash commands of the trace
    plugins, plugin_errors, denials      the init event's plugins and plugin_errors, the result event's permission_denials
    init_kwargs  extra arguments of init() (source, path); result_kwargs  of result() (subtype, is_error); no_result  drop the result event
    after        snapshot fields that replace the good run's (head, status, log, tracked, origin, remotes, ...)
    files_add, files_drop                files added to / removed from the after snapshot
    results      None (default: the trace has no tool_result events) | a list with one bool per command (True: the call succeeded,
                 False: its tool_result is an error, as a denied call's is; None: no tool_result at all) | True for every command succeeding

The trace shapes are modelled on recorded traces (init, assistant tool_use, result). How a Stop-hook block shows up in a real
trace is NOT known: the `fed` event below is an invented encoding, and checks only search it for the report's first words.
"""
import sys
from pathlib import Path

UNIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UNIT / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_common" / "python"))  # after python/: the shared modules win

import cases  # noqa: E402
import checks  # noqa: E402
import fixtures  # noqa: E402
import state  # noqa: E402

ROWS = {row.id: row for row in cases.load_cases(UNIT / "cases" / "cases.csv")}
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
REPORT = f"{checks.HOOK_MARK}: the repository is not saved and in sync.\n\nState:\n- untracked files (not gitignored):\n    notes/todo.md"
COMMIT = f'git commit -q -m "chore: snapshot work in progress" -m "{TRAILER}"'
COMMANDS = ["git status --porcelain", "git add -A", COMMIT, "git push -q origin main"]
_ids = iter(range(1, 10_000))


# ---- trace -------------------------------------------------------------------------------------------------

PLUGIN_DIR = UNIT.parents[1] / "plugins" / "auto-commit"


def init(plugins=(checks.PLUGIN,), errors=(), source="{}@inline", path=str(PLUGIN_DIR)):
    """The init event: each plugin as recorded by Claude Code (name, source, path); `--plugin-dir` plugins have the source `<name>@inline`."""
    return {"type": "system", "subtype": "init", "session_id": "stub-session",
            "plugins": [{"name": p, "source": source.format(p), "path": path} for p in plugins], "plugin_errors": list(errors)}


def bash(command):
    block = {"type": "tool_use", "id": f"t{next(_ids)}", "name": "Bash", "input": {"command": command}}
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"id": f"m{next(_ids)}", "content": [block]}}


def tool_result(call_event, error=False):
    """The user event that answers the tool_use of `call_event` (an event made by bash())."""
    block = call_event["message"]["content"][0]
    return {"type": "user", "parent_tool_use_id": None, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": block["id"], "is_error": error, "content": "denied" if error else "ok"}]}}


def hook_report():
    """INVENTED encoding of the Stop hook's stderr reaching the model."""
    return {"type": "user", "parent_tool_use_id": None, "message": {"role": "user", "content": [{"type": "text", "text": f"Stop hook blocking error: {REPORT}"}]}}


def hook_event():
    """The plugin's report as Claude Code 2.1.330 records it: hook_response, exit_code 2, outcome 'error' (not an infrastructure error)."""
    return {"type": "system", "subtype": "hook_response", "hook_name": "Stop", "hook_event": "Stop", "output": REPORT, "stdout": "", "stderr": REPORT,
            "exit_code": 2, "outcome": "error"}


def hook_started():
    return {"type": "system", "subtype": "hook_started", "hook_name": "Stop", "hook_event": "Stop"}


EPERM = "Failed to run: EPERM: operation not permitted, mkdir '/Users/x/.claude/plugins/data/auto-commit-inline'"


def hook_infra_error(exit_code=1, output=EPERM):
    """INVENTED trace of a Stop hook that never ran, in the shape recorded when Claude Code ran inside a sandbox: exit_code 1 (not 0 or 2)."""
    return {"type": "system", "subtype": "hook_response", "hook_name": "Stop", "hook_event": "Stop", "output": output, "stdout": "", "stderr": output,
            "exit_code": exit_code, "outcome": "error"}


def result(text="done", denials=(), cost=0.5, subtype="success", is_error=False):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "num_turns": 4, "total_cost_usd": cost,
            "duration_ms": 1000, "session_id": "stub-session", "permission_denials": list(denials)}


# ---- repository --------------------------------------------------------------------------------------------

def commit(sha, parents, subject, body=TRAILER):
    return {"sha": sha, "parents": list(parents), "subject": subject, "body": body}


C1 = commit("c1", [], "docs: add readme", "")
C2 = commit("c2", ["c1"], "feat: add shout", "")
LOCAL = commit("l1", ["c2"], fixtures.LOCAL_SUBJECT, "")


def before_snapshot(fixture):
    """The workspace and remote of fixture `fixture` as the agent finds them (the topology test_state.py verifies for real)."""
    spec = fixtures.FIXTURES[fixture]
    committed = {**fixtures.BASE, **spec.local}
    log = ([LOCAL] if spec.local else []) + [C2, C1]
    status = "".join(f"{' M' if path in committed else '??'} {path}\n" for path in spec.loose)
    fields = {"head": log[0]["sha"], "status": status, "log": log, "remotes": ["origin"] if spec.remote else [],
              "tracked": {p: state.blob_id(t.encode()) for p, t in committed.items()},
              "all_paths": sorted({*fixtures.BASE, *spec.local, *spec.remote_commit})}
    if spec.remote:
        fields.update(origin={checks.MAIN: "o1" if spec.remote_commit else "c2"}, origin_commits=(["o1"] if spec.remote_commit else []) + ["c2", "c1"],
                      origin_paths=sorted({*fixtures.BASE, *spec.remote_commit}))
    return state.snapshot_from(fixtures.build(fixture), **fields)


def after_snapshot(row, before):
    """What a good agent leaves: its edit and everything but .env committed, origin merged and pushed (not when .env is involved)."""
    if row.expect == "untouched":
        return dict(before)
    files = {path: record["text"] for path, record in before["files"].items()}
    if row.edits:
        files[row.edits] += "\n# edited by the agent\n"
    tracked = {p: t for p, t in files.items() if not checks.is_env(p)}
    work = commit("w1", [before["head"]], "chore: snapshot work in progress")
    new, theirs = [work], []
    if before["origin"] and before["origin"][checks.MAIN] not in {c["sha"] for c in before["log"]}:
        new.insert(0, commit("m1", ["w1", before["origin"][checks.MAIN]], "Merge branch 'main' of origin", ""))
        theirs = [commit(before["origin"][checks.MAIN], ["c2"], fixtures.REMOTE_SUBJECT, "")]
    fields = {"head": new[0]["sha"], "log": new + before["log"] + theirs, "status": "?? .env\n" if ".env" in files else "",
              "tracked": {p: state.blob_id(t.encode()) for p, t in tracked.items()}, "remotes": before["remotes"],
              "all_paths": sorted({*before["all_paths"], *tracked})}
    if before["origin"] and row.expect != "saved-no-env":
        fields.update(origin={checks.MAIN: new[0]["sha"]}, origin_commits=[c["sha"] for c in new] + before["origin_commits"],
                      origin_paths=sorted({*before["origin_paths"], *tracked}))
    else:
        fields.update(origin=before["origin"], origin_commits=before["origin_commits"], origin_paths=before["origin_paths"])
    return state.snapshot_from(files, **fields)


def good(case_id, hook=None, commands=None, plugins=(checks.PLUGIN,), plugin_errors=(), denials=(), after=None, files_add=None, files_drop=(),
         init_kwargs=None, result_kwargs=None, no_result=False, results=None):
    row = ROWS[case_id]
    untouched = row.expect == "untouched"
    hook = hook or ("none" if untouched else "fed")
    commands = ([] if untouched else COMMANDS) if commands is None else commands
    before = before_snapshot(row.fixture)
    final = after_snapshot(row, before)
    final["files"] = {p: r for p, r in final["files"].items() if p not in files_drop}
    final["files"].update({p: state.entry(t.encode()) for p, t in (files_add or {}).items()})
    final.update(after or {})
    events = [init(plugins, plugin_errors, **(init_kwargs or {}))] + {"fed": [hook_report()], "event-only": [hook_event()], "none": []}[hook]
    oks = [True] * len(commands) if results is True else results
    for i, command in enumerate(commands):
        call = bash(command)
        events += [call] + ([tool_result(call, error=not oks[i])] if oks is not None and oks[i] is not None else [])
    events += [] if no_result else [result(denials=denials, **(result_kwargs or {}))]
    return row, events, {"before": before, "after": final}


def call_id(events, index):
    """The tool_use id of the Bash call number `index` (0-based) of a trace: what a real denial entry carries as tool_use_id."""
    return [e["message"]["content"][0]["id"] for e in events if e["type"] == "assistant"][index]
