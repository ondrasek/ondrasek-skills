"""What a stream-json trace says about plugins, hooks, tool results and permission denials (beyond agent_trace.py).

Shapes read, from recorded Claude Code 2.1.319 traces (no recorded trace was made with --include-hook-events):

    {"type": "system", "subtype": "init", "plugins": [{"name": "x", "source": "x@inline"}, ...]}      plugin_errors: not seen, tolerated
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "...", "content": "text"}]}}
    {"type": "result", "permission_denials": [...]}

UNCONFIRMED shapes, used as optional evidence only and tolerated when absent: system events with subtype hook_response (fields
hook_event, exit_code, stderr, output), system events with subtype permission_denied, a list of text blocks as tool_result content.
"""
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
