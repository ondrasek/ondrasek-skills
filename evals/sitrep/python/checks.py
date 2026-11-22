"""Deterministic checks of one run of the sitrep skill.

Every check is a pure function of a `Case` and returns (id, passed, note); the note says what was measured. The recorded state
(state.py: workspace snapshots, the report directory, parsed data.js) is the ground truth; the trace is secondary, because it
shows only the main conversation. Groups:

    precondition  was the behaviour exercised at all? A failure makes the trial INCONCLUSIVE, not FAIL (see `verdict`): the run did
                  not finish (`run-completed`), a report.sh that was tried left no report directory, or a permission denial
                  blocked report.sh or data.js, which says nothing about the skill (a plugin that did not load, or not the one under
                  test, `skill-loaded`, is treated the same way)
    process       did the skill load and trigger, and was the report made and removed by report.sh, not by hand?
    gate          nothing was written into the project, the template and the tracker are untouched, the report holds only data.js
    outcome       data.js defines a valid window.SITREP that holds the planted items, the dependency, the milestones; the reply
                  hands the report over without pasting the list; the report is gone after the second turn

No check reads or depends on the user's own ~/.claude/CLAUDE.md, so results do not vary by machine.
Which checks apply to a row depends on its `expect` column (see BY_EXPECT).
"""
import json
import os
import re
from dataclasses import dataclass, field

import agent_trace as trace
import fixtures
import hook_trace
import sitrep_data
import state
from cases import Row

SKILL = "sitrep"
TRACKER = ".cache/milestones.json"
MKDIR_RE = re.compile(r"(?<![\w-])(mkdir|mktemp)\s")
# rm, rmdir, unlink (also as /bin/rm, or after `||`, `;`, `&&`), find ... -delete and rmtree; the lookbehind rejects only names that merely end in rm
RM_RE = re.compile(r"(?<![\w.-])(?:rm|rmdir|unlink)\s|\bfind\s[^|;&\n]*-delete\b|\brmtree\b")
BLOCKED_RE = re.compile(r"report\.sh|data\.js")
# how a refusal might read in a tool_result (the wording is unconfirmed: README item 1)
REFUSED_RE = re.compile(r"permission|not allowed|denied|not granted|disallowed|requires approval|not permitted", re.IGNORECASE)
LIST_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
MAX_LIST_LINES = 4
MAX_ITEM_MENTIONS = 2     # the reply may name two items in passing; three or more is the list pasted into chat
NEGATIVE_WRITES_RE = re.compile(r"\.html?$|(^|/)data\.js$|sitrep", re.IGNORECASE)

GROUPS = {
    "skill-loaded": "process", "skill-trigger": "process", "created-by-script": "process", "cleaned-by-script": "process",
    "run-completed": "precondition", "not-permission-blocked": "precondition", "report-located": "precondition",
    "report-outside-project": "gate", "project-untouched": "gate", "index-untouched": "gate", "only-data-added": "gate",
    "tracker-untouched": "gate", "no-report-when-negative": "gate",
    "data-defines-sitrep": "outcome", "data-parses": "outcome", "data-valid": "outcome", "items-cover-planted": "outcome",
    "dependency-recorded": "outcome", "start-here-set": "outcome", "milestone-item": "outcome", "milestone-strip": "outcome",
    "reply-hands-over": "outcome", "report-cleaned": "outcome",
}


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


def problem_list(problems):
    return "; ".join(problems[:3]) + (f"; and {len(problems) - 3} more" if len(problems) > 3 else "")


# ---- process -----------------------------------------------------------------------------------------------

def _same_path(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def skill_loaded(case):
    """The plugin under test loaded and nothing else answers to its name: exactly one plugin named `sitrep` whose path is the
    --plugin-dir of the run (recorded as states["plugin"]; a recording without it skips the path test) and, when the trace ran
    report.sh, only a report.sh under that directory. An installed copy of the plugin would otherwise be graded as the working copy."""
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
        elsewhere = [c[:70] for _, c in hook_trace.bash_commands(case.events)
                     if (state.REPORT_NEW_RE.search(c) or state.REPORT_CLEAN_RE.search(c))
                     and plugin.rstrip("/") + "/" not in c and os.path.realpath(plugin).rstrip("/") + "/" not in c]
        if elsewhere:
            problems.append(f"report.sh ran from outside {plugin!r}: {elsewhere}")
    note = f"plugins {names}, {len(skills)} skills listed; " + ("; ".join(problems) or f"{SKILL} loaded from the plugin under test" + ("" if plugin else " (no --plugin-dir recorded: path not compared)"))
    return "skill-loaded", not problems, note + (f"; plugin errors: {errors}" if errors else "")


def skill_trigger(case):
    invoked = [s for s in trace.skills_invoked(case.events) if trace.skill_matches(s, SKILL)]
    ok = bool(invoked) == case.row.should_trigger
    return "skill-trigger", ok, f"Skill calls for {SKILL}: {len(invoked)}; should_trigger={case.row.should_trigger}"


def created_by_script(case):
    commands = [c for _, c in hook_trace.bash_commands(case.turns[0])]
    ran = any(state.REPORT_NEW_RE.search(c) for c in commands)
    by_hand = [c[:60] for c in commands if MKDIR_RE.search(c)]
    return "created-by-script", ran and not by_hand, f"`report.sh new` {'ran' if ran else 'did NOT run'} in turn 1; mkdir/mktemp calls: {by_hand or 'none'}"


def cleaned_by_script(case):
    directory = case.report.get("dir")
    if not directory or len(case.turns) < 2:
        return "cleaned-by-script", False, "no report directory was recorded or the run has no second turn"
    name = os.path.basename(directory)
    commands = [c for _, c in hook_trace.bash_commands(case.turns[1]) if name in c]
    cleaned = [c for c in commands if state.REPORT_CLEAN_RE.search(c)]
    removed = [c[:60] for c in commands if RM_RE.search(c)]
    return "cleaned-by-script", bool(cleaned) and not removed, f"`report.sh clean` calls naming {name} in turn 2: {len(cleaned)}; rm calls naming it: {removed or 'none'}"


# ---- precondition ------------------------------------------------------------------------------------------

def run_completed(case):
    """Every turn the case needs ended in a success result event, the runner noted nothing (exit code, timeout, session id) and the
    snapshot after the last turn exists. Without this a crashed or aborted run passes every check that only looks for something absent."""
    expected = 2 if case.row.reply else 1
    results = [e for e in case.events if e.get("type") == "result"]
    bad = [f"{e.get('subtype')}{' (is_error)' if e.get('is_error') else ''}" for e in results if e.get("subtype") != "success" or e.get("is_error")]
    problems = ([f"runner note: {case.run_note}"] if case.run_note else []) + ([f"{len(results)} result events, {expected} expected"] if len(results) != expected else []) \
        + ([f"result events that are not a success: {bad}"] if bad else []) \
        + (["no workspace snapshot after turn 2"] if expected == 2 and not case.states.get("after2") else [])
    return "run-completed", not problems, "; ".join(problems) or f"{expected} turn(s), each ended in a success result"


def _refusals(case):
    """Calls that touch report.sh or data.js (Bash, Write, Edit) whose result reads like a refusal: a denial that arrives as tool_result text."""
    results = {}
    for turn in case.turns:
        results.update(hook_trace.tool_results(turn))
    return [f"{name}: {results[call_id].strip()[:70]!r}" for call_id, name, tool_input in hook_trace.tool_uses(case.events)
            if name in ("Bash", "Write", "Edit") and BLOCKED_RE.search(json.dumps(tool_input)) and REFUSED_RE.search(results.get(call_id, "")[:300])]


def not_permission_blocked(case):
    """A denial of report.sh or of a write of data.js; denials of open/xdg-open/start are expected (they are not granted).

    Read from result.permission_denials, from system permission_denied events and from tool_results of Bash, Write and Edit calls that
    name report.sh or data.js and read like a refusal (neither channel is confirmed to carry the denial: README item 1)."""
    denials = [d for turn in case.turns for d in hook_trace.permission_denials(turn)]
    blocked = [d for d in denials if d.get("tool_name", "") in ("Bash", "Write", "Edit", "") and BLOCKED_RE.search(json.dumps(d))]
    refused = _refusals(case)
    return "not-permission-blocked", not blocked and not refused, \
        f"permission denials: {len(denials)}; of report.sh or data.js: {len(blocked)} {[json.dumps(d)[:80] for d in blocked]}; tool results that read as a refusal: {refused or 'none'}"


def report_attempted(case):
    """True when turn 1 ran `report.sh new` (or the permission system refused it)."""
    commands = [c for _, c in hook_trace.bash_commands(case.turns[0])]
    return any(state.REPORT_NEW_RE.search(c) for c in commands) or any(
        "report.sh" in json.dumps(d) for d in hook_trace.permission_denials(case.turns[0]))


def report_located(case):
    """The report directory was found. A precondition only when report.sh was tried: a skill that never ran step 2 has no report to
    locate, and `created-by-script` then decides (a finding about the skill, not about the environment)."""
    directory = case.report.get("dir")
    exists = bool(directory) and bool(case.report.get("exists"))
    attempted = report_attempted(case)
    ok = exists or not attempted
    return "report-located", ok, f"report directory {directory} ({case.report.get('located_by', 'no record')}), exists={case.report.get('exists')}" \
        + ("" if attempted else "; report.sh new was never run, so there is nothing to locate (created-by-script decides)")


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
        return "project-untouched", False, "the workspace snapshot before or after turn 1 is missing"
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


def no_report_when_negative(case):
    """No report directory, no report.sh call and no report-like file (.html, data.js, a name with sitrep) added to the workspace."""
    listing = case.states.get("tmp_sitrep")
    calls = [c[:60] for _, c in hook_trace.bash_commands(case.events) if "report.sh" in c]
    before, after = case.states.get("before"), case.states.get("after1")
    written = [p for p in diff_states(before, after)[0] if NEGATIVE_WRITES_RE.search(p)] if before and after else None
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


def _item_kind(item):
    """'migration' or 'db' (or '' when unclear): the title decides, the detail only when the title names neither."""
    title, detail = str(item.get("title", "")).lower(), str(item.get("detail", "")).lower()
    if MIGRATION_TITLE_RE.search(title):
        return "migration"
    if DB_TITLE_RE.search(title):
        return "db"
    in_migration, in_db = bool(re.search(fixtures.PLANTED["migration"], detail)), bool(re.search(fixtures.PLANTED["db"], detail))
    return "migration" if in_migration and not in_db else "db" if in_db and not in_migration else ""


def dependency_recorded(case):
    """A migration item lists a database item in dependsOn, and the database item does not depend on it back. Each item is classified once
    (by its title, see _item_kind), so a reversed dependency, or an item that mentions both topics, does not count."""
    kinds = {i["id"]: _item_kind(i) for i in case.items if isinstance(i.get("id"), str)}
    byid = {i["id"]: i for i in case.items if isinstance(i.get("id"), str)}

    def deps(item):
        return set(item["dependsOn"]) if isinstance(item.get("dependsOn"), list) else set()

    linked = [f"{m} -> {d}" for m, kind in kinds.items() if kind == "migration" for d in sorted(deps(byid[m])) if kinds.get(d) == "db" and m not in deps(byid[d])]
    return "dependency-recorded", bool(linked), f"item kinds {kinds}; migration item depending on a database item (and not the reverse): {linked or 'none'}"


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


def milestone_strip(case):
    ms = (case.data or {}).get("milestones")
    ms = ms if isinstance(ms, dict) else {}
    ok = ms.get("done") == 1 and ms.get("total") == 4 and "storage layer" in str(ms.get("current", "")).lower()
    return "milestone-strip", ok, f"milestones: done {ms.get('done')}, total {ms.get('total')}, current {ms.get('current')!r} (1, 4, 'Storage layer' expected)"


def reply_hands_over(case):
    """The reply names the report directory (its sitrep.XXXXXX name), mentions Copy JSON and does not paste the list: at most four list
    lines and at most two of the planted topics or item titles of data.js named (a table or bold lines pasting every item is caught)."""
    reply, directory = case.reply1, case.report.get("dir")
    name = os.path.basename(directory) if directory else ""
    handed = bool(name) and bool(re.search(re.escape(name) + r"(?![A-Za-z0-9])", reply))
    copy_json, listed = bool(re.search(r"copy\s+json", reply, re.IGNORECASE)), len(LIST_LINE_RE.findall(reply))
    lower = reply.lower()
    topics = [key for key, pattern in fixtures.PLANTED.items() if re.search(pattern, lower)]
    titles = [t for t in (str(i.get("title", "")).strip() for i in case.items) if len(t) >= 4 and t.lower() in lower]
    mentions = max(len(topics), len(titles))
    ok = handed and copy_json and listed <= MAX_LIST_LINES and mentions <= MAX_ITEM_MENTIONS
    return "reply-hands-over", ok, (f"names the report directory {name or '(none recorded)'}: {handed}; mentions Copy JSON: {copy_json}; list lines: {listed} (at most {MAX_LIST_LINES}); "
                                     f"planted topics named {topics}, item titles named {len(titles)} (at most {MAX_ITEM_MENTIONS}: the list is not pasted)")


def report_cleaned(case):
    after = case.states.get("report2")
    ok = bool(case.report.get("dir")) and after is not None and after.get("exists") is False
    return "report-cleaned", ok, f"the report directory {case.report.get('dir')} " + ("is gone after turn 2" if ok else "still exists after turn 2, or was never recorded")


# ---- which checks apply ------------------------------------------------------------------------------------

NONE = [run_completed, skill_loaded, skill_trigger, no_report_when_negative]
FEEDBACK = [run_completed, skill_loaded, skill_trigger, project_untouched]
REPORT = [run_completed, skill_loaded, skill_trigger, not_permission_blocked, report_located, created_by_script, report_outside_project, project_untouched,
          index_untouched, only_data_added, data_defines_sitrep, data_parses, data_valid, items_cover_planted, dependency_recorded,
          start_here_set, reply_hands_over]

BY_EXPECT = {"none": NONE, "feedback": FEEDBACK, "report": REPORT, "report-clean": REPORT + [cleaned_by_script, report_cleaned],
             "report-ms": REPORT + [milestone_item, milestone_strip, tracker_untouched]}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`."""
    return [check(case) for check in BY_EXPECT[case.row.expect]]


def verdict(results):
    """PASS, FAIL or INCONCLUSIVE for [(id, passed, note)]; the failed checks are still listed by the report.

    A failed precondition (the run did not finish, a tried report.sh left no report, a permission denial), or a plugin that did not load
    or is not the one under test (`skill-loaded`), means the behaviour was never exercised: INCONCLUSIVE, because the failed gate and
    outcome checks of such a trial may only follow from it. Some failures stand on their own and make the trial FAIL anyway:
    something written into the project (it does not depend on a report existing), an `index.html` that differs from the template in a
    report that report.sh made and the harness found (the agent edited it), and a skill that was not called when it should have been (a finding about the
    description, but only when the plugin did load and the run finished: a crashed run says nothing about triggering).
    """
    failed = {cid for cid, ok, _ in results if not ok}
    if not failed:
        return "PASS"
    environment = {cid for cid in failed if GROUPS[cid] == "precondition"} | failed & {"skill-loaded"}
    findings = {"project-untouched"}
    if not failed & {"skill-loaded", "run-completed"}:
        findings.add("skill-trigger")
    if not failed & {"report-located", "created-by-script"}:      # report.sh ran and its directory exists: a template that differs was edited
        findings.add("index-untouched")
    return "INCONCLUSIVE" if environment and not failed & findings else "FAIL"
