"""What agent_trace.py does not read: the plugin list, hook lifecycle events, Bash commands and their tool results, permission denials.

Built for this harness on top of agent_trace's events (a list of stream-json objects). Shapes taken from a real recorded trace:
system/init carries `plugins` as [{"name": "oaat", "source": "oaat@inline", "path": ..., "version": ...}]; the result event carries
`permission_denials` (a list, empty in the recorded runs); a Bash tool_use has input {"command": ...}; a tool_result block is {"tool_use_id", "content": str} in a user event (`is_error` is
assumed to be set on a failed call: unconfirmed).

CONFIRMED in the first real run (Claude Code 2.1.330, 2026-11-22, --include-hook-events): a hook emits
{"type": "system", "subtype": "hook_started", "hook_name": "Stop", "hook_event": "Stop", ...} and then {"type": "system", "subtype":
"hook_response", "hook_name", "hook_event", "output", "stdout", "stderr", "exit_code": N, "outcome": "success" | "error"}. A Stop hook
that fires and asks for work exits 2 with outcome 'error': that is the hook doing its job, not an infrastructure error. An
INFRASTRUCTURE error has an exit code outside (0, 2), e.g. exit_code 1, outcome 'error', output "Failed to run: EPERM: operation not
permitted, mkdir '.../.claude/plugins/data/milestones-inline'" (seen when Claude Code itself ran inside a sandbox); the hook never ran,
so it cannot be blamed (hook_errors).

STILL UNCONFIRMED: the shape of an element of `permission_denials` and of a system `permission_denied` event, the `is_error` flag of a
failed Bash tool_result. The code matches on text or tolerates a missing field there, and the checks that depend on them grade as
INCONCLUSIVE, not FAIL.
"""
import json

import agent_trace as trace


def plugin_entries(events):
    """[{name, source, path}] of the `plugins` list of system/init (a bare string entry gives a name and a source only). The name is
    compared without its `@source` suffix. A copy loaded with --plugin-dir has source '<name>@inline' (recorded); an installed one
    carries its marketplace there."""
    init = trace.init_event(events)
    found = []
    for plugin in (init.get("plugins") if init else None) or []:
        if isinstance(plugin, dict):
            source = str(plugin.get("source") or "")
            found.append({"name": str(plugin.get("name") or source).split("@")[0], "source": source, "path": str(plugin.get("path") or "")})
        else:
            text = str(plugin or "")
            found.append({"name": text.split("@")[0], "source": text, "path": ""})
    return found


def plugins_loaded(events):
    """(plugin names, plugin errors) from system/init."""
    init = trace.init_event(events)
    if init is None:
        return [], []
    errors = init.get("plugin_errors")
    return [e["name"] for e in plugin_entries(events)], errors if isinstance(errors, list) else []


def hook_responses(events, hook_event=None):
    """The system events with subtype hook_response; `hook_event` (compared case-insensitively) narrows them to one hook event."""
    found = [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"]
    if hook_event is None:
        return found
    return [e for e in found if str(e.get("hook_event", "")).lower() == hook_event.lower()]


def hook_errors(events):
    """[(hook event, exit code, text)] of the hook_response events that are an INFRASTRUCTURE error: an exit code outside (0, 2), or no
    exit code and outcome 'error'. Exit code 2 (a Stop hook asking for work, outcome 'error' too) and 0 are not errors. The text is the
    first non-empty of output, stderr, stdout."""
    found = []
    for e in hook_responses(events):
        code = e.get("exit_code")
        if isinstance(code, bool) or not (isinstance(code, int) or code is None):
            code = None
        if (code is not None and code not in (0, 2)) or (code is None and e.get("outcome") == "error"):
            text = next((str(e[k]).strip() for k in ("output", "stderr", "stdout") if str(e.get(k) or "").strip()), "")
            found.append((str(e.get("hook_event") or e.get("hook_name") or "?"), code, text))
    return found


def bash_commands(events):
    """[(tool_use id, command)] of every Bash call of the main conversation, in order."""
    commands = []
    for event in events:
        if event.get("type") == "assistant" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("input"), dict):
                    commands.append((str(block.get("id", "")), str(block["input"].get("command", ""))))
    return commands


def tool_results(events):
    """{tool_use_id: (text, is_error)} of the tool_result blocks of the main conversation. The content is a string or a list of text
    blocks (the recorded shape is a string); is_error is False when the field is absent."""
    found = {}
    for event in events:
        if event.get("type") == "user" and trace.is_main(event):
            for block in trace.blocks(event):
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
                    found[str(block.get("tool_use_id", ""))] = (content if isinstance(content, str) else "", bool(block.get("is_error")))
    return found


def fed_to_model(events, needle):
    """True when `needle` occurs in the JSON text of any user, assistant or result event: it reached the conversation, however encoded."""
    return any(needle in json.dumps(e, ensure_ascii=False) for e in events if e.get("type") in ("user", "assistant", "result"))


def permission_denials(events):
    """The raw denials: the last result event's `permission_denials`, then every system `permission_denied` event."""
    result = trace.result_event(events) or {}
    denials = result.get("permission_denials")
    return (list(denials) if isinstance(denials, list) else []) + [e for e in events if e.get("type") == "system" and e.get("subtype") == "permission_denied"]


def continuation(events, needle):
    """How a blocking Stop hook's report shows in the trace: 'fed' (it reached the conversation), 'hook-event-only' (a Stop
    hook_response with exit code 2 or carrying the text, but nothing in the conversation: the hook blocked, the model never saw
    the report), or 'none'. Only 'fed' is evidence of delivery."""
    if fed_to_model(events, needle):
        return "fed"
    for response in hook_responses(events, "Stop"):
        if response.get("exit_code") == 2 or needle in json.dumps(response, ensure_ascii=False):
            return "hook-event-only"
    return "none"
