"""Shared by the offline tests: the python/ path, the paths of the unit, and builders for small invented traces."""
import json
import os
import shlex
import sys
import unittest
from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent
PYTHON = UNIT / "python"
TESTS = UNIT / "tests"
TRACES = TESTS / "fixtures" / "traces"
PROMPTS = UNIT / "evals" / "prompts.csv"
sys.path.insert(0, str(PYTHON))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_common" / "python"))  # after python/: the shared modules win

import cases  # noqa: E402
import checks  # noqa: E402
import agent_trace as trace  # noqa: E402

ROWS = {row.id: row for row in cases.load_prompts(PROMPTS)}


def command(script, *args):
    """A command string that runs a Python script with this interpreter (quoted for the runner's splitter)."""
    parts = [sys.executable, str(script), *args]
    return " ".join(f'"{p}"' for p in parts) if os.name == "nt" else shlex.join(parts)


def stub_agent():
    return command(TESTS / "stub_agent.py")


def stub_judge():
    return command(TESTS / "stub_judge.py")


def make_events(reply, called=("oaat:oaat",), skills=("oaat:oaat",), turns=2, cost=0.01, others=(), subtype="success", is_error=False):
    """The events of a small INVENTED trace: init, Skill tool calls, other tool calls, the reply and the result."""
    events = []
    if skills is not None:
        events.append({"type": "system", "subtype": "init", "session_id": "INVENTED-unit-test", "skills": list(skills), "tools": ["Skill"]})
    for name in called:
        events.append({"type": "assistant", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED", "name": "Skill", "input": {"skill": name}}]}})
    for name in others:
        events.append({"type": "assistant", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED2", "name": name, "input": {}}]}})
    events.append({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": reply}]}})
    events.append({"type": "result", "subtype": subtype, "is_error": is_error, "result": reply, "num_turns": turns, "total_cost_usd": cost})
    return events


def case_for(prompt_id, reply, **kwargs):
    """A Case for `reply`. A row with a second message gets two turns: GOOD_ITEM answers the first message (it loads the skill,
    so `called` and `skills` go to it), and `reply` answers the second; the other arguments go to the second turn."""
    row = ROWS[prompt_id]
    if not row.reply:
        return checks.Case.build(row, make_events(reply, **kwargs))
    first = {key: kwargs.pop(key) for key in ("called", "skills") if key in kwargs}
    events = make_events(GOOD_ITEM, **first) + make_events(reply, called=(), skills=("oaat:oaat",), **kwargs)
    return checks.Case.build(row, events)


def verdicts(case):
    """{check id: (passed, note)} of every check that applies to the case."""
    return {cid: (ok, note) for cid, ok, note in checks.run_checks(case)}


GOOD_ITEM = (
    "5 items need you. First one:\n\n"
    "**Item 1 of 5, decision: Pick the database**\n"
    "**What it is:** The first release needs one database.\n"
    "**Why it matters:** The choice sets hosting cost.\n"
    "**Options:**\n"
    "1. Postgres: more setup, many users.\n"
    "2. SQLite: no server, one writer.\n"
    "**Recommendation:** Option 1, because growth is expected.\n"
    "**One question:** Which database should the first release use?")


# ---- the tool path (AskUserQuestion): INVENTED traces; the event shape is the assumed one, UNCONFIRMED (README, "The tool path") ----
COUNT_LINE = "5 items need you. First one:"
GOOD_TOOL_INPUT = {"questions": [{
    "header": "Decision",
    "question": ("Item 1 of 5, decision: Pick the database. The first release needs one database. "
                 "The choice sets hosting cost and the backup plan. Which database should the first release use?"),
    "multiSelect": False,
    "options": [
        {"label": "Postgres (Recommended)", "description": "More setup, handles many users; growth is expected."},
        {"label": "SQLite", "description": "No server, one writer at a time."},
        {"label": "Skip for now", "description": "Park this item and move on."},
    ],
}]}


def make_tool_events(inputs=(GOOD_TOOL_INPUT,), text=COUNT_LINE, called=("oaat:oaat",), searches=(), turns=2, tools=("Skill", "AskUserQuestion"), cost=0.01):
    """The events of one INVENTED turn on the tool path: init, Skill calls, ToolSearch calls, then an assistant message with an
    optional text block and one AskUserQuestion tool_use per entry of `inputs`, and the result event (the text is the reply)."""
    events = [{"type": "system", "subtype": "init", "session_id": "INVENTED-unit-test", "skills": ["oaat:oaat"], "tools": list(tools)}]
    for name in called:
        events.append({"type": "assistant", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED", "name": "Skill", "input": {"skill": name}}]}})
    for query in searches:
        events.append({"type": "assistant", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_use", "id": "toolu_INVENTED_S", "name": "ToolSearch", "input": {"query": query, "max_results": 1}}]}})
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": f"toolu_INVENTED_Q{i}", "name": "AskUserQuestion", "input": tool_input} for i, tool_input in enumerate(inputs)]
    events.append({"type": "assistant", "parent_tool_use_id": None, "message": {"content": content}})
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": turns, "total_cost_usd": cost})
    return events


def tool_input(**changes):
    """A deep copy of GOOD_TOOL_INPUT; `changes` replace keys of its one question (header, question, options, multiSelect)."""
    import copy
    data = copy.deepcopy(GOOD_TOOL_INPUT)
    data["questions"][0].update(changes)
    return data


def tool_case_for(prompt_id="explicit-dollar", **kwargs):
    """A Case for a one-turn row on the tool path; `kwargs` go to make_tool_events."""
    return checks.Case.build(ROWS[prompt_id], make_tool_events(**kwargs))
