"""More questions about a stream-json trace than agent_trace.py (shared, evals/_common/python) answers.

Shapes used, and where they are known from:

    system/init   "plugins": [{"name": "sitrep", "source": "sitrep@inline", ...}]       seen in recorded Claude Code 2.1.319 traces
                  "plugin_errors": [...]                                                   NOT seen: read when present
    user event    {"message": {"content": [{"type": "tool_result", "tool_use_id": "...", "content": <string or list of blocks>}]}}
                  the string form is seen for the Skill tool; the shape of a Bash result is not evidenced, both forms are read
    result        "permission_denials": [...]                                              seen, always empty so far: the element shape
                  is undocumented, so elements are kept as raw dicts; checks.denial_kinds reads tool_name and tool_input when they are there
                  and otherwise looks at the element's strings (a report.sh call, or a string that is only a data.js path)
"""
import json

import agent_trace as trace


def plugins_loaded(events):
    """(names, errors): the plugins system/init lists (the part of the id before '@') and its plugin_errors as text."""
    init = trace.init_event(events) or {}
    names = []
    for entry in init.get("plugins") or []:
        raw = (entry.get("name") or str(entry.get("source", ""))) if isinstance(entry, dict) else str(entry)
        if raw:
            names.append(raw.split("@", 1)[0])
    errors = init.get("plugin_errors")
    errors = errors if isinstance(errors, list) else []
    return names, [e if isinstance(e, str) else json.dumps(e) for e in errors]


def plugin_entries(events):
    """[(name, path)] of the plugins system/init lists: `name` as in plugins_loaded, `path` as the init event gives it ('' when absent)."""
    init = trace.init_event(events) or {}
    found = []
    for entry in init.get("plugins") or []:
        if isinstance(entry, dict):
            raw = entry.get("name") or str(entry.get("source", ""))
            found.append((raw.split("@", 1)[0], str(entry.get("path") or "")))
        else:
            found.append((str(entry).split("@", 1)[0], ""))
    return found


def _flatten(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return ""


def tool_results(events, unless_ok=False):
    """{tool_use_id: text} of every tool_result block; a list of blocks is flattened to its text. is_error is not relied on, except with
    `unless_ok`, which leaves out the results that say is_error: false (a real denial has is_error: true; an absent field is still read)."""
    results = {}
    for event in events:
        if event.get("type") == "user":
            for block in trace.blocks(event):
                if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str) and not (unless_ok and block.get("is_error") is False):
                    results[block["tool_use_id"]] = _flatten(block.get("content"))
    return results


def errored_results(events):
    """{tool_use_id} of the tool_result blocks that say is_error: true (a real denial or a failed call; an absent field is not an error)."""
    return {block["tool_use_id"] for event in events if event.get("type") == "user" for block in trace.blocks(event)
            if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str) and block.get("is_error")}


def tool_uses(events):
    """[(tool_use_id, name, input dict)] of every tool_use block of the main conversation, in order."""
    found = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_use":
                    tool_input = block.get("input")
                    found.append((str(block.get("id", "")), str(block.get("name", "")), tool_input if isinstance(tool_input, dict) else {}))
    return found


def bash_commands(events):
    """[(tool_use_id, command)] of every Bash call of the main conversation, in order."""
    found = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    tool_input = block.get("input")
                    found.append((str(block.get("id", "")), str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""))
    return found


def permission_denials(events):
    """The raw elements of the last result event's permission_denials, plus every system permission_denied event."""
    denials = []
    result = trace.result_event(events) or {}
    if isinstance(result.get("permission_denials"), list):
        denials += [d for d in result["permission_denials"] if isinstance(d, dict)]
    return denials + [e for e in events if e.get("type") == "system" and e.get("subtype") == "permission_denied"]
