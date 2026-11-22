"""What a stream-json trace says about plugins, hooks, tool results and permission denials (beyond agent_trace.py).

Shapes read, from recorded Claude Code 2.1.319 and 2.1.330 traces:

    {"type": "system", "subtype": "init", "plugins": [{"name": "x", "source": "x@inline"}, ...]}      plugin_errors: not seen, tolerated
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "...", "content": "text"}]}}
    {"type": "result", "permission_denials": [...]}

CONFIRMED (2.1.330, run with --include-hook-events, a plugin loaded with --plugin-dir): one pair of system events per Bash call,

    {"type": "system", "subtype": "hook_started", "hook_id": ..., "hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse"}
    {"type": "system", "subtype": "hook_response", "hook_id": ..., "hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse",
     "stdout": "", "stderr": "...", "output": "...", "exit_code": 0, "outcome": "success"}

An allowing hook exits 0 (outcome success); the blocking one exits 2 (outcome "error", stderr = the hook's message); a hook that could
not run exits 1 with outcome "error" and the error text in output and stderr. There is no tool_use_id in these events, and hook_name
does not name the plugin: the plugin is attributed by its message or, for an infrastructure error, by the hook's path in the text.

UNCONFIRMED shapes, used as optional evidence only and tolerated when absent: system events with subtype permission_denied, a list
of text blocks as tool_result content.
"""
import json

import agent_trace as trace


def _plugin_entries(events):
    """[(name, source)] of system/init `plugins`: a recorded entry is {"name": "x", "path": ..., "source": "x@inline"}; a bare
    string is taken as the source. The name is the part before '@' when the entry has no name field."""
    init = trace.init_event(events) or {}
    entries = []
    for plugin in init.get("plugins") if isinstance(init.get("plugins"), list) else []:
        source = str(plugin.get("source") or plugin.get("name") or "") if isinstance(plugin, dict) else str(plugin)
        name = str(plugin.get("name") or source) if isinstance(plugin, dict) else source
        entries.append((name.split("@")[0], source))
    return entries


def plugins_loaded(events):
    """(names, errors) from system/init: the plugin names (the part before '@' of an id like name@inline) and plugin_errors."""
    init = trace.init_event(events) or {}
    errors = init.get("plugin_errors")
    return [name for name, _ in _plugin_entries(events)], errors if isinstance(errors, list) else []


def inline_plugins(events):
    """The names of the plugins loaded from --plugin-dir: recorded entries of those have a source ending in '@inline' (a copy
    installed from a marketplace has another suffix and would not be the code under test)."""
    return [name for name, source in _plugin_entries(events) if source.endswith("@inline")]


def hook_responses(events, hook_event):
    """The system events of subtype hook_response for `hook_event` (compared case-insensitively)."""
    return [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"
            and str(e.get("hook_event", "")).lower() == hook_event.lower()]


def _applies_to_bash(response, plugin):
    """True when a PreToolUse hook_response can concern a Bash call: `hook_name` is "PreToolUse:<matcher>" in the recorded traces, so
    a matcher that does not name Bash (PreToolUse:Edit) is another tool's hook; a response without a matcher, or one that names the
    plugin anywhere, is kept. The plugin cannot be attributed by `hook_name` (it is not in it), hence the wide net."""
    name = str(response.get("hook_name", ""))
    matcher = name.split(":", 1)[1] if ":" in name else ""
    return not matcher or "bash" in matcher.lower() or "*" in matcher or plugin in json.dumps(response)


def hook_infrastructure_errors(events, plugin):
    """The hook_response events that say a hook did not run to a decision: an integer `exit_code` that is neither 0 (allowed) nor 2
    (the blocking exit of a PreToolUse hook, which is the plugin doing its job). Recorded case: exit_code 1, outcome "error", the
    text "Failed to run: EPERM: operation not permitted, mkdir '.../plugins/data/<plugin>-inline'" (the Claude Code Bash sandbox
    forbids that directory). Counted: PreToolUse hooks that can concern Bash (see _applies_to_bash) and any hook naming `plugin`;
    an unrelated hook of another event is not this plugin's problem. A missing or non-integer exit_code is not counted (shape unknown)."""
    return [r for r in events if r.get("type") == "system" and r.get("subtype") == "hook_response"
            and isinstance(r.get("exit_code"), int) and not isinstance(r.get("exit_code"), bool) and r["exit_code"] not in (0, 2)
            and ((str(r.get("hook_event", "")).lower() == "pretooluse" and _applies_to_bash(r, plugin)) or plugin in json.dumps(r))]


def error_text(response, limit=140):
    """The first non-empty of stderr, output, stdout of a hook_response, whitespace collapsed and cut to `limit` characters."""
    for key in ("stderr", "output", "stdout"):
        text = " ".join(str(response.get(key) or "").split())
        if text:
            return text if len(text) <= limit else text[:limit - 3] + "..."
    return ""


def _flat(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return ""


def tool_results(events):
    """{tool_use_id: text} of every tool_result block; is_error is not relied on."""
    out = {}
    for event in events:
        if event.get("type") == "user":
            for block in trace.blocks(event):
                if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    out[block["tool_use_id"]] = _flat(block.get("content"))
    return out


def bash_commands(events):
    """[(tool_use_id, command)] of the Bash calls of the main conversation, in order."""
    commands = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("input"), dict):
                    commands.append((str(block.get("id", "")), str(block["input"].get("command", ""))))
    return commands


def permission_denials(events):
    """The raw dicts of the last result event's permission_denials plus any system permission_denied events."""
    result = trace.result_event(events) or {}
    denials = result.get("permission_denials")
    found = [d for d in denials if isinstance(d, dict)] if isinstance(denials, list) else []
    return found + [e for e in events if e.get("type") == "system" and e.get("subtype") == "permission_denied"]
