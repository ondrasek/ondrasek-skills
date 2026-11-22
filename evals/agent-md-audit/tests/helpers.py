"""Builders of invented traces and workspace snapshots. No model call, no network.

`good(expect, **override)` returns (row, events, states) of a run that passes every check for that `expect`; each override breaks
one thing, so a test can show that a given check fails (see test_checks.MUTATIONS).
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_common" / "python"))  # after python/: the shared modules win

import cases  # noqa: E402
import fixtures  # noqa: E402
import state  # noqa: E402

SKILL = "agent-md-audit:agent-md-audit"
DOCS = "https://code.claude.com/docs/en/memory"
SHORT_CLAUDE = "# Demo project\n\n@AGENTS.md\n"
_ids = itertools.count(1)

PARTS = {
    "verdict": "Verdicts: CLAUDE.md truncate, AGENTS.md keep.",
    "table": "| # | Problem |\n| - | - |\n| 1 | first |\n| 2 | second |\n| 3 | third |",
    "dead-import": "Finding: `@docs/missing-guide.md` does not resolve.",
    "stale-command": "Finding: `npm run typecheck` is not a script.",
    "emphasis": "Finding: line 3 uses IMPORTANT and ALL CAPS.",
    "contradiction": "Finding: the last two lines contradict each other, tabs against 2-space.",
    "size": "Finding: the file has 229 lines.",
    "sources": f"Source: {DOCS}",
    "confidence": "Each finding is marked documented or inferred.",
    "approval": "Which of these should I apply?",
}


def proposal_reply(omit=()):
    return "\n\n".join(text for key, text in PARTS.items() if key not in omit)


def init(skills=(SKILL,), session="stub-session"):
    return {"type": "system", "subtype": "init", "skills": list(skills), "session_id": session}


def call(name, mid=None, **tool_input):
    block = {"type": "tool_use", "id": f"t{next(_ids)}", "name": name, "input": tool_input}
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"id": mid or f"m{next(_ids)}", "content": [block]}}


def result(text, session="stub-session"):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 3,
            "total_cost_usd": 0.5, "duration_ms": 1000, "session_id": session}


DEFAULT_CALLS = [("Skill", {"skill": SKILL}), ("WebFetch", {"url": DOCS}), ("Read", {"file_path": "/w/CLAUDE.md"})]


def good(expect, omit=(), init_skills=(SKILL,), calls1=None, reply1=None, reply2="CLAUDE.md: 229 → 3 lines. AGENTS.md untouched.",
         after1_edit=None, after2_claude=SHORT_CLAUDE, after2_agents=None, after2_head="h1", after2_extra=None, should_trigger=True):
    fixture = {"none": "bare", "keep": "clean"}.get(expect, "defects")
    row = cases.Row(f"t-{expect}", expect != "none" and should_trigger, "explicit", fixture, expect, "p", "approve" if expect == "apply" else "")
    original = fixtures.build(fixture)
    if calls1 is None:
        calls1 = [("Read", {"file_path": "/w/src/index.ts"})] if expect == "none" else DEFAULT_CALLS
    if reply1 is None:
        reply1 = {"none": "Done.", "keep": f"Verdict: CLAUDE.md keep. Source: {DOCS}"}.get(expect) or proposal_reply(omit)
    events = [init(init_skills)] + [call(name, **tool_input) for name, tool_input in calls1] + [result(reply1)]
    before = state.snapshot_from(original, head="h1")
    after1 = state.snapshot_from({**original, **(after1_edit or {})}, head="h1")
    states = {"before": before, "after1": after1, "after2": None}
    if expect == "apply":
        events += [init(init_skills), call("Edit", file_path="/w/CLAUDE.md"), result(reply2)]
        files2 = {**original, "CLAUDE.md": after2_claude}
        if after2_agents is not None:
            files2["AGENTS.md"] = after2_agents
        files2.update(after2_extra or {})
        states["after2"] = state.snapshot_from(files2, head=after2_head)
    return row, events, states
