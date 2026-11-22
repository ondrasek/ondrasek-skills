"""What a stream-json trace says about plugins, hooks, Bash calls and permission denials.

Built on the shared agent_trace.py (evals/_common/python), for the facts a hook plugin's evals need. Shapes read, none of them guaranteed by
the documentation unless noted (the README lists them as unconfirmed):

    {"type": "system", "subtype": "init", "plugins": [{"name": "auto-commit", "source": "auto-commit@inline", "path": "..."}], "plugin_errors": [...]}
        plugins[] (name, path, source, version) is present in recorded traces of Claude Code 2.1.319; plugin_errors[] is assumed.
    {"type": "system", "subtype": "hook_started", "hook_name": "Stop", "hook_event": "Stop", ...}
    {"type": "system", "subtype": "hook_response", "hook_name": "Stop", "hook_event": "Stop", "output": ..., "stdout": ..., "stderr": ...,
     "exit_code": 2, "outcome": "error"}
        emitted with --include-hook-events (CONFIRMED by real traces of Claude Code 2.1.330). The plugin's report is exit_code 2, outcome
        'error' (not an infrastructure error); exit_code 0 is a silent stop; any other exit_code means the hook did not run (hook_errors).
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


def hook_errors(events, hook_event):
    """The hook_response events of one hook event that are an infrastructure error: an integer exit_code that is neither 0 (success)
    nor 2 (the hook's own blocking report, which shows as outcome 'error' too and is NOT an infrastructure error).

    Recorded: exit_code 1 with output "Failed to run: EPERM: operation not permitted, mkdir '.../.claude/plugins/data/auto-commit-inline'"
    when Claude Code itself ran inside a sandbox: the hook never ran.
    """
    return [e for e in hook_responses(events, hook_event) if isinstance(e.get("exit_code"), int) and not isinstance(e.get("exit_code"), bool)
            and e["exit_code"] not in (0, 2)]


def error_text(event, limit=200):
    """The text of a hook_response event (output, else stderr, else stdout), whitespace collapsed and cut to `limit` characters."""
    text = next((str(event[key]) for key in ("output", "stderr", "stdout") if event.get(key)), "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


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


def bash_calls(events):
    """[(tool_use id, command, succeeded)] of every Bash call of the main conversation, in order.

    `succeeded` is True only when a tool_result block with the call's id exists and does not carry is_error: a call without a result
    in the trace (cut off, or a trace that omits results) is not known to have worked, and a denied call's result is an error.
    """
    failed = {}
    for event in events:
        if event.get("type") == "user":
            for block in trace.blocks(event):
                if block.get("type") == "tool_result" and block.get("tool_use_id") is not None:
                    failed[str(block["tool_use_id"])] = bool(block.get("is_error"))
    return [(call_id, command, call_id in failed and not failed[call_id]) for call_id, command in bash_commands(events)]


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
