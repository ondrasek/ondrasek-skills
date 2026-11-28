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

What is documented, what was observed and what is not. The INPUT of the tool IS documented at the URL above: `questions[]`, each with
`question`, `header` (max 12 characters), `options` (2 to 4, each `label` and `description`) and `multiSelect`. The ENVELOPE of the
result is UNCONFIRMED across versions: no real headless run can call the tool (measured: it is not available in `claude -p`). ONE
sample was observed in an interactive Claude Code session (2026-11-22): the text of the tool_result is a wrapper sentence around the
answers, one `"<question text>"="<answer>"` pair per question, joined by `, `:

    Your questions have been answered: "<question 1>"="<label 1>", "<question 2>"="<label 2>". You can now continue with these answers in mind.

The label is verbatim (a `(Recommended)` suffix included). An answer TYPED into the free-text field comes back in a second wrapper
(observed 2026-11-28, Claude Code 2.1.332):

    The user answered: "<question>"="<typed text>". Read the answers carefully — they may request clarification, ...

Few samples, two versions: `answer_of` therefore also accepts a bare label (the earlier assumption) and a JSON mapping of question to answer (the shape
of the SDK's `answers`). The standard stream-json shape around it is assumed:

    {"type": "assistant", "parent_tool_use_id": null, "message": {"content": [
        {"type": "text", "text": "5 items need you. First one:"},
        {"type": "tool_use", "id": "toolu_1", "name": "AskUserQuestion", "input": {"questions": [
            {"question": "...", "header": "...", "multiSelect": false, "options": [{"label": "...", "description": "..."}]}]}}]}}
    {"type": "user", "parent_tool_use_id": null, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Your questions have been answered: \"...\"=\"Postgres (Recommended)\". You can now continue with these answers in mind."}]}}
                                                                                                          (is_error: true when denied or failed)
"""
import json
import os
import re
import shlex

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


# Two observed wrappers (interactive sessions): a picked option comes back in the first, an answer TYPED into the free-text field in the
# second (observed 2026-11-28, Claude Code 2.1.332). The tail of the second is matched loosely: only its first words are relied on.
ENVELOPE_RE = re.compile(r"\A\s*Your questions have been answered:\s*(?P<body>.*?)\s*You can now continue with these answers in mind\.?\s*\Z", re.DOTALL)
TYPED_RE = re.compile(r"\A\s*The user answered:\s*(?P<body>.*?\"\.)\s*Read the answers carefully\b.*\Z", re.DOTALL)
PAIR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"="((?:[^"\\]|\\.)*)"')


def wrapper_pairs(body, known):
    """{question text: answer} from the body of the observed wrapper (`"q1"="a1", "q2"="a2"`, a final period already cut).

    The known question texts of the call are looked up first, so an answer or a question that holds a quote or a comma is read whole:
    an answer runs up to the next known question or to the last quote. When none of them is found the generic pair pattern is tried.
    """
    marks = sorted((body.find(f'"{q}"="'), q) for q in known if f'"{q}"="' in body)
    pairs = {}
    for number, (start, question) in enumerate(marks):
        begin = start + len(question) + 4
        end = marks[number + 1][0] if number + 1 < len(marks) else len(body)
        chunk = body[begin:end].rstrip()
        if number + 1 < len(marks):
            chunk = chunk.rstrip(",").rstrip()
        pairs[question] = chunk[:-1] if chunk.endswith('"') else chunk
    return pairs or {q: a for q, a in PAIR_RE.findall(body)}


def answer_of(content, question_texts):
    """(answer for the first question, envelope) from the text of a tool_result: envelope is "wrapper" (the observed sentence),
    "mapping" (a JSON object question -> answer, or {"answers": {...}}) or "bare" (the content is the answer itself).

    An unparsable wrapper falls back to the whole content, which then reads as free text.
    """
    text = content.strip()
    found = ENVELOPE_RE.match(content) or TYPED_RE.match(content)
    if found:
        body = found.group("body").strip()
        if body.endswith("."):
            body = body[:-1]
        pairs = wrapper_pairs(body, [q for q in question_texts if q])
        if pairs:
            first = question_texts[0] if question_texts and question_texts[0] in pairs else next(iter(pairs))
            return pairs[first], "wrapper"
        return text, "wrapper"
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            mapping = data.get("answers") if isinstance(data.get("answers"), dict) else data
            values = {k: v for k, v in mapping.items() if isinstance(k, str) and isinstance(v, str)}
            if values:
                first = question_texts[0] if question_texts and question_texts[0] in values else next(iter(values))
                return values[first], "mapping"
    return text, "bare"


def match_label(answer, labels):
    """The option label that `answer` equals (ignoring case and surrounding whitespace), else None (free text)."""
    wanted = answer.strip().casefold()
    return next((label for label in labels if label.strip() and label.strip().casefold() == wanted), None)


def ask_records(events):
    """One record per AskUserQuestion call of the main conversation in `events`, in order:

    {"id", "input", "message", "position" (index in the timeline), "result" (None, or {"error", "content"}), "answered", "label",
     "answer", "envelope"}

    `answered` is true when the call got a tool_result that is not an error (the user saw the question and answered it); a call
    with an error result or with no result at all counts as denied or failed. `answer` is the answer extracted from the result
    (see `answer_of`) and `envelope` how it was wrapped; `label` is the option label that the answer equals (ignoring case and
    surrounding whitespace; the canonical spelling of the call's option), or None for free text ("Other") and for a call that was
    not answered.
    """
    entries, records = timeline(events), []
    for position, entry in enumerate(entries):
        if entry["kind"] != "call" or entry["name"] != TOOL:
            continue
        result = next((e for e in entries[position + 1:] if e["kind"] == "result" and entry["id"] is not None and e["id"] == entry["id"]), None)
        record = {"id": entry["id"], "input": entry["input"], "message": entry["message"], "position": position,
                  "result": {"error": result["error"], "content": result["content"]} if result else None}
        record["answered"] = result is not None and not result["error"]
        record["label"], record["answer"], record["envelope"] = None, None, None
        if record["answered"]:
            texts = [question_text(q) for q in questions(entry["input"])]
            record["answer"], record["envelope"] = answer_of(result["content"], texts)
            # an exact match: free text that merely mentions an option ("Is SQLite enough?") is free text, not a choice
            record["label"] = match_label(record["answer"], option_labels(first_question_of(entry["input"])))
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


MILESTONES_FILE = ".cache/milestones.json"


def is_bookkeeping(name, tool_input):
    """True for a call that records or reads a milestone answer through the milestones plugin (the skill requires it in the same turn):
    a Skill call whose name ends in `milestones`, a Bash call that runs a script named `milestones.py` (a word of the command, by its
    base name), a Read of `.cache/milestones.json`. Such a call is exempt from `tool-detours` and `turns-bound`; any other call is not."""
    data = tool_input if isinstance(tool_input, dict) else {}
    if name == "Skill":
        return str(data.get("skill", "")).strip().endswith("milestones")
    if name == "Bash":
        command = data.get("command")
        try:
            words = shlex.split(command) if isinstance(command, str) else []
        except ValueError:
            words = str(command).split()
        return any(os.path.basename(word.replace("\\", "/")) == "milestones.py" for word in words)
    if name == "Read":
        return str(data.get("file_path", "")).replace("\\", "/").endswith(MILESTONES_FILE)
    return False


def bookkeeping_calls(events):
    """[(name, input)] of the milestone bookkeeping calls of the main conversation in `events`."""
    return [(name, tool_input) for name, tool_input in trace.tool_calls(events) if is_bookkeeping(name, tool_input)]
