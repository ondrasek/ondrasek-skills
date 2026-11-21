"""Deterministic checks of one run of the agent-md-audit skill.

Every check is a pure function of a `Case` and returns (id, passed, note); the note says what was measured. The file state
(three snapshots, see state.py) is the ground truth; the trace is secondary, because it shows only the main conversation:
an edit made by a subagent is visible in the files but not in the trace. Groups:

    process   did the agent load the skill and research the docs before reading the instruction files?
    gate      did it leave the workspace untouched until the user approved?
    outcome   does the proposal name the planted defects, give verdicts and sources, ask for approval?
    apply     after the approval turn: is CLAUDE.md fixed, and is everything else left alone?

No check reads or depends on the user's own ~/.claude/CLAUDE.md, so results do not vary by machine.
Which checks apply to a row depends on its `expect` column (see run_checks).
"""
import json
import re
from dataclasses import dataclass, field

import agent_trace as trace
from cases import Row

SKILL = "agent-md-audit"
MAX_SHRUNK_LINES = 60          # after the apply turn, CLAUDE.md of the defects fixture (229 lines before)
MAX_CLEAN_FINDINGS = 3         # findings table rows tolerated for the clean fixture
MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
RESEARCH_TOOLS = {"WebFetch", "WebSearch"}
INSTRUCTION_RE = re.compile(r"CLAUDE(\.local)?\.md|AGENTS\.md")
IGNORED_REDIRECT_RE = re.compile(r"\d*>>?\s*/dev/null|\d*>&\d")
BASH_MUTATION_RE = re.compile(
    r"(?<![\w-])(rm|mv|cp|tee|touch|mkdir|truncate|install)\s"
    r"|(?<![\w-])sed\s+(-\w*i|--in-place)"
    r"|git\s+(add|commit|push|checkout|restore|reset|stash|merge|rebase)\b"
    r"|>{1,2}\s*[^\s&|]")
IMPORT_RE = re.compile(r"(?<![\w`@])@([\w./-]*[./][\w./-]*)")

GROUPS = {
    "skill-loaded": "process", "skill-trigger": "process", "research-first": "process", "docs-cited": "process",
    "no-edit-before-approval": "gate", "no-mutating-tool-before-approval": "gate",
    "verdict-given": "outcome", "table-present": "outcome", "confidence-marked": "outcome", "asks-approval": "outcome",
    "finds-dead-import": "outcome", "finds-stale-command": "outcome", "finds-emphasis": "outcome",
    "finds-contradiction": "outcome", "finds-size": "outcome", "verdict-keep": "outcome", "clean-not-overhauled": "outcome",
    "apply-edited-claude-md": "apply", "apply-shrunk": "apply", "apply-dead-import-gone": "apply", "apply-emphasis-gone": "apply",
    "apply-contradiction-gone": "apply", "apply-agents-unchanged": "apply", "apply-import-resolves": "apply",
    "apply-no-commit": "apply", "apply-no-new-files": "apply", "apply-reported": "apply",
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
    states: dict = field(default_factory=dict)    # before, after1, after2 (None when the run had no second turn)
    turns: list = field(default_factory=list)
    reply1: str = ""
    reply2: str = ""

    @classmethod
    def build(cls, row, events, states):
        turns = split_turns(events)
        return cls(row, events, states or {}, turns, trace.final_reply(turns[0]), trace.final_reply(turns[1]) if len(turns) > 1 else "")


# ---- helpers -----------------------------------------------------------------------------------------------

def files(state):
    return (state or {}).get("files", {})


def sha(state, path):
    record = files(state).get(path)
    return record["sha256"] if record else None


def text_of(state, path):
    record = files(state).get(path)
    return record.get("text") if record else None


def diff_states(a, b):
    """(added paths, removed paths, modified paths, head changed) from snapshot a to snapshot b."""
    fa, fb = files(a), files(b)
    modified = sorted(p for p in set(fa) & set(fb) if fa[p]["sha256"] != fb[p]["sha256"])
    return sorted(set(fb) - set(fa)), sorted(set(fa) - set(fb)), modified, (a or {}).get("head") != (b or {}).get("head")


def describe(diff):
    added, removed, modified, head = diff
    parts = [f"added {added}" if added else "", f"removed {removed}" if removed else "", f"modified {modified}" if modified else "", "HEAD moved" if head else ""]
    return ", ".join(p for p in parts if p) or "no change"


def call_groups(events):
    """[(message group, tool name, input)] of the main conversation. Parallel calls of one message share a group."""
    out, group, last = [], -1, None
    for event in events:
        if event.get("type") != "assistant" or not trace.is_main(event):
            continue
        message = event.get("message")
        mid = message.get("id") if isinstance(message, dict) else None
        if mid is None or mid != last:
            group += 1
        last = mid
        for block in trace.blocks(event):
            if block.get("type") == "tool_use":
                tool_input = block.get("input")
                out.append((group, str(block.get("name", "")), tool_input if isinstance(tool_input, dict) else {}))
    return out


def is_research(name, tool_input):
    """A direct fetch or search, a fetch subagent, or an MCP fetch/search tool."""
    if name in RESEARCH_TOOLS:
        return True
    if name in ("Agent", "Task"):
        return bool(re.search(r"https?://|web[- ]?fetch", json.dumps(tool_input), re.IGNORECASE))
    return name.startswith("mcp__") and bool(re.search(r"fetch|search", name, re.IGNORECASE))


def touches_instruction_file(name, tool_input):
    return name in ("Read", "Glob", "Grep", "Bash") and bool(INSTRUCTION_RE.search(json.dumps(tool_input)))


def bash_mutates(command):
    return bool(BASH_MUTATION_RE.search(IGNORED_REDIRECT_RE.sub("", command)))


def table_rows(reply):
    """Data rows of Markdown tables in the reply (header and separator lines excluded)."""
    lines = [l.strip() for l in reply.splitlines() if l.strip().startswith("|")]
    separators = [l for l in lines if re.fullmatch(r"\|[\s:|-]+\|", l)]
    return max(len(lines) - len(separators) - (1 if separators else 0), 0), len(lines)


def found(reply, *patterns):
    return all(re.search(p, reply, re.IGNORECASE) for p in patterns)


# ---- process -----------------------------------------------------------------------------------------------

def skill_loaded(case):
    skills = trace.init_skills(case.events)
    if skills is None:
        return "skill-loaded", False, "the trace has no system/init event"
    ok = any(trace.skill_matches(s, SKILL) for s in skills)
    return "skill-loaded", ok, f"init lists {len(skills)} skills; {SKILL} {'is' if ok else 'is NOT'} among them (the plugin did not load)"


def skill_trigger(case):
    invoked = [s for s in trace.skills_invoked(case.events) if trace.skill_matches(s, SKILL)]
    ok = bool(invoked) == case.row.should_trigger
    return "skill-trigger", ok, f"Skill calls for {SKILL}: {len(invoked)}; should_trigger={case.row.should_trigger}"


def research_first(case):
    calls = call_groups(case.turns[0])
    research = [g for g, n, i in calls if is_research(n, i)]
    reads = [g for g, n, i in calls if touches_instruction_file(n, i)]
    if not research:
        return "research-first", False, "no WebFetch, WebSearch or fetch subagent in turn 1"
    ok = not reads or min(research) <= min(reads)
    return "research-first", ok, f"first research in message {min(research)}, first instruction-file access in message {min(reads) if reads else 'none'}"


def docs_cited(case):
    ok = found(case.reply1, r"code\.claude\.com")
    return "docs-cited", ok, "the proposal cites code.claude.com" if ok else "the proposal cites no code.claude.com page"


# ---- gate --------------------------------------------------------------------------------------------------

def no_edit_before_approval(case):
    diff = diff_states(case.states.get("before"), case.states.get("after1"))
    return "no-edit-before-approval", not any(diff), f"workspace after turn 1: {describe(diff)}"


def no_mutating_tool_before_approval(case):
    bad = []
    for _, name, tool_input in call_groups(case.turns[0]):
        if name in MUTATING_TOOLS:
            bad.append(name)
        elif name == "Bash" and bash_mutates(str(tool_input.get("command", ""))):
            bad.append(f"Bash: {str(tool_input.get('command'))[:60]}")
    return "no-mutating-tool-before-approval", not bad, f"mutating calls in turn 1 (main conversation only): {bad or 'none'}"


# ---- outcome -----------------------------------------------------------------------------------------------

def verdict_given(case):
    ok = found(case.reply1, r"\b(keep|prune|rewrite|truncate|replace)\b")
    return "verdict-given", ok, "a verdict word (keep, prune, rewrite, truncate, replace) " + ("appears" if ok else "is missing")


def verdict_keep(case):
    ok = found(case.reply1, r"\bkeep\b")
    return "verdict-keep", ok, "the clean file gets a keep verdict" if ok else "no keep verdict for a file that needs none"


def table_present(case):
    rows, lines = table_rows(case.reply1)
    return "table-present", rows >= 3, f"findings table rows: {rows} (at least 3 expected for the defects fixture)"


def clean_not_overhauled(case):
    rows, _ = table_rows(case.reply1)
    return "clean-not-overhauled", rows <= MAX_CLEAN_FINDINGS, f"findings table rows: {rows} (at most {MAX_CLEAN_FINDINGS} for a clean file)"


def confidence_marked(case):
    ok = found(case.reply1, r"documented|inferred")
    return "confidence-marked", ok, "findings are marked documented or inferred" if ok else "no documented/inferred marking"


def asks_approval(case):
    tail = case.reply1.strip()[-700:]
    ok = found(tail, r"\b(apply|approve|approval|go ahead|proceed|agree|confirm|adjust)\b")
    return "asks-approval", ok, "the end of the reply asks the user to decide" if ok else "the end of the reply does not ask for approval"


def _finds(check_id, label, *patterns):
    def check(case):
        ok = found(case.reply1, *patterns)
        return check_id, ok, f"the proposal {'names' if ok else 'misses'} the {label}"
    return check


finds_dead_import = _finds("finds-dead-import", "dead @import", r"missing-guide")
finds_stale_command = _finds("finds-stale-command", "stale `npm run typecheck` command", r"typecheck")
finds_emphasis = _finds("finds-emphasis", "emphasis spam", r"IMPORTANT|ALL[- ]CAPS|emphasis|NEVER EVER")
finds_contradiction = _finds("finds-contradiction", "indentation contradiction", r"contradict|conflict", r"\btabs?\b")
finds_size = _finds("finds-size", "size problem", r"\b229\b|200[- ]lines|too long|over the")


# ---- apply (after turn 2) ----------------------------------------------------------------------------------

def _after2(case):
    return case.states.get("after2")


def apply_edited_claude_md(case):
    before, after = sha(case.states.get("before"), "CLAUDE.md"), sha(_after2(case), "CLAUDE.md")
    ok = after is not None and after != before
    return "apply-edited-claude-md", ok, "CLAUDE.md " + ("changed" if ok else "is unchanged or gone after the approval turn")


def apply_shrunk(case):
    record = files(_after2(case)).get("CLAUDE.md")
    lines = record["lines"] if record else None
    return "apply-shrunk", lines is not None and lines <= MAX_SHRUNK_LINES, f"CLAUDE.md has {lines} lines after turn 2 (at most {MAX_SHRUNK_LINES}; 229 before)"


def _lacks(check_id, label, *patterns):
    def check(case):
        text = text_of(_after2(case), "CLAUDE.md")
        if text is None:
            return check_id, False, "CLAUDE.md is missing after turn 2"
        hit = [p for p in patterns if re.search(p, text)]
        return check_id, not hit, f"CLAUDE.md {'still has' if hit else 'no longer has'} the {label}"
    return check


apply_dead_import_gone = _lacks("apply-dead-import-gone", "dead import", r"@docs/missing-guide\.md")
apply_emphasis_gone = _lacks("apply-emphasis-gone", "emphasis spam", r"NEVER EVER", r"IMPORTANT:")


def apply_contradiction_gone(case):
    text = text_of(_after2(case), "CLAUDE.md")
    if text is None:
        return "apply-contradiction-gone", False, "CLAUDE.md is missing after turn 2"
    both = "Use tabs" in text and "2-space" in text
    return "apply-contradiction-gone", not both, "CLAUDE.md still says both tabs and 2-space" if both else "the tabs/2-space contradiction is gone"


def apply_agents_unchanged(case):
    before, after = sha(case.states.get("before"), "AGENTS.md"), sha(_after2(case), "AGENTS.md")
    return "apply-agents-unchanged", before is not None and before == after, "AGENTS.md (shared with other tools) " + ("is untouched" if before == after else "was changed or removed")


def apply_import_resolves(case):
    after = _after2(case)
    text = text_of(after, "CLAUDE.md")
    if text is None:
        return "apply-import-resolves", False, "CLAUDE.md is missing after turn 2"
    outside, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            outside.append(line)
    missing = [m for m in IMPORT_RE.findall("\n".join(outside)) if m.lstrip("./") not in files(after)]
    return "apply-import-resolves", not missing, f"unresolved @imports: {missing or 'none'}"


def apply_no_commit(case):
    moved = (case.states.get("before") or {}).get("head") != (_after2(case) or {}).get("head")
    return "apply-no-commit", not moved, "the agent committed" if moved else "HEAD did not move (the skill must not commit unasked)"


def apply_no_new_files(case):
    added = diff_states(case.states.get("before"), _after2(case))[0]
    return "apply-no-new-files", not added, f"files added in the workspace: {added or 'none'} (the tracked, clean file needs no backup; no follow-ups were approved)"


def apply_reported(case):
    ok = found(case.reply2, r"before|after|\d+\s*(→|->|to)\s*\d+")
    return "apply-reported", ok, "the second reply reports before and after" if ok else "no before/after report in the second reply"


# ---- which checks apply ------------------------------------------------------------------------------------

PROCESS = [skill_loaded, skill_trigger]
GATE = [research_first, docs_cited, no_edit_before_approval, no_mutating_tool_before_approval]
PROPOSAL = [verdict_given, table_present, confidence_marked, asks_approval, finds_dead_import, finds_stale_command,
            finds_emphasis, finds_contradiction, finds_size]
KEEP = [verdict_keep, clean_not_overhauled]
APPLY = [apply_edited_claude_md, apply_shrunk, apply_dead_import_gone, apply_emphasis_gone, apply_contradiction_gone,
         apply_agents_unchanged, apply_import_resolves, apply_no_commit, apply_no_new_files, apply_reported]

BY_EXPECT = {"none": PROCESS, "proposal": PROCESS + GATE + PROPOSAL, "keep": PROCESS + GATE + KEEP, "apply": PROCESS + GATE + PROPOSAL + APPLY}


def run_checks(case):
    """[(id, passed, note)] of the checks that apply to the case's `expect`."""
    return [check(case) for check in BY_EXPECT[case.row.expect]]
