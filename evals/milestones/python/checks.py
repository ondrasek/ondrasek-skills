"""Deterministic checks of one run of the milestones plugin.

Every check is a pure function of a `Case` and returns (id, passed, note); the note says what was measured. The file state (two
snapshots, see state.py) is the ground truth; the trace is secondary, because it shows only the main conversation: a tracker write
by a subagent is visible in the files but not in the trace. Groups:

    precondition  was the behaviour under test exercised at all? (the agent run completed and left both snapshots; no installed copy of
                  the plugin leaked in beside the one under test; the script was not permission-denied; the Stop hook ran and its
                  report reached the model). A failure makes the trial INCONCLUSIVE, not FAIL: the plugin is not to blame.
    process       did the plugin load, the skill trigger exactly when it should, the tracker change only through milestones.py?
    gate          what must never happen: a changed tracker on a read, a direct write, a write attempt on a read or a quiet run, a commit,
                  a stray file, a tracker nobody asked for.
    outcome       is the tracker valid and what the prompt asked for, and the rest untouched?
    soft          ADVISORY (WARN): checks that rest only on parsing the model's own prose (its reply). They are computed and listed with
                  their note, but never change the verdict or the exit code: regexes over free text can be neither non-evadable nor free
                  of false positives, so they may not by themselves fail a trial. Everything else is decided by the workspace and git
                  state or by structured trace facts (tool name, command argv, exit status).

Which checks apply to a row depends on its `expect` column (see run_checks). The user's own ~/.claude is not read by any check.
"""
import json
import os
import re
import shlex
from dataclasses import dataclass, field

import agent_trace as trace
import hook_trace as hooks
import schema_check
from cases import Row
from fixtures import MARKER, TRACKER, build as build_fixture

SKILL = "milestones"
HOOK_NEEDLE = "MILESTONES HOOK"
MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
# the one file that may change when the prompt asks for code: the reminder case (a function) and a negative on a fixture with a tracker
# (a docstring)
CODE_FILE = "src/textutil.py"
# the values of `needs` that set the statusline's "needs you" flag: the plugin's own milestones.is_you() takes exactly (case-insensitive)
# "you" or "author" (and the author's own name forms, which the fixture does not use). "me", "user" or "you (the key from legal)" do not.
USER_NEEDS = {"you", "author"}
WRITE_SUBCOMMANDS = {"set", "add", "remove", "title", "init"}
SCRIPT_SUBCOMMANDS = WRITE_SUBCOMMANDS | {"show"}
PYTHON_RE = re.compile(r"python[\d.]*$")
# A Bash command that writes the tracker file itself (a read such as `cat .cache/milestones.json` is not one). A command is split into
# segments on unquoted && || ; | and newlines (split_commands), and every pattern below is applied to ONE segment that names the tracker.
# A segment that runs the script is checked for a redirect into the tracker only.
# the end of the tracker's file name: `milestones.json.bak` or `milestones.json~` is another file
TRACKER_FILE = r"milestones\.json(?![\w~]|\.\w)"
REDIRECT_RE = re.compile(rf">{{1,2}}\s*[\"']?[^\s|;&]*{TRACKER_FILE}")
MOVE_RE = re.compile(rf"\b(tee|mv|rm|truncate|sponge)\b[^|;&\n]*{TRACKER_FILE}"
                     rf"|\b(cp|install|ln)\b[^|;&\n]*{TRACKER_FILE}[\"']?\s*$"      # the tracker as the destination, not the source
                     rf"|\bsed\b[^|;&\n]*\s(?:-\w*i|--in-place)[^|;&\n]*{TRACKER_FILE}"
                     rf"|\bperl\b[^|;&\n]*\s-(?![MmIdDC])[A-Za-z]*i[A-Za-z]*(?=[\s.=])[^|;&\n]*{TRACKER_FILE}"   # perl -pi -e ... / -i.bak
                     rf"|\bawk\b[^|;&\n]*-i\s*inplace[^|;&\n]*{TRACKER_FILE}"
                     rf"|\bdd\b[^|;&\n]*\bof=[\"']?\S*{TRACKER_FILE}")
# `git rm --cached .cache/milestones.json` only drops the path from the index: the file is not written
GIT_RM_CACHED_RE = re.compile(r"\bgit\b[^|;&\n]*\brm\b[^|;&\n]*--cached\b")
# a Python write in a segment that names the tracker: open(..., 'w') (the mode must be a string inside the open( call itself, one level of
# nested parentheses allowed, so that a later string argument of print() or json.load() is not a mode), mode='w', write_text, json.dump(
# (not json.dumps, and not json.dump(x, sys.stdout), which prints; a file opened for writing in the same call is caught by its open( mode), shutil and os file moves
PY_MODE = r"[\"'](?:[wax][bt]?\+?|r[bt]?\+|r\+[bt]?)[\"']"
_ARGS = r"(?:[^()\n]|\([^()\n]*\))*"      # the arguments of open( up to its mode: one level of nested parentheses
PY_WRITE_RE = re.compile(rf"\bopen\((?:{_ARGS},)?\s*{PY_MODE}|\bmode\s*=\s*{PY_MODE}|\b(?:write_text|write_bytes)\("
                         rf"|\bjson\.dump\((?![^\n]*,\s*sys\.std(?:out|err)\s*[,)])|\bshutil\.(?:copy\w*|move)\(|\bos\.(?:replace|rename|remove|unlink)\(")

GROUPS = {
    "agent-completed": "precondition", "plugin-under-test-isolated": "precondition", "not-permission-blocked": "precondition",
    "reminder-fired": "precondition", "reminder-delivered": "precondition",
    "skill-loaded": "process", "skill-trigger": "process", "shown-by-script": "process", "written-by-script": "process",
    "show-reads-only": "gate", "no-direct-tracker-write": "gate", "tracker-not-committed": "gate", "nothing-else-changed": "gate",
    "no-tracker-when-negative": "gate", "no-write-attempt": "gate",
    "reply-names-current": "soft", "tracker-valid": "outcome", "wanted-state": "outcome", "others-untouched": "outcome",
    "timestamps-by-script": "outcome", "tracker-created": "outcome", "quiet-when-nothing-changed": "outcome",
}


@dataclass
class Case:
    row: Row
    events: list
    states: dict = field(default_factory=dict)    # before, after
    reply: str = ""
    run_note: str = ""                            # what run_evals noticed outside the trace: a non-zero exit, a missing state file
    plugin: str = ""                              # the --plugin directory under test, when known

    @classmethod
    def build(cls, row, events, states, run_note="", plugin=""):
        return cls(row, events, states if isinstance(states, dict) else {}, trace.final_reply(events), run_note, str(plugin or ""))

    @property
    def before(self):
        return self.states.get("before")

    @property
    def after(self):
        return self.states.get("after")


# ---- helpers -----------------------------------------------------------------------------------------------

def files(state):
    return (state or {}).get("files", {})


def sha(state, path):
    record = files(state).get(path)
    return record["sha256"] if record else None


def text_of(state, path):
    record = files(state).get(path)
    return record.get("text") if record else None


def changed_paths(a, b):
    """The paths added, removed or modified from snapshot a to snapshot b."""
    fa, fb = files(a), files(b)
    return sorted((set(fa) ^ set(fb)) | {p for p in set(fa) & set(fb) if fa[p]["sha256"] != fb[p]["sha256"]})


def tracker_of(state):
    """(the parsed tracker, '') or (None, why not) for the tracker file of a snapshot."""
    text = text_of(state, TRACKER)
    if text is None:
        return None, f"no {TRACKER}"
    try:
        data = json.loads(text)
    except ValueError:
        return None, "the tracker is not JSON"
    return (data, "") if isinstance(data, dict) else (None, "the tracker is not a JSON object")


def both_trackers(case):
    """(tracker before, tracker after, '') or (None, None, why) when either snapshot lacks a parsable tracker."""
    before, why_before = tracker_of(case.before)
    after, why_after = tracker_of(case.after)
    if before is None or after is None:
        return None, None, f"before the run: {why_before or 'ok'}; after: {why_after or 'ok'}"
    return before, after, ""


def items_of(data):
    """{id: item} of the well-formed items of a parsed tracker."""
    items = data.get("items") if isinstance(data, dict) else None
    return {i["id"]: i for i in items if isinstance(i, dict) and isinstance(i.get("id"), int)} if isinstance(items, list) else {}


def script_tokens(segment):
    """The argv from the script path on, or [] when the segment is not a call of the plugin's script: its command word (after env
    assignments, `env`, `command`, `exec`, `time`, `nohup`) is a Python interpreter (`python3`, `python3.12`) and a later token, before a
    -c or -m option, is a path whose last component is milestones.py (`echo python3 milestones.py set` and `grep milestones.py x` are
    not calls). One level only: a call wrapped in `bash -c '...'` is not seen (README, Limits)."""
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        tokens = segment.split()
    start = 0
    while start < len(tokens) and (re.match(r"[A-Za-z_]\w*=", tokens[start]) or tokens[start] in ("env", "command", "exec", "time", "nohup")):
        start += 1
    if start >= len(tokens) or not PYTHON_RE.match(os.path.basename(tokens[start])):
        return []
    for i in range(start + 1, len(tokens)):
        if tokens[i] in ("-c", "-m"):
            return []
        if os.path.basename(tokens[i]) == "milestones.py":
            return tokens[i:]
    return []


def script_invocations(command):
    """The milestones.py subcommands (show, set, add, remove, title, init) that a Bash command line runs, from its parsed argv: a text that
    merely mentions the script (an echo, a grep pattern, a python -c string) is not a call."""
    found = []
    for segment in split_commands(command):
        for token in script_tokens(segment)[1:4]:
            if token in SCRIPT_SUBCOMMANDS:
                found.append(token)
                break
    return found


def script_subcommands(events):
    """The milestones.py subcommands the main conversation ran through Bash, in order."""
    return [sub for _, command in hooks.bash_commands(events) for sub in script_invocations(command)]


def split_commands(command):
    """The segments of a shell command line, split on && || ; | and newlines outside single and double quotes."""
    segments, current, quote, i = [], [], "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                current.append(command[i])
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in ";|\n" or command.startswith("&&", i):
            segments.append("".join(current))
            current = []
            if ch == "&" or command.startswith("||", i):
                i += 1
        else:
            current.append(ch)
        i += 1
    segments.append("".join(current))
    return [seg for seg in segments if seg.strip()]


def writes_tracker(command):
    """True when a segment of the Bash command writes, moves or deletes the tracker file without going through the script."""
    for segment in split_commands(command):
        if "milestones.json" not in segment:
            continue
        if script_tokens(segment):
            if REDIRECT_RE.search(segment):
                return True
        elif GIT_RM_CACHED_RE.search(segment):
            continue
        elif REDIRECT_RE.search(segment) or MOVE_RE.search(segment) or PY_WRITE_RE.search(segment):
            return True
    return False


def script_key(command):
    """A script call without its path and quotes ('set 2 --state done'): the same call, quoted or not, compares equal."""
    return " ".join(command.split("milestones.py", 1)[-1].replace('"', "").replace("'", "").split())


def tool_path(tool_input):
    return str(tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or "")


# ---- precondition ------------------------------------------------------------------------------------------

def agent_completed(case):
    """The run finished and left evidence. Without it every gate and negative check is vacuous: a trace holding only the init event,
    a budget stop or a timeout leaves the files untouched, which is what a correct negative run also does. The reply text is NOT a
    condition: the Stop hook asks for a silent update, so an empty final reply is a correct run in the reminder cases, and requiring
    text would hide a real failure there behind INCONCLUSIVE."""
    problems = []
    if case.run_note:
        problems.append(f"run note: {case.run_note}")
    result = trace.result_event(case.events)
    if result is None:
        problems.append("no result event (the run did not finish)")
    elif result.get("is_error") or (isinstance(result.get("subtype"), str) and result["subtype"] != "success"):
        problems.append(f"the result event reports subtype {result.get('subtype')!r}, is_error {result.get('is_error')!r}")
    problems += [f"no {name} snapshot (state.json missing or empty)" for name in ("before", "after") if "files" not in (case.states.get(name) or {})]
    return "agent-completed", not problems, "the run finished with a result and both snapshots" if not problems else "; ".join(problems)


# the text of a refused call in a tool_result (UNCONFIRMED wording): such a result is a denial even when is_error is not set on it
DENIED_TEXT_RE = re.compile(r"permission[^.\n]{0,60}denied|denied[^.\n]{0,60}permission|was denied|haven't granted", re.I)


def looks_denied(text):
    return len(text) < 400 and bool(DENIED_TEXT_RE.search(text))


def not_permission_blocked(case):
    """A denied call to the script blocks the trial unless an identical call (same arguments, path and quotes aside) later succeeded.

    A call to the script counts as DENIED when (a) an element of `permission_denials` (or a system permission_denied event) carries its
    tool_use_id, whatever else it carries, or (b) its own tool_result is flagged is_error and reads like a denial. A denial element with
    no usable tool_use_id is matched to a (b) call with the same arguments when there is one; an element left over is matched by its
    tool_input (or its whole text) alone. A denied call is cleared only by a later call with the same arguments whose tool_result has
    no is_error and does not read like a denial; for a leftover element the successful-looking calls must outnumber the leftovers, so
    that a denial whose own tool_result lacks is_error cannot clear itself. All shapes are UNCONFIRMED: this degrades toward
    INCONCLUSIVE, never toward a silent pass.
    """
    results = hooks.tool_results(case.events)
    calls = [(ident, command) for ident, command in hooks.bash_commands(case.events) if "milestones.py" in command]
    call_ids = {ident for ident, _ in calls}
    elements = [d for d in hooks.permission_denials(case.events) if isinstance(d, dict) and d.get("tool_name") in (None, "Bash")]
    by_id = {str(d["tool_use_id"]) for d in elements if str(d.get("tool_use_id") or "") in call_ids}
    from_result = {ident for ident, _ in calls if ident not in by_id and ident in results and results[ident][1] and looks_denied(results[ident][0])}
    denied_ids = by_id | from_result
    commands = dict(calls)

    def command_of(denial):
        tool_input = denial.get("tool_input")
        return str(tool_input.get("command", "")) if isinstance(tool_input, dict) and tool_input.get("command") else json.dumps(denial, ensure_ascii=False)

    leftovers = [command_of(d) for d in elements if str(d.get("tool_use_id") or "") not in call_ids and "milestones.py" in json.dumps(d, ensure_ascii=False)]
    consumed = {}
    for ident in sorted(from_result):                       # a (b) call is the very denial that an unidentified element reports
        key = script_key(commands[ident])
        consumed[key] = consumed.get(key, 0) + 1
    owed = {}
    for command in leftovers:
        key = script_key(command)
        if consumed.get(key, 0) > 0:
            consumed[key] -= 1
        else:
            owed[key] = owed.get(key, 0) + 1
    succeeded = {}
    for ident, command in calls:
        if ident not in denied_ids and ident in results and not results[ident][1] and not looks_denied(results[ident][0]):
            succeeded[script_key(command)] = succeeded.get(script_key(command), 0) + 1
    blocked = [commands[i][:60] for i in sorted(denied_ids) if succeeded.get(script_key(commands[i]), 0) < 1]
    blocked += [c[:60] for c in leftovers if succeeded.get(script_key(c), 0) <= owed.get(script_key(c), 0) and owed.get(script_key(c), 0) > 0]
    blocked = list(dict.fromkeys(blocked))
    return "not-permission-blocked", not blocked, (f"denied calls to milestones.py: {len(denied_ids) + sum(owed.values())}; never retried successfully: "
                                                  f"{blocked or 'none'} (a denial means the grant did not match)")


def reminder_fired(case):
    fired = MARKER not in files(case.before) and MARKER in files(case.after)
    return "reminder-fired", fired, f"{MARKER} " + ("was created: the plugin's Stop hook ran" if fired else "does not exist after the run: the hook did not fire (or ran elsewhere)")


def reminder_delivered(case):
    """Only the hook's report in the conversation ('fed') or a changed tracker is evidence that the reminder reached the model. A Stop
    hook_response that blocked (exit code 2) without the report in the conversation is exactly what a headless run that does not
    continue on Stop would show, so it is not delivery. A changed tracker counts whatever the change is: a wrong or invented update is
    graded by the outcome checks (FAIL), not hidden as INCONCLUSIVE."""
    how = hooks.continuation(case.events, HOOK_NEEDLE)
    if how == "fed":
        return "reminder-delivered", True, "the hook's report is in the conversation"
    if sha(case.before, TRACKER) != sha(case.after, TRACKER):
        return "reminder-delivered", True, "attributed by state only: the tracker changed, the trace shows no hook report in the conversation"
    if how == "hook-event-only":
        return "reminder-delivered", False, ("the Stop hook blocked (hook_response) but its report never reached the model and the tracker did not change: "
                                             "whether Stop continues a headless run is unconfirmed")
    return "reminder-delivered", False, "no trace of the hook's report and the tracker did not change: whether Stop continues a headless run is unconfirmed"


# ---- process -----------------------------------------------------------------------------------------------

def milestones_entries(case):
    """([plugins[] entries named milestones], [those that are the --plugin-dir copy]): source '<name>@inline', or a path equal to --plugin."""
    entries = [e for e in hooks.plugin_entries(case.events) if e["name"] == SKILL]
    wanted = os.path.realpath(case.plugin) if case.plugin else ""
    inline = [e for e in entries if e["source"].endswith("@inline") or (wanted and e["path"] and os.path.realpath(e["path"]) == wanted)]
    return entries, inline


def plugin_under_test_isolated(case):
    """No other copy of the plugin was loaded beside the --plugin-dir one: an installed copy (source '<name>@<marketplace>') or a second
    copy is an environment leak, not a defect of the plugin, so a failure here is INCONCLUSIVE. Zero entries pass: skill-loaded says
    that the plugin did not load."""
    entries, inline = milestones_entries(case)
    leaked = [(e["source"], e["path"]) for e in entries if e not in inline] if inline else [(e["source"], e["path"]) for e in entries]
    ok = len(entries) <= 1 and not leaked
    return "plugin-under-test-isolated", ok, (f"plugin entries named {SKILL}: {[(e['source'], e['path']) for e in entries]} "
                                              f"(at most one, the --plugin-dir copy, is wanted); other copies: {leaked or 'none'}")


def skill_loaded(case):
    """The plugin under test is loaded as the --plugin-dir copy (source '<name>@inline' or a path equal to --plugin) and its skill is listed
    in system/init. Whether another copy leaked in is plugin-under-test-isolated."""
    skills = trace.init_skills(case.events)
    if skills is None:
        return "skill-loaded", False, "the trace has no system/init event"
    errors = hooks.plugins_loaded(case.events)[1]
    entries, inline = milestones_entries(case)
    listed = any(trace.skill_matches(s, SKILL) for s in skills)
    return "skill-loaded", listed and bool(inline), (f"plugin entries named {SKILL}: {[(e['source'], e['path']) for e in entries]} "
                                                      f"(one loaded with --plugin-dir is wanted), plugin errors {errors}; skill {SKILL} {'is' if listed else 'is NOT'} listed")


def skill_trigger(case):
    invoked = [s for s in trace.skills_invoked(case.events) if trace.skill_matches(s, SKILL)]
    ok = bool(invoked) == case.row.should_trigger
    return "skill-trigger", ok, f"Skill calls for {SKILL}: {len(invoked)}; should trigger: {case.row.should_trigger}"


def shown_by_script(case):
    """A `milestones.py show` call whose tool_result is a table, not an error: it holds the tracker's title and is not flagged is_error."""
    results = hooks.tool_results(case.events)
    title = (tracker_of(case.before)[0] or {}).get("title")
    calls = [(ident, command) for ident, command in hooks.bash_commands(case.events) if "show" in script_invocations(command)]
    problems = []
    for ident, command in calls:
        if ident not in results:
            problems.append("a show call has no tool_result in the trace")
            continue
        text, is_error = results[ident]
        if is_error or not text.strip() or (title and title not in text):
            problems.append(f"a show call returned no table (is_error {is_error}): {text.strip()[:60]!r}")
        else:
            return "shown-by-script", True, "milestones.py show ran and its output holds the tracker's table"
    return "shown-by-script", False, ("milestones.py show never ran (the agent did not use the script to show the tracker)" if not calls
                                      else "; ".join(problems))


def written_by_script(case):
    if sha(case.before, TRACKER) == sha(case.after, TRACKER):
        return "written-by-script", True, "the tracker did not change (wanted-state or tracker-created say whether it should have)"
    writes = [s for s in script_subcommands(case.events) if s in WRITE_SUBCOMMANDS]
    return "written-by-script", bool(writes), f"the tracker changed; milestones.py write calls in the trace: {writes or 'none'}"


# ---- gate --------------------------------------------------------------------------------------------------

def show_reads_only(case):
    before, after = sha(case.before, TRACKER), sha(case.after, TRACKER)
    return "show-reads-only", before == after, "the tracker is byte-identical" if before == after else "the tracker changed (or vanished) in a run that should only read it"


def no_direct_tracker_write(case):
    bad = []
    for name, tool_input in trace.tool_calls(case.events):
        if name in MUTATING_TOOLS and tool_path(tool_input).endswith("milestones.json"):
            bad.append(f"{name} {tool_path(tool_input)}")
        elif name == "Bash":
            command = str(tool_input.get("command", ""))
            if writes_tracker(command):
                bad.append(f"Bash: {command[:60]}")
    return "no-direct-tracker-write", not bad, f"direct write attempts on the tracker file (main conversation only; a denied attempt counts): {bad or 'none'}"


def no_write_attempt(case):
    """No milestones.py write subcommand (set, add, remove, title, init) in the trace, whatever came of it. In the read, quiet and negative
    cases the tracker may not be written at all: a denied call or one the script refused leaves the tracker as it was, so the state alone
    cannot tell that the agent tried. Parsed argv of the main conversation's Bash calls; a subagent's calls are not in the trace."""
    writes = [sub for sub in script_subcommands(case.events) if sub in WRITE_SUBCOMMANDS]
    return "no-write-attempt", not writes, f"milestones.py write calls in the trace (a denied or refused attempt counts): {writes or 'none'}"


def tracker_not_committed(case):
    """No .cache/ path in git, ever. In the show, update and create cases HEAD must not have moved at all; the reminder cases do not grade it,
    because an agent may legitimately commit the edit it was asked for."""
    after = case.after or {}
    tracked = sorted(p for p in {*after.get("tracked", []), *after.get("all_paths", [])} if p.startswith(".cache/"))
    moved = (case.before or {}).get("head") != after.get("head") and not case.row.expect.startswith("reminded")
    return "tracker-not-committed", not tracked and not moved, f".cache paths in git: {tracked or 'none'}; HEAD {'moved' if moved else 'did not move (or is not graded here)'}"


def allowed_changes(row):
    """The files outside the tracker that a run may change: the code file where the prompt asks for code, none elsewhere."""
    code_prompt = row.expect == "reminded-update" or (row.expect == "none" and TRACKER in build_fixture(row.fixture))
    return {CODE_FILE} if code_prompt else set()


def nothing_else_changed(case):
    """No other file added, removed or modified, including any other file under .cache/. The tracker and the hook's marker are graded
    by their own checks."""
    allowed = allowed_changes(case.row)
    stray = [p for p in changed_paths(case.before, case.after) if p not in allowed and p not in (TRACKER, MARKER)]
    return "nothing-else-changed", not stray, f"files changed other than the tracker and the marker (allowed: {sorted(allowed) or 'none'}): {stray or 'none'}"


def no_tracker_when_negative(case):
    exists = TRACKER in files(case.after)
    return "no-tracker-when-negative", not exists, "a tracker was created for a prompt that is not about one" if exists else "no tracker was created"


# ---- outcome -----------------------------------------------------------------------------------------------

# What may stand between a milestone's name and "finished": blanks (no newline: the next line is another item), a copula (with an adverb), one
# colon, dash, arrow, bar or opening parenthesis. Sentence punctuation (. ; ,) may not: "In progress: Storage layer. Completed 1/4" is
# not a claim that it is finished.
_JOIN = r"(?:[ \t]+|[ \t]*(?:'s|[ \t](?:is|are|was|has|has been|had been|have been))[ \t]*|[ \t]*[:\-\u2013\u2014\u2192|(][ \t]*)"
_ADVERB = r"(?:(?:now|already|all|officially|fully|just)[ \t]+)*"
_VERB = r"(?:finished|completed?|done|shipped|wrapped up)"
# and the verb must END the claim, on its own line: "done so far", "completed tables", "done 1/4" are partial, "done." "done," "done and"
# are not, and "Done:" is a label of the next item
_END = r"(?=[ \t]*(?:$|[.,;!?)\]|\u2014\u2013-]|\u2705)|[ \t]+(?:and|but|then)\b)"
# the clause before a match: a subordinator or negation there ("once/after Storage layer is done", "have not finished the Storage layer")
# means the reply does not say it is finished now
_HEDGE = re.compile(r"\b(?:once|after|until|when|before|if|unless|while|as soon as|not|never|yet|n't)\b|n't", re.I)
# a verb before the name: "we finished the Storage layer", "completed Storage layer", "done with the Storage layer"
_VERB_FIRST = r"(?:finished|completed?|shipped|wrapped up|done with)[ \t]+(?:the[ \t]+|with[ \t]+)?"


def _clause_before(text, start):
    return re.split(r"[.;!?\n]", text[:start])[-1]


def claims_done(reply, name):
    """True when the reply says `name` is finished: the name, then (is, has) finished, complete(d), done or shipped as the end of the
    line or clause, or the name followed by a check mark (also after a colon), or a verb finished/completed/shipped right before the
    name. Markdown emphasis is ignored. A match in a clause with once, after, until, when, if, not, never or yet does not count. A
    heuristic over prose with known limits (README): other phrasings and hedges after the name ("done, apart from ...") are not read.
    It is ADVISORY: reply-names-current is a soft check and never decides a verdict."""
    text = re.sub(r"[*_`]", "", reply)
    nm = re.escape(re.sub(r"[*_`]", "", name))
    pattern = re.compile(rf"{nm}{_JOIN}{_ADVERB}{_VERB}{_END}|{nm}[ \t]*:?[ \t]*\u2705|{_VERB_FIRST}{nm}\b", re.I | re.M)
    return any(not _HEDGE.search(_clause_before(text, m.start())) for m in pattern.finditer(text))


def reply_names_current(case):
    before, _ = tracker_of(case.before)
    doing = [i["name"] for i in items_of(before).values() if i.get("state") == "doing"]
    named = [n for n in doing if n.lower() in case.reply.lower()]
    wrong = [n for n in named if claims_done(case.reply, n)]
    return "reply-names-current", bool(named) and not wrong, f"the reply names the milestone in progress ({doing}): {named or 'no'}; says it is finished: {wrong or 'no'}"


def tracker_valid(case):
    data, why = tracker_of(case.after)
    if data is None:
        return "tracker-valid", False, why
    errors = schema_check.tracker_errors(data)
    return "tracker-valid", not errors, f"violations of milestones.schema.json: {errors or 'none'}"


def state_problems(case):
    """What is missing of the wanted item states and `needs` in the tracker after the run (an empty list: all there)."""
    data, why = tracker_of(case.after)
    if data is None:
        return [why]
    items, problems = items_of(data), []
    for ident, state in case.row.want.states:
        got = items.get(ident, {}).get("state")
        if got != state:
            problems.append(f"#{ident} is {got}, wanted {state}")
    for ident in case.row.want.needs:
        if not str(items.get(ident, {}).get("needs", "")).strip():
            problems.append(f"#{ident} has no needs")
    for ident in case.row.want.needs_you:
        got = str(items.get(ident, {}).get("needs", "")).strip()
        if got.lower() not in USER_NEEDS:
            problems.append(f"#{ident} needs {got!r}, wanted exactly 'you' or 'author' (the values that set the statusline flag)")
    return problems


def wanted_state(case):
    problems = state_problems(case)
    want = case.row.want
    return "wanted-state", not problems, f"wanted {want.states} needs {want.needs} needs-the-user {want.needs_you}: {problems or 'all hold'}"


def others_untouched(case):
    before, after, why = both_trackers(case)
    if before is None:
        return "others-untouched", False, why
    old, new = items_of(before), items_of(after)
    named = {i for i, _ in case.row.want.states} | set(case.row.want.needs) | set(case.row.want.needs_you)
    problems = ([f"#{i} removed or renumbered" for i in old if i not in new] + [f"#{i} added" for i in new if i not in old]
                + [f"#{i} changed" for i in old if i in new and i not in named and new[i] != old[i]]
                + [f"#{i} renamed" for i in old if i in new and i in named and new[i].get("name") != old[i].get("name")])
    if before.get("title") != after.get("title"):
        problems.append("the title changed")
    return "others-untouched", not problems, f"items the prompt did not name (and the names of those it did): {problems or 'unchanged'}"


def timestamps_by_script(case):
    before, after, why = both_trackers(case)
    if before is None:
        return "timestamps-by-script", False, why
    old, problems = items_of(before), []
    for ident, item in items_of(after).items():
        was = old.get(ident, {}).get("state")
        if item.get("state") == "doing" and was != "doing" and not item.get("started"):
            problems.append(f"#{ident} became doing without started")
        if item.get("state") == "done" and was != "done" and not item.get("finished"):
            problems.append(f"#{ident} became done without finished")
    if not after.get("updated") or after.get("updated") == before.get("updated"):
        problems.append("the top-level updated was not refreshed")
    return "timestamps-by-script", not problems, f"bookkeeping only milestones.py does: {problems or 'present'}"


def unmatched_words(words, names):
    """The words left without an item when every word needs its OWN item name containing it (case-insensitive): a maximum bipartite
    matching, so that one item named 'schema importer migration' beside junk items satisfies one word, not three."""
    owner = {}                                              # name index -> word index

    def place(w, seen):
        for n, name in enumerate(names):
            if words[w].lower() in name.lower() and n not in seen:
                seen.add(n)
                if n not in owner or place(owner[n], seen):
                    owner[n] = w
                    return True
        return False

    placed = [place(w, set()) for w in range(len(words))]
    return [word for word, ok in zip(words, placed) if not ok]


def tracker_created(case):
    data, why = tracker_of(case.after)
    if data is None:
        return "tracker-created", False, why
    want, items = case.row.want, items_of(data)
    problems = ["a tracker existed before the run"] if TRACKER in files(case.before) else []
    if data.get("title") != want.title:
        problems.append(f"title is {data.get('title')!r}, wanted {want.title!r}")
    if len(items) != want.count:
        problems.append(f"{len(items)} items, wanted {want.count}")
    problems += [f"no distinct item name mentions {word!r}" for word in unmatched_words(want.names, [str(i.get("name", "")) for i in items.values()])]
    return "tracker-created", not problems, f"the new tracker: {problems or 'title, count and names as wanted'}"


def quiet_when_nothing_changed(case):
    before, after, why = both_trackers(case)
    if before is None:
        return "quiet-when-nothing-changed", False, why
    old, new = items_of(before), items_of(after)
    fields = ("name", "state", "left", "needs")
    changed = sorted({i for i in set(old) | set(new) if i not in old or i not in new or any(old[i].get(f) != new[i].get(f) for f in fields)})
    retitled = [] if before.get("title") == after.get("title") else ["the title"]
    return "quiet-when-nothing-changed", not changed and not retitled, f"items whose name, state, left or needs differ from before: {changed or 'none'}; changed title: {retitled or 'no'}"


# ---- which checks apply ------------------------------------------------------------------------------------

SKILL_RUN = [agent_completed, plugin_under_test_isolated, skill_loaded, skill_trigger, not_permission_blocked]
REMINDED = [agent_completed, plugin_under_test_isolated, skill_loaded, reminder_fired, reminder_delivered, not_permission_blocked]
GATES = [no_direct_tracker_write, tracker_not_committed, nothing_else_changed]
BY_EXPECT = {
    "show": SKILL_RUN + [shown_by_script, show_reads_only, no_write_attempt, reply_names_current] + GATES,
    "update": SKILL_RUN + [tracker_valid, wanted_state, others_untouched, timestamps_by_script, written_by_script] + GATES,
    "create": SKILL_RUN + [tracker_valid, tracker_created, written_by_script] + GATES,
    "reminded-update": REMINDED + [tracker_valid, wanted_state, others_untouched, timestamps_by_script, written_by_script] + GATES,
    "reminded-quiet": REMINDED + [quiet_when_nothing_changed, no_write_attempt, tracker_valid] + GATES,
}

# What a failed precondition can explain. A trial with a failed precondition is INCONCLUSIVE only when every failed hard check is
# explained by one of them; a failed check that none explains is the agent's own doing and the trial is FAIL (run_evals.verdict).
# A script call that was denied, or a hook that never reached the model, leaves the script's effects missing (state, timestamps, the
# table); it does not explain a direct tracker write, a commit, a stray file, a write attempt or a changed tracker on a read. A run that
# did not complete, or an environment leak, explains the missing process and outcome facts, but not those gates either.
NOT_RUN_EFFECTS = {"wanted-state", "timestamps-by-script", "written-by-script", "tracker-valid", "others-untouched", "tracker-created", "shown-by-script"}
EXPLAINED_BY = {
    "agent-completed": NOT_RUN_EFFECTS | {"skill-loaded", "skill-trigger", "quiet-when-nothing-changed"},    # quiet: a missing snapshot compares nothing
    "plugin-under-test-isolated": NOT_RUN_EFFECTS | {"skill-loaded", "skill-trigger"},
    "not-permission-blocked": NOT_RUN_EFFECTS,
    "reminder-fired": NOT_RUN_EFFECTS,
    "reminder-delivered": NOT_RUN_EFFECTS,
}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`.

    A `none` case on a fixture that has a tracker must leave it byte-identical and may change only src/textutil.py (the prompt may edit
    code); on a fixture without one it must create no tracker and change no file. Either way nothing may write the tracker directly or
    commit anything. Which of the two applies comes from the row's fixture, never from the snapshots (a missing snapshot must not
    pick the weaker branch).
    """
    if case.row.expect == "none":
        has_tracker = TRACKER in build_fixture(case.row.fixture)
        chosen = [agent_completed, plugin_under_test_isolated, skill_loaded, skill_trigger, not_permission_blocked,
                  show_reads_only if has_tracker else no_tracker_when_negative, no_write_attempt] + GATES
    else:
        chosen = BY_EXPECT[case.row.expect]
    return [check(case) for check in chosen]
