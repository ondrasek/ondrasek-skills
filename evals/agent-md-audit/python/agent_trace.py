"""Read a Claude Code stream-json trace (one JSON object per line) and answer questions about it.

A trace is what `claude -p --output-format stream-json --verbose` prints. These fields are used, and no others:

    {"type": "system", "subtype": "init", "skills": [...], "tools": [...], "model": "...", "session_id": "..."}
    {"type": "assistant", "parent_tool_use_id": null,
     "message": {"content": [{"type": "text", "text": "..."},
                             {"type": "tool_use", "id": "...", "name": "Skill", "input": {"skill": "oaat"}}]}}
    {"type": "user", "message": {"content": [{"type": "tool_result", ...}]}}
    {"type": "result", "subtype": "success", "is_error": false, "result": "...", "num_turns": 2,
     "total_cost_usd": 0.01, "duration_ms": 1234, "session_id": "..."}

Sources: https://code.claude.com/docs/en/headless (stream-json, system/init), https://code.claude.com/docs/en/agent-sdk/typescript
(SDKAssistantMessage, SDKResultMessage, SDKSystemMessage), https://code.claude.com/docs/en/tools-reference (the tool named Skill).
The Skill tool's input field `skill` is read from Claude Code 2.1.318 session transcripts; the documentation pages above do not list it.
"""
import json


def parse(text):
    """The events of a JSONL text. A line that is not a JSON object is skipped and counted in `bad`."""
    events, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            bad += 1
    return events, bad


def load(path):
    with open(path, encoding="utf-8") as handle:
        return parse(handle.read())[0]


def is_main(event):
    """True for a message of the main conversation (a subagent message carries a parent_tool_use_id)."""
    return event.get("parent_tool_use_id") is None


def blocks(event):
    """The content blocks of an assistant event; a plain string content counts as one text block."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def tool_calls(events):
    """[(name, input)] of every tool_use block of the main conversation, in order."""
    calls = []
    for event in events:
        if event.get("type") == "assistant" and is_main(event):
            for block in blocks(event):
                if block.get("type") == "tool_use":
                    tool_input = block.get("input")
                    calls.append((str(block.get("name", "")), tool_input if isinstance(tool_input, dict) else {}))
    return calls


def skills_invoked(events):
    """The skill names of every Skill tool call, in order ('oaat' for a project skill, 'oaat:oaat' for a plugin skill)."""
    return [str(tool_input.get("skill", "")) for name, tool_input in tool_calls(events) if name == "Skill"]


def skill_matches(invoked, skill):
    """A plugin skill arrives as `plugin:skill`: the name after the last colon is the skill."""
    return invoked == skill or invoked.rsplit(":", 1)[-1] == skill


def init_event(events):
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def init_skills(events):
    """The skills the session listed in system/init, or None when there is no init event."""
    init = init_event(events)
    if init is None:
        return None
    skills = init.get("skills")
    return [str(s) for s in skills] if isinstance(skills, list) else []


def result_event(events):
    found = None
    for event in events:
        if event.get("type") == "result":
            found = event
    return found


def final_reply(events):
    """The reply text: `result` of a success result event, else the text of the last main assistant message."""
    result = result_event(events)
    if result and result.get("subtype") == "success" and isinstance(result.get("result"), str):
        return result["result"]
    for event in reversed(events):
        if event.get("type") == "assistant" and is_main(event):
            text = "".join(b.get("text", "") for b in blocks(event) if b.get("type") == "text")
            if text.strip():
                return text
    return ""


def result_texts(events):
    """The `result` text of every success result event, in order: one per turn of a resumed session."""
    return [e["result"] for e in events if e.get("type") == "result" and e.get("subtype") == "success" and isinstance(e.get("result"), str)]


def earlier_reply(events):
    """The reply of the turn before the last one in a two-turn trace, or '' when the trace holds one turn."""
    texts = result_texts(events)
    return texts[-2] if len(texts) >= 2 else ""


def session_id(events):
    """The session id of the first event that has one, or '' (a resumed turn needs it)."""
    for event in events:
        if isinstance(event.get("session_id"), str) and event["session_id"]:
            return event["session_id"]
    return ""


def stats(events):
    """(num_turns of the last result event, total_cost_usd summed over every result event); None for a value the events lack."""
    result = result_event(events) or {}
    turns = result.get("num_turns")
    costs = [e["total_cost_usd"] for e in events if e.get("type") == "result" and isinstance(e.get("total_cost_usd"), (int, float))]
    return (turns if isinstance(turns, int) else None, sum(costs) if costs else None)
