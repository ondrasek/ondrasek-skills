"""Helpers for the tool path of the oaat skill: items presented with the `AskUserQuestion` tool instead of the text template.

These read a trace in the shared reader's terms (`agent_trace`) and add what only this harness needs: the events of the LAST turn
of a trace, a timeline of that turn (text, tool calls, tool results in order), the AskUserQuestion calls with their results, the text
between the calls, and the ToolSearch calls. They are kept here and not in evals/_common because no other harness needs them.

The model of an interactive walk-through (code.claude.com/docs/en/agent-sdk/user-input: the tool pauses the run and the answer comes
back as its tool_result IN THE SAME TURN): ONE turn holds several sequential calls,

    assistant: text (count line) + tool_use AskUserQuestion      <- item 1
    user:      tool_result (the chosen label, `Skip for now`, or the free text typed into "Other")
    assistant: text (one plain line) + tool_use AskUserQuestion  <- item 2
    user:      tool_result ...
    assistant: text (the wrap-up)

so the checks grade PER CALL, not per turn, and a skip is a tool_result and never a new prompt.

What is documented and what is not. The INPUT of the tool IS documented at the URL above: `questions[]`, each with `question`,
`header` (max 12 characters), `options` (2 to 4, each `label` and `description`) and `multiSelect`. The stream-json ENVELOPE of the
result is UNCONFIRMED: no real headless run can call the tool (measured: it is not available in `claude -p`), so nothing here was
read from a real interactive trace. The standard stream-json shape is assumed:

    {"type": "assistant", "parent_tool_use_id": null, "message": {"content": [
        {"type": "text", "text": "5 items need you. First one:"},
        {"type": "tool_use", "id": "toolu_1", "name": "AskUserQuestion", "input": {"questions": [
            {"question": "...", "header": "...", "multiSelect": false, "options": [{"label": "...", "description": "..."}]}]}}]}}
    {"type": "user", "parent_tool_use_id": null, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Postgres (Recommended)"}]}}     (is_error: true when denied or failed)
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


def content_text(content):
    """The text of a tool_result `content`: a string, or a list of {"type": "text", "text"} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def timeline(events):
    """The main conversation in `events` as an ordered list of entries:

    {"kind": "text", "text", "message"}                      a text block of assistant message number `message`
    {"kind": "call", "name", "id", "input", "message"}       a tool_use block
    {"kind": "result", "id", "error", "content"}             a tool_result block of a user event (`error` is is_error is True)
    """
    entries, message = [], 0
    for event in events:
        if not trace.is_main(event):
            continue
        kind = event.get("type")
        if kind == "assistant":
            message += 1
            for block in trace.blocks(event):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    entries.append({"kind": "text", "text": block["text"], "message": message})
                elif block.get("type") == "tool_use":
                    tool_input = block.get("input")
                    entries.append({"kind": "call", "name": str(block.get("name", "")), "id": block.get("id"),
                                    "input": tool_input if isinstance(tool_input, dict) else {}, "message": message})
        elif kind == "user":
            content = (event.get("message") or {}).get("content") if isinstance(event.get("message"), dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    entries.append({"kind": "result", "id": block.get("tool_use_id"), "error": block.get("is_error") is True,
                                    "content": content_text(block.get("content"))})
    return entries


def ask_calls(events):
    """The inputs of every AskUserQuestion tool call of the main conversation in `events`, in order."""
    return [tool_input for name, tool_input in trace.tool_calls(events) if name == TOOL]


def ask_records(events):
    """One record per AskUserQuestion call of the main conversation in `events`, in order:

    {"id", "input", "message", "position" (index in the timeline), "result" (None, or {"error", "content"}), "answered", "label"}

    `answered` is true when the call got a tool_result that is not an error (the user saw the question and answered it); a call
    with an error result or with no result at all counts as denied or failed. `label` is the option label that the result
    equals (after strip), or None for free text ("Other") and for a call that was not answered.
    """
    entries, records = timeline(events), []
    for position, entry in enumerate(entries):
        if entry["kind"] != "call" or entry["name"] != TOOL:
            continue
        result = next((e for e in entries[position + 1:] if e["kind"] == "result" and entry["id"] is not None and e["id"] == entry["id"]), None)
        record = {"id": entry["id"], "input": entry["input"], "message": entry["message"], "position": position,
                  "result": {"error": result["error"], "content": result["content"]} if result else None}
        record["answered"] = result is not None and not result["error"]
        record["label"] = None
        if record["answered"]:
            labels = option_labels(first_question_of(entry["input"]))
            # an exact match: free text that merely mentions an option ("Is SQLite enough?") is free text, not a choice
            record["label"] = next((label for label in labels if label.strip() and label.strip() == result["content"].strip()), None)
        records.append(record)
    return records


def unanswered_overlaps(events):
    """Ids of the AskUserQuestion calls that were made while an earlier AskUserQuestion call had no tool_result yet.

    This is the sequence rule of the skill (one call, the answer, then the next call; never batch): two calls in one assistant
    message, or two calls without a tool_result between them, are both listed here.
    """
    open_id, found = None, []
    for entry in timeline(events):
        if entry["kind"] == "call" and entry["name"] == TOOL:
            if open_id is not None:
                found.append(entry["id"])
            open_id = entry["id"] if entry["id"] is not None else "?"
        elif entry["kind"] == "result" and open_id is not None and entry["id"] == open_id:
            open_id = None
    return found


def segments(events):
    """The text outside the AskUserQuestion calls, as one string per gap: [before call 1, between call 1 and 2, ..., after the last call].

    A turn with no call has one segment. Text blocks of one gap are joined by a newline, so two blocks are two lines.
    """
    parts = [[]]
    for entry in timeline(events):
        if entry["kind"] == "call" and entry["name"] == TOOL:
            parts.append([])
        elif entry["kind"] == "text" and entry["text"].strip():
            parts[-1].append(entry["text"].strip())
    return ["\n".join(p) for p in parts]


def questions(call_input):
    """The question objects of one call's input ([] when `questions` is missing or not a list of objects)."""
    found = call_input.get("questions") if isinstance(call_input, dict) else None
    return [q for q in found if isinstance(q, dict)] if isinstance(found, list) else []


def first_question_of(call_input):
    found = questions(call_input)
    return found[0] if found else {}


def first_question(events):
    """The first question of the first AskUserQuestion call in `events`, or {}."""
    for call in ask_calls(events):
        return first_question_of(call)
    return {}


def question_text(question):
    text = question.get("question") if isinstance(question, dict) else None
    return text if isinstance(text, str) else ""


def question_texts(events):
    """The text of every question of every AskUserQuestion call in `events` (the item a past turn presented)."""
    return [question_text(q) for call in ask_calls(events) for q in questions(call) if question_text(q)]


def option_labels(question):
    options = question.get("options") if isinstance(question, dict) else None
    return [o["label"] for o in options if isinstance(o, dict) and isinstance(o.get("label"), str)] if isinstance(options, list) else []


def option_descriptions(question):
    options = question.get("options") if isinstance(question, dict) else None
    return [o["description"] for o in options if isinstance(o, dict) and isinstance(o.get("description"), str)] if isinstance(options, list) else []


def text_outside(events):
    """The text the agent printed in `events` (main conversation), that is: outside any tool call. Blocks are joined by a newline."""
    return "\n".join(e["text"] for e in timeline(events) if e["kind"] == "text" and e["text"].strip()).strip()


def searched_for_tool(events):
    """[input of every ToolSearch call whose input mentions the AskUserQuestion tool], over the whole trace."""
    return [tool_input for name, tool_input in trace.tool_calls(events)
            if name == SEARCH_TOOL and TOOL_NAME_RE.search(json.dumps(tool_input, ensure_ascii=False))]
