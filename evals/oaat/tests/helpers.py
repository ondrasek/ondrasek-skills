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


# ---- the tool path (AskUserQuestion): INVENTED traces. The text of a tool_result follows the envelope OBSERVED in one interactive session
# (2026-11-22, one sample, one version: UNCONFIRMED across versions, README "The tool path"); a bare label and a JSON mapping are the others ----
COUNT_LINE = "5 items need you. First one:"
CLOSING_LINE = "Noted: Postgres for the first release."
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


def wrap_answer(tool_input, answer, envelope="wrapper"):
    """The content of a tool_result for `answer` (a label, `Skip for now` or free text) to the questions of `tool_input`.

    wrapper (the observed envelope): `Your questions have been answered: "<question>"="<answer>", ... You can now continue with these
    answers in mind.` with one pair per question; mapping: a JSON object {"answers": {question: answer}}; bare: the answer itself.
    """
    if envelope == "bare":
        return answer
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    texts = [q.get("question", "") for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []
    texts = texts or [""]
    if envelope == "mapping":
        return json.dumps({"answers": {text: answer for text in texts}})
    pairs = ", ".join(f'"{text}"="{answer}"' for text in texts)
    return f"Your questions have been answered: {pairs}. You can now continue with these answers in mind."


class Ask:
    """One AskUserQuestion call in a walk: its input, and what comes back as its tool_result.

    answer: what the user answered (the chosen label, `Skip for now`, or free text); None = no tool_result at all. The content of the
    tool_result is that answer in the observed wrapper envelope (`envelope="bare"` or "mapping" for the other readings).
    error: the tool_result carries is_error true (a denied or failed call); its content is `answer` as given, unwrapped.
    """

    def __init__(self, tool_input=None, answer="", error=False, tool_id=None, envelope="wrapper"):
        self.input = GOOD_TOOL_INPUT if tool_input is None else tool_input
        self.answer, self.error, self.tool_id, self.envelope = answer, error, tool_id, envelope


class Use:
    """One call of another tool in a walk (a Skill, Bash or Read call, for example), answered by a tool_result `result`."""

    def __init__(self, name, tool_input=None, result="ok"):
        self.name, self.input, self.result = name, {} if tool_input is None else tool_input, result


def default_answer(tool_input):
    """The label of the first option of the first question: the recommended choice."""
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    options = questions[0].get("options") if isinstance(questions, list) and questions and isinstance(questions[0], dict) else None
    return options[0].get("label", "answered") if isinstance(options, list) and options and isinstance(options[0], dict) else "answered"


def init_event(tools=("Skill", "AskUserQuestion")):
    return {"type": "system", "subtype": "init", "session_id": "INVENTED-unit-test", "skills": ["oaat:oaat"], "tools": list(tools)}


def assistant(*blocks):
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"content": list(blocks)}}


def tool_use(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def user_results(*results):
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": list(results)}}


def make_walk_events(messages, called=("oaat:oaat",), searches=(), others=(), turns=None, cost=0.01, tools=("Skill", "AskUserQuestion")):
    """The events of ONE INVENTED turn of an interactive walk-through (the same-turn model, README "The tool path").

    `messages` is a list of assistant messages; a message is a list of blocks: a str is a text block, an Ask is an AskUserQuestion
    tool_use, a Use is a call of another tool. After a message, every Ask of it that has an answer is followed by a user event with its
    tool_result (is_error true when `error`); an Ask with answer None gets no result. The result event repeats the text of the last
    message (as the real stream does). `others` are calls made before the messages: a tool name, or a (name, input) pair.
    `turns` defaults to the number of assistant messages (the Skill, ToolSearch and other calls included).
    """
    events, count = [init_event(tools)], 0
    for name in called:
        events += [assistant(tool_use("toolu_INVENTED", "Skill", {"skill": name})),
                   user_results({"type": "tool_result", "tool_use_id": "toolu_INVENTED", "content": "INVENTED tool result"})]
        count += 1
    for query in searches:
        events.append(assistant(tool_use("toolu_INVENTED_S", "ToolSearch", {"query": query, "max_results": 1})))
        count += 1
    for item in others:
        name, other_input = item if isinstance(item, tuple) else (item, {})
        events.append(assistant(tool_use("toolu_INVENTED_O", name, other_input)))
        count += 1
    serial, last_text = 0, ""
    for blocks in messages:
        content, results, last_text = [], [], ""
        for block in blocks:
            if isinstance(block, Ask):
                tool_id = block.tool_id or f"toolu_INVENTED_Q{serial}"
                serial += 1
                content.append(tool_use(tool_id, "AskUserQuestion", block.input))
                if block.answer is not None:
                    content_text = block.answer if block.error else wrap_answer(block.input, block.answer, block.envelope)
                    result = {"type": "tool_result", "tool_use_id": tool_id, "content": content_text}
                    if block.error:
                        result["is_error"] = True
                    results.append(result)
            elif isinstance(block, Use):
                serial += 1
                tool_id = f"toolu_INVENTED_U{serial}"
                content.append(tool_use(tool_id, block.name, block.input))
                results.append({"type": "tool_result", "tool_use_id": tool_id, "content": block.result})
            elif block:
                content.append({"type": "text", "text": block})
                last_text = block
        events.append(assistant(*content))
        if results:
            events.append(user_results(*results))
        count += 1
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": last_text, "num_turns": count if turns is None else turns,
                   "total_cost_usd": cost})
    return events


def make_tool_events(inputs=(GOOD_TOOL_INPUT,), text=COUNT_LINE, closing=CLOSING_LINE, between="", answers=None, errors=False, batched=False, **kwargs):
    """A good one-turn walk of `inputs`: the count line (`text`) and the first call, then for every later call `between` (one plain
    line) and the call, then a closing plain line. Every call but the last is answered with the label of its first option and the last
    with the free text `stop` (the walk ends there: a label with items left is an early end, `tool-early-end`), unless `answers` says
    otherwise (one entry per call; None = no tool_result). `batched=True` puts every call into ONE assistant message (what the skill
    forbids). `errors=True` makes every tool_result a denial. Other keywords go to make_walk_events."""
    answers = list(answers) if answers is not None else [default_answer(item) for item in inputs[:-1]] + ["stop"] * bool(inputs)
    asks = [Ask(item, answer, errors) for item, answer in zip(inputs, answers)]
    if batched:
        messages = [[text, *asks, closing]]
    else:
        messages = [[text, asks[0]]] if asks else [[text]]
        messages += [[between, ask] for ask in asks[1:]]
        messages.append([closing])
    return make_walk_events(messages, **kwargs)


def tool_input(**changes):
    """A deep copy of GOOD_TOOL_INPUT; `changes` replace keys of its one question (header, question, options, multiSelect)."""
    import copy
    data = copy.deepcopy(GOOD_TOOL_INPUT)
    data["questions"][0].update(changes)
    return data


def item_input(n, title, kind="question", **changes):
    """The input of a call for item N of 5 (kind: decision, question, risk, ...), in the skill's shape, titled `title`."""
    question = GOOD_TOOL_INPUT["questions"][0]["question"].replace("Item 1 of 5, decision: Pick the database", f"Item {n} of 5, {kind}: {title}")
    return tool_input(**{"question": question, "header": kind.capitalize(), **changes})


def tool_case_for(prompt_id="explicit-dollar", **kwargs):
    """A Case for a one-turn row on the tool path; `kwargs` go to make_tool_events."""
    return checks.Case.build(ROWS[prompt_id], make_tool_events(**kwargs))
