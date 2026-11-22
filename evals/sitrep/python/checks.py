"""Deterministic checks of one run of the sitrep skill.

Every check is a pure function of a `Case` and returns (id, passed, note); the note says what was measured. The recorded state
(state.py: workspace snapshots, the report directory, parsed data.js) is the ground truth; the trace is secondary, because it
shows only the main conversation. Groups:

    precondition  was the behaviour exercised at all? A failure makes the trial INCONCLUSIVE, not FAIL (see `verdict`): the run did
                  not finish (`run-completed`), the workspace snapshots were not recorded (`state-recorded`: a case never recorded, a
                  trace without its .state.json), a report.sh that was tried left no report directory, or a permission denial
                  blocked report.sh or data.js and no later successful call of the same effect redid it, or turn 1 had a denied Bash call of any kind and never tried `report.sh new` (the harness's
                  own grants denied the agent's exploration), which says nothing about the skill (a plugin that did not load, or not the
                  one under test, `skill-loaded`, is treated the same way)
    process       did the skill load and trigger, and was the report made and removed by report.sh, not by hand?
    gate          nothing was written into the project, the template and the tracker are untouched, the report holds only data.js
    outcome       data.js defines a valid window.SITREP with a dependency between two items, the milestone item and the numbers of the
                  progress strip, the start-here card; the report is gone after the second turn
    soft          ADVISORY (WARN): checks that rest only on matching words in text the model wrote (the reply, the wording of item
                  titles). They are computed and listed, with their note, but never change the verdict or the exit code: a regex over
                  free text cannot be made both non-evadable and free of false positives. Each keeps its failing run in the tests.

Decided by workspace state or by structured trace facts (tool name, the argv of a Bash command parsed by shell_argv.py, the result
subtype) are the other groups; a command line is never judged by text that merely mentions a command (`cat report.sh`, `grep mkdir x`).

No check reads or depends on the user's own ~/.claude/CLAUDE.md, so results do not vary by machine.
Which checks apply to a row depends on its `expect` column (see BY_EXPECT).
"""
import json
import os
import re
from collections import namedtuple
from types import SimpleNamespace
from dataclasses import dataclass, field

import agent_trace as trace
import fixtures
import hook_trace
import shell_argv
import sitrep_data
import state
from cases import Row

SKILL = "sitrep"
TRACKER = ".cache/milestones.json"
MKDIR_NAMES = {"mkdir", "mktemp"}                  # command words (argv[0] basename) that create a directory by hand
REMOVER_NAMES = {"rm", "rmdir", "unlink"}          # command words that remove; find -delete / -exec rm and an interpreter's rmtree are read in removals()
RMTREE_RE = re.compile(r"\brmtree\b")
SCRIPT_NAME = "report.sh"
DATA_JS_PATH_RE = re.compile(r"(?:^|/)data\.js$")                 # a file_path of a Write or Edit
DATA_JS_ALONE_RE = re.compile(r"\S*(?:^|/)data\.js")             # fullmatch: a string that is only a path (denials of unknown shape)
GUARDED_TOOLS = ("Bash", "Write", "Edit")
# how a refusal might read in a tool_result (the wording is unconfirmed: README item 1)
DENIED_BASH_RE = re.compile(r"Permission to use Bash\b[^\n]{0,200}?\bdenied", re.IGNORECASE)     # the dontAsk denial text of a Bash call (seen in a real trace, Claude Code 2.1.330)
REFUSING_RE = re.compile(r"refusing to delete")      # what report.sh clean prints when it declines (plugins/sitrep/skills/sitrep/scripts/report.sh)
REFUSED_RE = re.compile(r"permission|not allowed|denied|not granted|disallowed|requires approval|not permitted", re.IGNORECASE)
LIST_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
MAX_LIST_LINES = 4
MAX_ITEM_UNITS = 4        # lines and sentences of the reply that name a planted topic or an item title: five or more is the list pasted into chat
MAX_REPLY_CHARS = 1500    # a hand-over is two or three lines (SKILL.md step 3); a longer reply is a report pasted into chat (a guess: README item 4)
UNIT_SPLIT_RE = re.compile(r"\n+|(?<=[.!?])\s+")
ABBREVIATION_RE = re.compile(r"\b(?:vs|e\.g|i\.e|etc|cf)\.", re.IGNORECASE)    # a period after these does not end a sentence
NEGATIVE_WRITES_RE = re.compile(r"\.html?$|(^|/)data\.js$|sitrep", re.IGNORECASE)

GROUPS = {
    "skill-loaded": "process", "skill-trigger": "process", "created-by-script": "process", "cleaned-by-script": "process",
    "run-completed": "precondition", "state-recorded": "precondition", "not-permission-blocked": "precondition", "report-located": "precondition",
    "report-outside-project": "gate", "project-untouched": "gate", "index-untouched": "gate", "only-data-added": "gate",
    "tracker-untouched": "gate", "no-report-when-negative": "gate",
    "data-defines-sitrep": "outcome", "data-parses": "outcome", "data-valid": "outcome",
    "dependency-recorded": "outcome", "start-here-set": "outcome", "milestone-item": "outcome", "milestone-strip": "outcome",
    "report-cleaned": "outcome",
    # advisory: text heuristics over what the model wrote; never decide the verdict (see verdict)
    "reply-hands-over": "soft", "items-cover-planted": "soft", "dependency-direction": "soft", "milestone-current": "soft",
}
PRECONDITIONS = {cid for cid, group in GROUPS.items() if group == "precondition"}
SOFT = {cid for cid, group in GROUPS.items() if group == "soft"}
TURN_TWO = {"cleaned-by-script", "report-cleaned"}   # the checks that read what the second turn did


def split_turns(events):
    """The events of each turn: a turn ends with its result event (a resumed session prints one per turn)."""
    turns, current = [], []
    for event in events:
        current.append(event)
        if event.get("type") == "result":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns or [[]]


@dataclass
class Case:
    row: Row
    events: list
    states: dict = field(default_factory=dict)    # before, after1, after2, layout, tmp_sitrep, report1, report2 (see state.py)
    turns: list = field(default_factory=list)
    reply1: str = ""
    run_note: str = ""                            # what the runner noted: a non-zero exit, a missing session id, a missing recording

    @classmethod
    def build(cls, row, events, states, note=""):
        turns = split_turns(events)
        return cls(row, events, states or {}, turns, trace.final_reply(turns[0]), note or "")

    @property
    def report(self):
        """The record of the report directory after turn 1 (report_snapshot), {} when the run has none."""
        return self.states.get("report1") or {}

    @property
    def data(self):
        """The parsed window.SITREP when it is an object, else None."""
        parsed = self.report.get("parsed")
        return parsed if isinstance(parsed, dict) else None

    @property
    def items(self):
        """The items of the parsed data that are objects."""
        items = (self.data or {}).get("items")
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


# ---- helpers -----------------------------------------------------------------------------------------------

def files(snap):
    return (snap or {}).get("files", {})


def clean_status(snap):
    """`git status --porcelain` without Claude Code's own `?? .claude/` marker (whether headless runs create one is unconfirmed)."""
    return [line for line in (snap or {}).get("status", "").splitlines() if not line.startswith("?? .claude/")]


def diff_states(a, b):
    """(added paths, removed paths, modified paths, HEAD or status changed) from snapshot a to snapshot b."""
    fa, fb = files(a), files(b)
    modified = sorted(p for p in set(fa) & set(fb) if fa[p]["sha256"] != fb[p]["sha256"])
    moved = (a or {}).get("head") != (b or {}).get("head") or clean_status(a) != clean_status(b)
    return sorted(set(fb) - set(fa)), sorted(set(fa) - set(fb)), modified, moved


def describe(diff):
    added, removed, modified, moved = diff
    parts = [f"added {added}" if added else "", f"removed {removed}" if removed else "", f"modified {modified}" if modified else "", "HEAD or status changed" if moved else ""]
    return ", ".join(p for p in parts if p) or "no change"


def workspace_chain(case):
    """The workspace snapshots in order: before, after turn 1 and, for a two-turn case, after turn 2 (None when one is missing)."""
    chain = [case.states.get("before"), case.states.get("after1")] + ([case.states.get("after2")] if case.row.reply else [])
    return chain if all(chain) else None


def missing_snapshots(case):
    """The recorded snapshots the case's checks compare that are absent: `before` and `after1` for every case, and the report-less
    negative cases also need `tmp_sitrep` (the listing of the trial TMPDIR, [] when nothing was made). The one after turn 2 is not here:
    it belongs to turn 2, and `run-completed` reports it per turn."""
    names = ["before", "after1"] + (["tmp_sitrep"] if case.row.expect == "none" else [])
    empty_is_recorded = {"tmp_sitrep"}      # [] is a real record (no report directory); an empty snapshot is not
    return [name for name in names if case.states.get(name) is None or (not case.states[name] and name not in empty_is_recorded)]


def problem_list(problems):
    return "; ".join(problems[:3]) + (f"; and {len(problems) - 3} more" if len(problems) > 3 else "")


# ---- process -----------------------------------------------------------------------------------------------

def _same_path(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def script_calls(command):
    """[(path as written, verb, args)] of every call of report.sh in `command`, read from the parsed argv: the command word (or the script
    given to sh/bash) is named report.sh. `cat report.sh` and `grep x report.sh` are not calls."""
    return [(c.path, next((a for a in c.args if not a.startswith("-")), ""), c.args)
            for c in shell_argv.commands(command) if c.name == SCRIPT_NAME]


def _verb_calls(command, *verbs):
    """The report.sh calls of `command` whose verb is one of `verbs` (the two verbs of the script are new and clean)."""
    return [call for call in script_calls(command) if call[1] in verbs]


def _script_call(command):
    """True for a `report.sh new` or `report.sh clean` call."""
    return bool(_verb_calls(command, "new", "clean"))


def _loose_script_call(text):
    """The same for a string of unknown origin (an element of a permission denial of unknown shape), which may be a sentence that quotes the command."""
    return bool(state.REPORT_NEW_RE.search(text) or state.REPORT_CLEAN_RE.search(text))


def _outside_plugin(command, plugin):
    """The absolute (or ~) report.sh paths called in `command` that do not lie under `plugin`. A relative or `$VAR` path is not judged: it may
    follow a `cd`, and the init event's plugin path is what tells the copies apart."""
    root, real = plugin.rstrip("/") + "/", os.path.realpath(plugin).rstrip("/") + "/"
    return [path for path, _, _ in _verb_calls(command, "new", "clean")
            if path.startswith(("/", "~")) and not path.startswith(root) and not os.path.realpath(os.path.expanduser(path)).startswith(real)]


def removals(command):
    """[(Command, text it names)] for the removals `command` runs, from its parsed argv: rm, rmdir, unlink (also /bin/rm, after `||`, `;`,
    `&&`, inside `sh -c`), find with -delete or -exec rm, an interpreter whose argument calls rmtree. The text is the arguments of that
    command, or the whole line for a command fed by xargs (its arguments come from the pipe)."""
    found = []
    for c in shell_argv.commands(command):
        executes = [c.args[i + 1] for i, a in enumerate(c.args[:-1]) if a in ("-exec", "-execdir", "-ok", "-okdir")]
        removes = (c.name in REMOVER_NAMES or (c.name == "find" and ("-delete" in c.args or any(os.path.basename(e) in REMOVER_NAMES for e in executes)))
                   or (c.name in shell_argv.INTERPRETERS and any(RMTREE_RE.search(a) for a in c.args)))
        if removes:
            found.append((c, command if c.via_xargs else " ".join(c.args)))
    return found


def creates_directories(command):
    """The mkdir / mktemp commands `command` runs (by argv: `grep mkdir notes.md` is not one)."""
    return [c for c in shell_argv.commands(command) if c.name in MKDIR_NAMES]


def skill_loaded(case):
    """The plugin under test loaded and nothing else answers to its name: exactly one plugin named `sitrep` whose path is the
    --plugin-dir of the run (recorded as states["plugin"]; a recording without it skips the path test) and, when the trace ran
    report.sh by an absolute path, only a report.sh under that directory. An installed copy of the plugin would otherwise be graded as the working copy."""
    skills = trace.init_skills(case.events)
    if skills is None:
        return "skill-loaded", False, "the trace has no system/init event"
    names, errors = hook_trace.plugins_loaded(case.events)
    entries = [path for name, path in hook_trace.plugin_entries(case.events) if name == SKILL]
    plugin = case.states.get("plugin")
    problems = []
    if not (SKILL in names and any(trace.skill_matches(s, SKILL) for s in skills)):
        problems.append("the plugin did not load")
    if len(entries) > 1:
        problems.append(f"{len(entries)} plugins are named {SKILL}: {entries}, so the copy under test cannot be told from another")
    if plugin and len(entries) == 1 and not _same_path(entries[0], plugin):
        problems.append(f"the loaded plugin is at {entries[0]!r}, not at the --plugin-dir {plugin!r}")
    if plugin:
        elsewhere = [c[:70] for _, c in hook_trace.bash_commands(case.events) if _script_call(c) and _outside_plugin(c, plugin)]
        if elsewhere:
            problems.append(f"report.sh ran from outside {plugin!r}: {elsewhere}")
    note = f"plugins {names}, {len(skills)} skills listed; " + ("; ".join(problems) or f"{SKILL} loaded from the plugin under test" + ("" if plugin else " (no --plugin-dir recorded: path not compared)"))
    return "skill-loaded", not problems, note + (f"; plugin errors: {errors}" if errors else "")


def skill_trigger(case):
    invoked = [s for s in trace.skills_invoked(case.events) if trace.skill_matches(s, SKILL)]
    ok = bool(invoked) == case.row.should_trigger
    return "skill-trigger", ok, f"Skill calls for {SKILL}: {len(invoked)}; should_trigger={case.row.should_trigger}"


def _cleaned_in_turn_one(case):
    """The `report.sh clean` calls of turn 1 that name the report directory (any, when none was recorded): the user has not said they
    are done yet, so the skill removed the report before they could look at it."""
    directory = case.report.get("dir")
    name = os.path.basename(directory) if directory else ""
    return [c[:60] for _, c in hook_trace.bash_commands(case.turns[0])
            if any(not name or name in " ".join(args) for _, _, args in _verb_calls(c, "clean"))]


def _ran_new(case):
    """Turn 1 called `report.sh new` (parsed argv)."""
    return any(_verb_calls(c, "new") for _, c in hook_trace.bash_commands(case.turns[0]))


def created_by_script(case):
    """The report was made by `report.sh new`, not by hand, and kept for the user (not cleaned in the same turn)."""
    commands = [c for _, c in hook_trace.bash_commands(case.turns[0])]
    ran = _ran_new(case)
    by_hand = [c[:60] for c in commands if creates_directories(c)]
    early = _cleaned_in_turn_one(case)
    return "created-by-script", ran and not by_hand and not early, \
        f"`report.sh new` {'ran' if ran else 'did NOT run'} in turn 1; mkdir/mktemp calls: {by_hand or 'none'}; `report.sh clean` in turn 1 (before the user is done): {early or 'none'}"


def cleaned_by_script(case):
    directory = case.report.get("dir")
    if not directory or len(case.turns) < 2:
        return "cleaned-by-script", False, "no report directory was recorded or the run has no second turn"
    name = os.path.basename(directory)
    results = hook_trace.tool_results(case.turns[1])
    calls = hook_trace.bash_commands(case.turns[1])
    # a clean call whose result says the script refused does not count; a removal counts when it names the directory or any sitrep.* path (a glob)
    cleaned = [c for call_id, c in calls if any(name in " ".join(args) for _, _, args in _verb_calls(c, "clean")) and REFUSING_RE.search(results.get(call_id, "")) is None]
    removed = [c[:60] for _, c in calls if any(name in text or "sitrep" in text for _, text in removals(c))]
    return "cleaned-by-script", bool(cleaned) and not removed, \
        f"`report.sh clean` calls naming {name} in turn 2 that the script did not refuse: {len(cleaned)}; removals (rm, find -delete, rmtree ...) naming it or a sitrep.* path: {removed or 'none'}"


# ---- precondition ------------------------------------------------------------------------------------------

def turn_problems(case):
    """{turn number: [problems]} for the turns the case needs (1, or 1 and 2). A turn has a problem when it has no result event or its
    result is not a success; the runner's note (a non-zero exit, a timeout, a missing session id) belongs to turn 1 in a one-turn case
    or when turn 1 itself has a problem, otherwise to turn 2 (turn 1 ended well, the second one could not start or failed); the missing
    snapshot after turn 2 belongs to turn 2. More result events than turns are a problem of every turn."""
    expected = 2 if case.row.reply else 1
    problems = {n: [] for n in range(1, expected + 1)}
    for n in problems:
        turn = case.turns[n - 1] if n <= len(case.turns) else []
        result = turn[-1] if turn and turn[-1].get("type") == "result" else None
        if result is None:
            problems[n].append(f"turn {n} has no result event")
        elif result.get("subtype") != "success" or result.get("is_error"):
            problems[n].append(f"turn {n} ended in {result.get('subtype')}{' (is_error)' if result.get('is_error') else ''}")
    total = sum(1 for e in case.events if e.get("type") == "result")
    if total > expected:
        for n in problems:
            problems[n].append(f"{total} result events, {expected} expected")
    if case.run_note:
        problems[1 if expected == 1 or problems[1] else 2].append(f"runner note: {case.run_note}")
    if expected == 2 and not case.states.get("after2"):
        problems[2].append("no workspace snapshot after turn 2")
    return problems


def run_completed(case):
    """Every turn the case needs ended in a success result event, the runner noted nothing (exit code, timeout, session id) and the
    snapshot after the last turn exists. Without this a crashed or aborted run passes every check that only looks for something absent."""
    problems = turn_problems(case)
    flat = [text for n in problems for text in problems[n]]
    return "run-completed", not flat, "; ".join(flat) or f"{len(problems)} turn(s), each ended in a success result"


def state_recorded(case):
    """The state snapshots exist (see missing_snapshots). Nothing was measured without them, so a missing one is not a finding about the
    skill: a case that was never recorded (`--fixtures` on a directory without it) or a trace without its .state.json is INCONCLUSIVE."""
    missing = missing_snapshots(case)
    return "state-recorded", not missing, ("the workspace snapshots needed are all recorded" if not missing else
        f"not recorded: {missing} (a case never run or recorded, or a trace without its .state.json): nothing was measured, so this says nothing about the skill")


def _strings(value):
    """Every string inside a JSON-like value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _strings(v)]
    if isinstance(value, list):
        return [t for v in value for t in _strings(v)]
    return []


def call_kinds(name, tool_input):
    """What a call of tool `name` touches that matters here: "script" (a Bash `report.sh new` or `clean`) and "data" (a Write or Edit of
    a data.js). A command that merely mentions data.js (`node --check .../data.js`) or report.sh (`cat report.sh`) touches neither."""
    kinds = set()
    if name == "Bash" and _script_call(str(tool_input.get("command", ""))):
        kinds.add("script")
    if name in ("Write", "Edit") and DATA_JS_PATH_RE.search(str(tool_input.get("file_path", "")).strip()):
        kinds.add("data")
    return kinds


def denial_kinds(denial):
    """call_kinds of one permission_denials element. Its shape is unconfirmed (README item 7): tool_name and tool_input are used when
    they are there, otherwise every string in it is read as a command (a report.sh call) or as a bare path (a data.js)."""
    name, tool_input = denial.get("tool_name", ""), denial.get("tool_input")
    if name in GUARDED_TOOLS and isinstance(tool_input, dict):
        return call_kinds(name, tool_input)
    if name not in ("",) + GUARDED_TOOLS:
        return set()
    strings = _strings(denial)
    return ({"script"} if any(_loose_script_call(t) for t in strings) else set()) | ({"data"} if any(DATA_JS_ALONE_RE.fullmatch(t.strip()) for t in strings) else set())


def _reads_as_refusal(name, text):
    """Whether the tool_result `text` of a call of tool `name` reads like a refusal. A Write or Edit that succeeded may echo what was
    written ("The file ... has been updated. Here's the result of running `cat -n` ..."), including item text such as "waiting for
    permission from legal": those results, and anything past the first 200 characters of a Write or Edit result, are not read."""
    text = text.strip()
    if name in ("Write", "Edit"):
        if text.startswith(("The file ", "File created successfully")) or "cat -n" in text[:300]:
            return False
        return bool(REFUSED_RE.search(text[:200]))
    return bool(REFUSED_RE.search(text[:300]))


def _refusals_in(events):
    """Calls of one turn that run report.sh or write data.js (Bash, Write, Edit) whose result reads like a refusal: a denial that arrives as tool_result text."""
    results = hook_trace.tool_results(events)
    return [f"{name}: {results[call_id].strip()[:70]!r}" for call_id, name, tool_input in hook_trace.tool_uses(events)
            if call_kinds(name, tool_input) and _reads_as_refusal(name, results.get(call_id, ""))]


def _refusals(case):
    return [r for turn in case.turns for r in _refusals_in(turn)]


def denied_bash(events):
    """[(tool_use_id, command)] of the Bash calls of `events` (one turn) that were denied, each once, whichever channel reported it: an
    element of the result event's permission_denials or a system permission_denied event naming the Bash tool (the real shapes: a dict
    with tool_name, tool_use_id and, in the result event only, tool_input), or a Bash tool_result that is not marked as a success and
    reads like the dontAsk denial ('Permission to use Bash has been denied ...'). The command is looked up by tool_use_id when the
    denial does not carry it ('' when it cannot be found)."""
    uses = {call_id: tool_input for call_id, name, tool_input in hook_trace.tool_uses(events) if name == "Bash"}
    found = {}
    for denial in hook_trace.permission_denials(events):
        if denial.get("tool_name") != "Bash":
            continue
        call_id, tool_input = str(denial.get("tool_use_id") or ""), denial.get("tool_input")
        command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else str(uses.get(call_id, {}).get("command", ""))
        found.setdefault(call_id or command, command)
    for call_id, text in hook_trace.tool_results(events, unless_ok=True).items():
        if call_id in uses and DENIED_BASH_RE.search(text.strip()[:300]):
            found.setdefault(call_id, str(uses.get(call_id, {}).get("command", "")))
    return list(found.items())


def unexercised_denials(case):
    """The denied Bash calls of turn 1 when turn 1 never tried `report.sh new` (else []): the agent was stopped by the permission
    system before it reached the behaviour under test, e.g. an exploration command chained from a grant the harness did not give.
    A denied unrelated call followed by a `report.sh new` that ran is not one (an attempted `new` that was denied is judged by
    denial_kinds)."""
    return [] if _ran_new(case) else denied_bash(case.turns[0])


Call = namedtuple("Call", "turn id name input text ok")


def call_log(case):
    """[Call] of every tool call of the main conversation, in order, over all turns. `text` is the tool_result ('' when it has none),
    `ok` is True when the call completed: it has a tool_result, that result does not say is_error: true and does not read like a refusal."""
    results, errored = hook_trace.tool_results(case.events), hook_trace.errored_results(case.events)
    return [Call(n, call_id, name, tool_input, results.get(call_id, ""),
                 call_id in results and call_id not in errored and not _reads_as_refusal(name, results[call_id]))
            for n, turn in enumerate(case.turns, start=1) for call_id, name, tool_input in hook_trace.tool_uses(turn)]


def effects(name, tool_input):
    """What a call of tool `name` does that a denial leaves undone: "script:new" / "script:clean" (a Bash call of that report.sh verb, any
    spelling of the path: quoted, absolute, through a variable) and "data:<path>" (a Write or Edit of that data.js)."""
    found = set()
    if name == "Bash":
        found |= {f"script:{verb}" for _, verb, _ in _verb_calls(str(tool_input.get("command", "")), "new", "clean")}
    path = str(tool_input.get("file_path", "")).strip()
    if name in ("Write", "Edit") and DATA_JS_PATH_RE.search(path):
        found.add(f"data:{os.path.normpath(path)}")
    return found


def _denial_effects(denial, call):
    """The effects of one permission_denials element. A denial that names its tool and input is read from them; one with only a
    tool_use_id (a system permission_denied event) from the call it names (`call`, or None); otherwise every string in it is read as a
    command (a report.sh call) or as a bare path (a data.js), like denial_kinds."""
    name, tool_input = denial.get("tool_name", ""), denial.get("tool_input")
    if name in GUARDED_TOOLS and isinstance(tool_input, dict):
        return effects(name, tool_input)
    if call is not None and call.name in GUARDED_TOOLS and name in ("",) + GUARDED_TOOLS:
        return effects(call.name, call.input)
    if name not in ("",) + GUARDED_TOOLS:
        return set()
    strings = _strings(denial)
    return ({"script:new"} if any(state.REPORT_NEW_RE.search(t) for t in strings) else set()) | ({"script:clean"} if any(state.REPORT_CLEAN_RE.search(t) for t in strings) else set()) \
        | {f"data:{os.path.normpath(t.strip())}" for t in strings if DATA_JS_ALONE_RE.fullmatch(t.strip())}


def _locate(log, denial, turn):
    """The index in `log` of the call a denial element without a usable tool_use_id is about: a call of the same tool in the same turn with
    the same command (Bash) or file_path (Write, Edit). Among several the one that did not complete, else the first. None when the element
    names no tool and input: it cannot be placed, so no later call can answer it."""
    name, tool_input = denial.get("tool_name"), denial.get("tool_input")
    if name not in GUARDED_TOOLS or not isinstance(tool_input, dict):
        return None
    key = "command" if name == "Bash" else "file_path"
    found = [i for i, c in enumerate(log) if c.turn == turn and c.name == name and str(c.input.get(key, "")).strip() == str(tool_input.get(key, "")).strip()]
    return next((i for i in found if not log[i].ok), found[0] if found else None)


def denials(case):
    """[(turn, index in call_log or None, effects, description)] of every denial of report.sh `new` or `clean` or of a Write or Edit of
    a data.js, whichever channel reported it: an element of a result event's permission_denials or a system permission_denied event, or
    the tool_result of such a call when it reads like a refusal. The same call seen through two channels is listed twice (both are
    cleared, or not, together)."""
    log = call_log(case)
    ids = [c.id for c in log]
    found = []
    for n, turn in enumerate(case.turns, start=1):
        for d in hook_trace.permission_denials(turn):
            call_id = str(d.get("tool_use_id") or "")
            at = ids.index(call_id) if call_id in ids else _locate(log, d, n)
            kinds = _denial_effects(d, log[at] if at is not None else None)
            if kinds:
                found.append((n, at, kinds, json.dumps(d)[:80]))
    for at, c in enumerate(log):
        if effects(c.name, c.input) and _reads_as_refusal(c.name, c.text):
            found.append((c.turn, at, effects(c.name, c.input), f"{c.name}: {c.text.strip()[:70]!r}"))
    return found, log


def redone(log, at, kinds):
    """True when every effect of the denied call `log[at]` happened anyway in a LATER call that completed (see call_log): `report.sh new`
    denied as part of an exploration line and then run on its own, a data.js write denied and then written again. Same effect, not same
    text. A denial that cannot be placed in the log (at is None) is never redone: nothing says what came after it."""
    if at is None:
        return False
    return kinds <= {e for c in log[at + 1:] if c.ok for e in effects(c.name, c.input)}


def unredone_denials(case):
    """[(turn, description)] of the denials (see denials) that no later successful call answered (see redone), and the number that was."""
    found, log = denials(case)
    open_ = [(n, label) for n, at, kinds, label in found if not redone(log, at, kinds)]
    return open_, len(found) - len(open_)


def blocked_turns(case):
    """The turns (1, 2) in which report.sh or a data.js write was denied and not redone by a later successful call, by a recorded denial
    or by a refusal read from a tool_result; turn 1 also when a Bash call was denied there and `report.sh new` was never tried (unexercised_denials)."""
    return {n for n, _ in unredone_denials(case)[0]} | ({1} if unexercised_denials(case) else set())


def not_permission_blocked(case):
    """A denial of `report.sh new` or `clean` or of a Write or Edit of a data.js that no later call redid successfully; denials of
    open/xdg-open/start are expected (they are not granted), and so is a denial of any other command that only names data.js
    (`node --check .../data.js`): the skill is not at fault.

    A denial is cleared when a LATER call of the same effect completed (a tool_result without is_error that does not read like a
    refusal): for report.sh, a Bash call that runs the same verb (`report.sh new`, any spelling of the path); for data.js, a Write or Edit
    of the same path. The known case: the agent chains `S=...; "$S/scripts/report.sh" new; cat ...; git log | head` into one call; a
    shell-variable call that no permission pattern can grant under the harness' permission mode is denied as a whole, and the agent then
    runs `"/abs/.../report.sh" new` on its own, which succeeds. A denial with no successful redo, or whose redo came back as an error,
    stays blocked (the trial is INCONCLUSIVE); so does one that cannot be placed in the trace.

    Also failed (the trial is INCONCLUSIVE) when turn 1 had a denied Bash call of any kind and never tried `report.sh new`: a compound
    exploration command denied for one ungranted part ends the agent's work before the skill's own step, so nothing was measured
    (a `new` that was tried, even denied, ends that rule; the denial itself is judged above).

    Read from result.permission_denials, from system permission_denied events and from tool_results of such calls that read like a
    refusal (neither channel is confirmed to carry the denial: README item 1; the dontAsk denial text and both events are seen in a
    real trace, Claude Code 2.1.330)."""
    all_denials = [d for turn in case.turns for d in hook_trace.permission_denials(turn)]
    blocked, cleared = unredone_denials(case)
    stopped = unexercised_denials(case)
    return "not-permission-blocked", not blocked and not stopped, \
        f"permission denials: {len(all_denials)}; of report.sh or a data.js write: {len(blocked) + cleared}, never redone by a later successful call: {len(blocked)} {[label for _, label in blocked] or 'none'}" \
        + (f"; redone by a later successful call (not blocking): {cleared}" if cleared else "") + "; " \
        + (f"turn 1 had denied Bash calls and never tried report.sh new, so the behaviour under test was not exercised: {[c[:80] or '(command unknown)' for _, c in stopped]}" if stopped
           else "no denied Bash call kept turn 1 from trying report.sh new")


def report_attempted(case):
    """True when turn 1 ran `report.sh new` (or the permission system refused it)."""
    return _ran_new(case) or any("script" in denial_kinds(d) for d in hook_trace.permission_denials(case.turns[0]))


def report_located(case):
    """The report directory was found. A precondition only when report.sh was tried: a skill that never ran step 2 has no report to
    locate, and `created-by-script` then decides (a finding about the skill, not about the environment). The same when the skill removed
    the report itself in turn 1 with `report.sh clean`: the report is gone because of the skill."""
    directory = case.report.get("dir")
    exists = bool(directory) and bool(case.report.get("exists"))
    attempted = report_attempted(case)
    cleaned = _cleaned_in_turn_one(case)
    ok = exists or not attempted or bool(cleaned)
    return "report-located", ok, f"report directory {directory} ({case.report.get('located_by', 'no record')}), exists={case.report.get('exists')}" \
        + ("" if attempted else "; report.sh new was never run, so there is nothing to locate (created-by-script decides)") \
        + ("; the skill ran report.sh clean in turn 1, which is why the report is gone (created-by-script decides)" if cleaned and not exists else "")


# ---- gate --------------------------------------------------------------------------------------------------

def report_outside_project(case):
    directory, ws = case.report.get("dir"), (case.states.get("layout") or {}).get("ws")
    if not directory or not ws:
        return "report-outside-project", False, "no report directory or workspace path recorded"
    inside = os.path.normpath(directory) == os.path.normpath(ws) or os.path.normpath(directory).startswith(os.path.normpath(ws) + os.sep)
    named = os.path.basename(directory).startswith("sitrep.")
    return "report-outside-project", not inside and named, f"report {directory}: inside the workspace {inside}, named sitrep.* {named}"


def project_untouched(case):
    """Each snapshot equals the one before. A missing snapshot after turn 2 (the second turn did not run) is not a write into the
    project: the snapshots that exist are compared and `run-completed` reports the missing turn."""
    states = case.states
    if not states.get("before") or not states.get("after1"):
        return "project-untouched", False, "the workspace snapshot before or after turn 1 is missing (not recorded: see state-recorded, which makes the trial INCONCLUSIVE)"
    chain = [states["before"], states["after1"]] + ([states["after2"]] if states.get("after2") else [])
    changes = [f"after turn {n}: {describe(diff)}" for n, diff in enumerate((diff_states(a, b) for a, b in zip(chain, chain[1:])), start=1) if any(diff)]
    missing = "; no snapshot after turn 2 (the second turn did not run: see run-completed)" if case.row.reply and not states.get("after2") else ""
    return "project-untouched", not changes, f"workspace: {'; '.join(changes) or 'no change after any turn'}{missing}"


def index_untouched(case):
    got, want = case.report.get("files", {}).get("index.html"), case.report.get("template_sha256")
    return "index-untouched", got is not None and got == want, "index.html " + ("equals template.html" if got is not None and got == want else "is missing or differs from template.html")


def only_data_added(case):
    names = sorted(case.report.get("files", {}))
    return "only-data-added", names == ["data.js", "index.html"], f"the report directory holds {names}"


def tracker_untouched(case):
    chain = workspace_chain(case)
    shas = [files(s).get(TRACKER, {}).get("sha256") for s in chain or []]
    ok = bool(shas) and shas[0] is not None and len(set(shas)) == 1
    return "tracker-untouched", ok, f"{TRACKER} is " + ("byte-identical after every turn" if ok else "missing, changed or not snapshotted")


def _negative_evidence(case):
    """(sitrep.* directories in the trial TMPDIR or None, report.sh calls, report-like files added to the workspace or None)."""
    calls = [c[:60] for _, c in hook_trace.bash_commands(case.events) if script_calls(c)]
    before, after = case.states.get("before"), case.states.get("after1")
    written = [p for p in diff_states(before, after)[0] if NEGATIVE_WRITES_RE.search(p)] if before and after else None
    return case.states.get("tmp_sitrep"), calls, written


def no_report_when_negative(case):
    """No report directory, no report.sh call (parsed argv: `cat report.sh` is none) and no report-like file (.html, data.js, a name with sitrep) added to the workspace."""
    listing, calls, written = _negative_evidence(case)
    ok = listing == [] and not calls and written == []
    return "no-report-when-negative", ok, f"sitrep.* directories in the trial TMPDIR: {listing}; report.sh calls: {calls or 'none'}; " \
        + (f"report-like files added to the workspace: {written or 'none'}" if written is not None else "the workspace snapshots are missing")


# ---- outcome -----------------------------------------------------------------------------------------------

def data_defines_sitrep(case):
    text = case.report.get("data_text")
    ok = text is not None and bool(sitrep_data.ASSIGN_RE.search(text))
    return "data-defines-sitrep", ok, "data.js " + ("assigns window.SITREP" if ok else "is missing or does not assign window.SITREP")


def data_parses(case):
    ok = case.data is not None
    return "data-parses", ok, "data.js parses into an object" if ok else f"data.js did not parse into an object: {case.report.get('parse_error') or 'the value is not an object'}"


def data_valid(case):
    if case.data is None:
        return "data-valid", False, "there is no parsed object to validate"
    problems = sitrep_data.validate(case.data)
    return "data-valid", not problems, "the data follows schema.md" if not problems else f"schema.md departures: {problem_list(problems)}"


def _item_text(item):
    return f"{item.get('title', '')} {item.get('detail', '')}".lower()


def _assign(candidates, keys):
    """The largest number of keys that can each get a distinct item: candidates is {item index: set of keys it matches}."""
    owner = {}

    def place(key, seen):
        for index, wanted in candidates.items():
            if key in wanted and index not in seen:
                seen.add(index)
                if index not in owner or place(owner[index], seen):
                    owner[index] = key
                    return True
        return False

    return sum(1 for key in keys if place(key, set()))


def items_cover_planted(case):
    """Five distinct items, one per planted item: an item that mentions the retention question in passing does not stand for it."""
    per_item = {n: {key for key, pattern in fixtures.PLANTED.items() if re.search(pattern, _item_text(i))} for n, i in enumerate(case.items)}
    matched = _assign(per_item, list(fixtures.PLANTED))
    uncovered = [key for key in fixtures.PLANTED if not any(key in keys for keys in per_item.values())]
    return "items-cover-planted", matched == len(fixtures.PLANTED), \
        f"{len(case.items)} items; planted items that got a distinct item of their own: {matched} of {len(fixtures.PLANTED)}; mentioned by no item: {uncovered or 'none'}"


MIGRATION_TITLE_RE = re.compile(r"migrat")
DB_TITLE_RE = re.compile(r"postgres|sqlite|database|\bdb\b")


def _item_kinds(item):
    """The roles an item can play, a subset of {"migration", "db"}: the title decides (both roles when it names both topics, as in
    'Postgres or SQLite (blocks the migration)'), the detail only when the title names neither and then only if it names one topic."""
    title, detail = str(item.get("title", "")).lower(), str(item.get("detail", "")).lower()
    kinds = ({"migration"} if MIGRATION_TITLE_RE.search(title) else set()) | ({"db"} if DB_TITLE_RE.search(title) else set())
    if kinds:
        return kinds
    in_migration, in_db = bool(re.search(fixtures.PLANTED["migration"], detail)), bool(re.search(fixtures.PLANTED["db"], detail))
    return {"migration"} if in_migration and not in_db else {"db"} if in_db and not in_migration else set()


def _deps(item):
    return set(d for d in item["dependsOn"] if isinstance(d, str)) if isinstance(item.get("dependsOn"), list) else set()


def dependency_recorded(case):
    """Structure only: some item lists, in dependsOn, a different item that exists (an id of another item). The skill is asked to record
    what blocks what (schema.md); the fixture plants one such dependency, and which item depends on which is `dependency-direction`'s
    (advisory) question, because it needs the item titles read."""
    byid = {i["id"]: i for i in case.items if isinstance(i.get("id"), str)}
    edges = [f"{i} -> {d}" for i, item in byid.items() for d in sorted(_deps(item)) if d != i and d in byid]
    return "dependency-recorded", bool(edges), f"dependsOn links between two different existing items: {edges or 'none'}"


def dependency_direction(case):
    """ADVISORY. A migration item lists a different database item in dependsOn, and the database item does not depend on it back. An
    item's roles come from its title first (see _item_kinds), so a dependency recorded backwards, or between two items that only mention
    each other's topic in their details, does not count; an item whose title names both topics may play either role. An item whose
    title names neither topic and whose detail names both is the database item when a migration item depends on it and the detail names a database."""
    byid = {i["id"]: i for i in case.items if isinstance(i.get("id"), str)}
    kinds = {i: _item_kinds(item) for i, item in byid.items()}
    targets = {d for i, item in byid.items() if "migration" in kinds[i] for d in _deps(item)}
    for i, item in byid.items():
        if not kinds[i] and i in targets and re.search(fixtures.PLANTED["db"], f"{item.get('title', '')} {item.get('detail', '')}".lower()):
            kinds[i] = {"db"}
    linked = [f"{m} -> {d}" for m, roles in kinds.items() if "migration" in roles for d in sorted(_deps(byid[m]))
              if d != m and "db" in kinds.get(d, ()) and m not in _deps(byid[d])]
    shown = {i: sorted(k) for i, k in kinds.items()}
    return "dependency-direction", bool(linked), f"item roles {shown}; migration item depending on a different database item (and not the reverse): {linked or 'none'}"


def start_here_set(case):
    nxt = (case.data or {}).get("next")
    title = nxt.get("title") if isinstance(nxt, dict) else None
    ok = isinstance(title, str) and bool(title.strip())
    return "start-here-set", ok, f"next.title: {title!r}" if ok else "the data has no `next` with a title (the start-here card)"


def milestone_item(case):
    item = next((i for i in case.items if i.get("id") == "ms-3"), None)
    got = {k: (item or {}).get(k) for k in ("type", "status", "urgency")}
    ok = item is not None and got == {"type": "blocker", "status": "blocked", "urgency": 3}
    return "milestone-item", ok, f"item ms-3: {got if item else 'missing'} (blocker, blocked, urgency 3 expected)"


def _strip(case):
    ms = (case.data or {}).get("milestones")
    return ms if isinstance(ms, dict) else {}


def milestone_strip(case):
    """The progress strip carries the tracker's numbers: done 1 of 4 (numbers only; the name of the current milestone is `milestone-current`)."""
    ms = _strip(case)
    ok = ms.get("done") == 1 and ms.get("total") == 4
    return "milestone-strip", ok, f"milestones: done {ms.get('done')}, total {ms.get('total')} (1 of 4 expected)"


def milestone_current(case):
    """ADVISORY. The strip's `current` text names the milestone that is doing (Storage layer): a word match on text the model wrote."""
    ms = _strip(case)
    ok = "storage layer" in str(ms.get("current", "")).lower()
    return "milestone-current", ok, f"milestones.current: {ms.get('current')!r} ('Storage layer' expected)"


def _mentions(unit, titles):
    lower = unit.lower()
    return any(re.search(pattern, lower) for pattern in fixtures.PLANTED.values()) or any(t in lower for t in titles)


def reply_hands_over(case):
    """ADVISORY. The reply names the report directory (its sitrep.XXXXXX name), mentions Copy JSON and does not paste the list: at most
    four list lines, at most MAX_REPLY_CHARS characters and at most MAX_ITEM_UNITS units (a line or a sentence; a period after vs.,
    e.g., i.e., etc. does not end one) that name a planted topic or an item title of data.js. A table row, a bold line or a sentence
    per item is a unit each, so pasting all five items is caught; the two or three lines SKILL.md step 3 asks for (counts, the start
    here item) are not. A list pasted into one run-on line passes: it is a word match on free text and cannot be made watertight."""
    reply, directory = case.reply1, case.report.get("dir")
    name = os.path.basename(directory) if directory else ""
    handed = bool(name) and bool(re.search(re.escape(name) + r"(?![A-Za-z0-9])", reply))
    copy_json, listed = bool(re.search(r"copy\s+json", reply, re.IGNORECASE)), len(LIST_LINE_RE.findall(reply))
    titles = [t for t in (str(i.get("title", "")).strip().lower() for i in case.items) if len(t) >= 4]
    protected = ABBREVIATION_RE.sub(lambda m: m.group(0).replace(".", "\x00"), reply)
    units = [u for u in UNIT_SPLIT_RE.split(protected) if u.strip()]
    naming = sum(1 for u in units if _mentions(u, titles))
    ok = handed and copy_json and listed <= MAX_LIST_LINES and naming <= MAX_ITEM_UNITS and len(reply) <= MAX_REPLY_CHARS
    return "reply-hands-over", ok, (f"names the report directory {name or '(none recorded)'}: {handed}; mentions Copy JSON: {copy_json}; list lines: {listed} (at most {MAX_LIST_LINES}); "
                                     f"lines or sentences naming a planted topic or item title: {naming} (at most {MAX_ITEM_UNITS}: the list is not pasted); length {len(reply)} (at most {MAX_REPLY_CHARS})")


def report_cleaned(case):
    after = case.states.get("report2")
    ok = bool(case.report.get("dir")) and after is not None and after.get("exists") is False
    return "report-cleaned", ok, f"the report directory {case.report.get('dir')} " + ("is gone after turn 2" if ok else "still exists after turn 2, or was never recorded")


# ---- which checks apply ------------------------------------------------------------------------------------

NONE = [run_completed, state_recorded, skill_loaded, skill_trigger, no_report_when_negative]
FEEDBACK = [run_completed, state_recorded, skill_loaded, skill_trigger, project_untouched]
REPORT = [run_completed, state_recorded, skill_loaded, skill_trigger, not_permission_blocked, report_located, created_by_script, report_outside_project, project_untouched,
          index_untouched, only_data_added, data_defines_sitrep, data_parses, data_valid, items_cover_planted, dependency_recorded, dependency_direction,
          start_here_set, reply_hands_over]

BY_EXPECT = {"none": NONE, "feedback": FEEDBACK, "report": REPORT, "report-clean": REPORT + [cleaned_by_script, report_cleaned],
             "report-ms": REPORT + [milestone_item, milestone_strip, milestone_current, tracker_untouched]}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`."""
    return [check(case) for check in BY_EXPECT[case.row.expect]]


def warnings(results):
    """The ids of the failed advisory (soft) checks of [(id, passed, note)]: listed by the report, never part of the verdict."""
    return [cid for cid, ok, _ in results if not ok and cid in SOFT]


def _scope(case, failed):
    """Which turns are damaged: broken1 (turn 1 did not finish, a report.sh call or data.js write of it was denied, a Bash call of it was
    denied before `report.sh new` was tried, the report it tried to make was not found, the plugin under test did not load, or the snapshots were not recorded) and broken2 (turn 2 did not finish or had a denial)."""
    problems, blocked = turn_problems(case), blocked_turns(case)
    return SimpleNamespace(
        problems=problems,
        broken1=bool(problems[1]) or 1 in blocked or bool(failed & {"report-located", "skill-loaded", "state-recorded"}),
        broken2=bool(problems.get(2)) or 2 in blocked)


def _explained(case, cid, failed, scope):
    """Can the failed check `cid` follow from a failed precondition (True) or does it stand on its own (False)?

    Standing on its own: something written into the project (proven only by snapshots that exist: with `before` or `after1` not recorded
    `project-untouched` is explained by `state-recorded`); an index.html that differs from the template (or is missing) in a report
    that report.sh made and the harness found; a skill that was called when it should not be, or not called when it should be (the
    last only when turn 1 finished); a report made in a negative case (a sitrep directory, a report.sh call, a report-like file; both
    of the last two only when the plugin loaded). Every other check reads what a turn left behind and is explained by that turn's
    damage: the checks of turn 1 by broken1, `cleaned-by-script` and `report-cleaned` by either turn."""
    loaded = "skill-loaded" not in failed
    if cid == "project-untouched":
        return bool(missing_snapshots(case))
    if cid == "index-untouched":
        return not (case.report.get("dir") and case.report.get("exists") and _ran_new(case))
    if cid == "skill-trigger":
        called = any(trace.skill_matches(s, SKILL) for s in trace.skills_invoked(case.events))
        return not (loaded and ((called and not case.row.should_trigger) or (not called and case.row.should_trigger and not scope.problems[1])))
    if cid == "no-report-when-negative":
        listing, calls, written = _negative_evidence(case)
        return not (loaded and (listing or calls or written))
    if cid in TURN_TWO:
        return scope.broken1 or scope.broken2
    return scope.broken1


def verdict(case, results):
    """PASS, FAIL or INCONCLUSIVE for a case and its [(id, passed, note)]; the failed checks are still listed by the report.

    The advisory (soft) checks do not take part: a trial whose only failures are soft is a PASS, and the report lists them as warnings.

    A failed precondition (the run did not finish, the snapshots were not recorded, a tried report.sh left no report, a permission denial), or a plugin that did not load
    or is not the one under test (`skill-loaded`), means the behaviour was never exercised: INCONCLUSIVE, but only for the failures that
    can follow from it. The damage is scoped by turn (see _scope): a failure that the damaged turn does not explain stands, and then the
    trial is a FAIL, e.g. a data.js without dependencies after a turn 1 that finished is a finding although turn 2 crashed. Failures that
    stand on their own whatever happened are listed in _explained."""
    failed = {cid for cid, ok, _ in results if not ok and cid not in SOFT}
    if not failed:
        return "PASS"
    environment = failed & PRECONDITIONS | failed & {"skill-loaded"}
    if not environment:
        return "FAIL"
    scope = _scope(case, failed)
    return "INCONCLUSIVE" if all(_explained(case, cid, failed, scope) for cid in failed - environment) else "FAIL"
