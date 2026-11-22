"""What a stream-json trace says about plugins, hooks, Bash calls and permission denials.

Built on agent_trace.py (copied unchanged), for the facts a hook plugin's evals need. Shapes read, none of them guaranteed by
the documentation unless noted (the README lists them as unconfirmed):

    {"type": "system", "subtype": "init", "plugins": [{"name": "auto-commit", "source": "auto-commit@inline", "path": "..."}], "plugin_errors": [...]}
        plugins[] (name, path, source, version) is present in recorded traces of Claude Code 2.1.319; plugin_errors[] is assumed.
    {"type": "system", "subtype": "hook_response", "hook_event": "Stop", "exit_code": 2, "stderr": "..."}
        emitted with --include-hook-events; the field names are a guess, so they are only read leniently.
    {"type": "result", "permission_denials": [{"tool_name": "Bash", ...}]}   and system events of subtype permission_denied.
"""
import json

import agent_trace as trace


def plugins_loaded(events):
    """(names, errors) from system/init: plugin names without the `@source` suffix, and the raw plugin_errors entries."""
    errors = (trace.init_event(events) or {}).get("plugin_errors")
    return [p["name"] for p in plugin_entries(events)], errors if isinstance(errors, list) else []


def plugin_entries(events):
    """[{name, source, path, ...}] of system/init `plugins`; a plain string entry becomes {name, source}.

    Recorded traces (Claude Code 2.1.319) carry name, path, source and version; a --plugin-dir plugin has the source `<name>@inline`.
    """
    init = trace.init_event(events) or {}
    entries = []
    for plugin in init.get("plugins") or []:
        entry = plugin if isinstance(plugin, dict) else {"source": str(plugin)}
        name = entry.get("name") or entry.get("source")
        if name:
            entries.append({**entry, "name": str(name).split("@")[0]})
    return entries


def hook_responses(events, hook_event):
    """The system/hook_response events of one hook event (case-insensitive, e.g. 'Stop')."""
    return [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"
            and str(e.get("hook_event", "")).lower() == hook_event.lower()]


def marked_responses(events, hook_event, needle):
    """The system/hook_response events of one hook event whose JSON text carries `needle` (the marker of the report under test)."""
    return [e for e in hook_responses(events, hook_event) if needle in json.dumps(e, ensure_ascii=False)]


def bash_commands(events):
    """[(tool_use id, command)] of every Bash call of the main conversation, in order."""
    commands = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("input"), dict):
                    commands.append((str(block.get("id", "")), str(block["input"].get("command", ""))))
    return commands


def fed_to_model(events, needle):
    """True when `needle` occurs in a block of a user event that is not a tool result: the hook's stderr reached the conversation.

    Only text that no model wrote counts. The assistant's messages and the result event's text are model prose (an agent that quotes
    or reads the report would otherwise make itself the evidence), and a tool_result is the output of a command the agent chose to run.
    How a Stop block is encoded in a real trace is unconfirmed; a report encoded any other way is still found through the
    `hook_response` event or, failing that, through the commits it caused (see checks.hook_report_delivered).
    """
    return any(needle in json.dumps(block, ensure_ascii=False) for e in events if e.get("type") == "user"
               for block in trace.blocks(e) if block.get("type") != "tool_result")


def permission_denials(events):
    """The raw denial entries: the last result event's permission_denials plus system permission_denied events."""
    result = trace.result_event(events) or {}
    denied = result.get("permission_denials")
    return (denied if isinstance(denied, list) else []) + [e for e in events if e.get("type") == "system" and e.get("subtype") == "permission_denied"]


def continuation(events, needle):
    """'fed' when the report reached the model, 'hook-event-only' when a Stop hook_response carries it, else 'none'.

    The marker is required: a Stop hook that exits 2 without it is some other hook (a user-level copy, the milestones reminder), and
    is not evidence that the plugin under test spoke.
    """
    if fed_to_model(events, needle):
        return "fed"
    if marked_responses(events, "Stop", needle):
        return "hook-event-only"
    return "none"
