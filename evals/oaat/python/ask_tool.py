"""Helpers for the tool path of the oaat skill: an item presented with the `AskUserQuestion` tool instead of the text template.

These read a trace in the shared reader's terms (`agent_trace`) and add what only this harness needs: the events of the LAST turn
of a trace, the AskUserQuestion calls in it, the text of the question, the text printed outside the call, and the ToolSearch calls.
They are kept here and not in evals/_common because no other harness needs them.

The assumed event shape is UNCONFIRMED. No real headless run can call the tool (measured: it is not available in `claude -p`), so
nothing here was read from a real interactive trace. The standard stream-json shape is assumed:

    {"type": "assistant", "parent_tool_use_id": null, "message": {"content": [
        {"type": "text", "text": "5 items need you. First one:"},
        {"type": "tool_use", "id": "...", "name": "AskUserQuestion", "input": {"questions": [
            {"question": "...", "header": "...", "multiSelect": false, "options": [{"label": "...", "description": "..."}]}]}}]}}
"""
import json
import re

import agent_trace as trace

TOOL = "AskUserQuestion"
SEARCH_TOOL = "ToolSearch"
# 'AskUserQuestion', 'ask user question', 'ask_user_question': any spelling of the tool's name inside a search input
TOOL_NAME_RE = re.compile(r"ask[\s_-]*user[\s_-]*question", re.IGNORECASE)


def split(events):
    """(earlier events, events of the last turn). A turn ends with a result event; a two-turn trace holds two of them.

    When the trace does not end with a result event (a run that was cut off), the last turn is what follows the last result event.
    """
    ends = [i for i, event in enumerate(events) if event.get("type") == "result"]
    if events and events[-1].get("type") == "result":
        ends = ends[:-1]
    start = ends[-1] + 1 if ends else 0
    return events[:start], events[start:]


def last_turn(events):
    return split(events)[1]


def ask_calls(events):
    """The inputs of every AskUserQuestion tool call of the main conversation in `events`, in order."""
    return [tool_input for name, tool_input in trace.tool_calls(events) if name == TOOL]


def questions(call_input):
    """The question objects of one call's input ([] when `questions` is missing or not a list of objects)."""
    found = call_input.get("questions") if isinstance(call_input, dict) else None
    return [q for q in found if isinstance(q, dict)] if isinstance(found, list) else []


def first_question(events):
    """The first question of the first AskUserQuestion call in `events`, or {}."""
    for call in ask_calls(events):
        found = questions(call)
        return found[0] if found else {}
    return {}


def question_text(question):
    text = question.get("question") if isinstance(question, dict) else None
    return text if isinstance(text, str) else ""


def question_texts(events):
    """The text of every question of every AskUserQuestion call in `events` (the item a past turn presented)."""
    return [question_text(q) for call in ask_calls(events) for q in questions(call) if question_text(q)]


def text_outside(events):
    """The text the agent printed in `events` (main conversation), that is: outside any tool call. Blocks are joined by a newline."""
    parts = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            parts += [b.get("text", "") for b in trace.blocks(event) if b.get("type") == "text" and isinstance(b.get("text"), str)]
    return "\n".join(p for p in parts if p.strip()).strip()


def searched_for_tool(events):
    """[input of every ToolSearch call whose input mentions the AskUserQuestion tool], over the whole trace."""
    return [tool_input for name, tool_input in trace.tool_calls(events)
            if name == SEARCH_TOOL and TOOL_NAME_RE.search(json.dumps(tool_input, ensure_ascii=False))]
