"""Deterministic checks of one run of the oaat skill: on the trace (what the agent did) and on the reply (what it said).

Every check is a pure function of a `Case` and returns (id, passed, note). The note says what was measured, so a failure
explains itself. The checks fall into the four groups of the OpenAI post (https://developers.openai.com/blog/eval-skills):

    outcome     did the reply do what the skill promises?
    process     did the agent load and call the skill (a tool call, not a claim in prose)?
    style       does the reply keep the skill's fixed shape?
    efficiency  did the agent stay short and avoid detours?

Which checks apply to a prompt depends on its `expect` column (see run_checks).

A second path exists: when the turn presents an item with the `AskUserQuestion` tool and the user answers (expect item or skip), the
item is in the tool input and not in the reply text, so the text-template style checks give way to the tool checks (see
`Case.tool_path` and `run_checks`). An interactive walk-through is ONE turn with several sequential calls, so the tool checks grade
every call on its own. A turn without an answered call (no call, or a denied or failed one) is graded exactly as before, on the
text. The tool path can only be exercised on invented traces: a headless `claude -p` run has no such tool (README, "The tool path").
"""
import re
from dataclasses import dataclass, field, replace

import agent_trace as trace
import ask_tool
from cases import HEADER_RE, Row, pending_titles, previous_header_number, previous_header_title

MAX_REPLY_LINES = 12     # non-empty lines; the skill asks for about 8 lines for one item
MAX_REPLY_WORDS = 180
MAX_OPTIONS = 3
MAX_OTHER_TOOL_CALLS = 2  # tool calls besides Skill
MAX_TURNS = 4             # Skill call, reply, and slack for one retry
MAX_TOOL_HEADER = 12      # characters of the AskUserQuestion `header`
MIN_TOOL_OPTIONS, MAX_TOOL_OPTIONS = 2, 4   # the item's 1 to 3 choices plus `Skip for now`
RECOMMENDED_SUFFIX = "(Recommended)"
SKIP_LABEL = "Skip for now"
MAX_COUNT_LINE_WORDS = 30  # the one-line count before the first item ("5 items need you. First one:"); the same limit as the plain line
MAX_PLAIN_LINE_WORDS = 30  # the ONE short plain line (confirmation, explanation, correction) between two calls

LABELS = ("what it is", "why it matters", "options", "one question")
WRAPUP_LABELS = ("decided", "parked", "next action")
QUESTION_LABELS = ("one question", "question")
PREVIEW_PHRASES = ("next item", "up next", "next up", "coming up", "after this one", "after that one", "then we will", "then we'll", "later we")
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[A-Ca-c][.)])\s+")
NUMBER_RE = re.compile(r"^\s*(\d+)[.)]\s+")

GROUPS = {
    "skill-loaded": "process", "skill-trigger": "process",
    "completed": "outcome", "header-number": "outcome", "header-total": "outcome", "no-other-items": "outcome",
    "no-preview": "outcome", "wrapup-headings": "outcome", "tangent-flag": "outcome", "all-items-listed": "outcome",
    "no-oaat-header": "outcome", "tool-not-allowed": "outcome", "tool-early-end": "outcome",
    "one-header": "style", "sections": "style", "options-max-3": "style", "options-numbered": "style", "recommended-marked": "style", "one-question": "style",
    "length-bound": "efficiency", "tool-detours": "efficiency", "turns-bound": "efficiency",
    # the tool path (AskUserQuestion): offline-only checks, see the README section "The tool path (AskUserQuestion)"
    "no-tool-search": "process", "tool-denied-fallback": "process", "tool-template-ends-turn": "process", "tool-one-call-one-question": "process",
    "tool-header-short": "style", "tool-options-shape": "style", "tool-recommended-first": "style", "tool-skip-last": "style",
    "tool-question-shape": "style", "tool-no-extra-text": "style",
}


@dataclass
class Case:
    row: Row
    events: list
    skill: str = "oaat"
    titles: list = field(default_factory=list)
    reply: str = ""
    history: str = ""   # the earlier turn the reply follows: the agent's own earlier reply, else the transcript the prompt quotes

    @classmethod
    def build(cls, row, events, skill="oaat"):
        earlier, _ = ask_tool.split(events)
        # an earlier turn that presented its item with the tool holds the header in the question text: add it to the history
        past = "\n".join(filter(None, [trace.earlier_reply(events), *ask_tool.question_texts(ask_tool.last_turn(earlier))]))
        return cls(row, events, skill, pending_titles(row.prompt), trace.final_reply(events), past or row.prompt)

    @property
    def turn(self):
        """The events of the last turn (the turn that is graded)."""
        return ask_tool.last_turn(self.events)

    @property
    def asks(self):
        """The AskUserQuestion calls of the graded turn, in order (see ask_tool.ask_records): answered, denied and failed ones."""
        return ask_tool.ask_records(self.turn)

    @property
    def tool_path(self):
        """True when an item of this turn is presented with AskUserQuestion AND the call got a tool_result that is not an error.

        A denied or failed call (an error result, or no result at all) is only recorded: the skill sanctions the text template as the
        fallback, so such a turn is graded on the text path (and `tool-denied-fallback` fails it when nothing was printed after the call).
        """
        return self.row.expect in ("item", "skip") and any(r["answered"] for r in self.asks)


def plain(line):
    """A line without Markdown emphasis, heading marks and list marks, for matching labels."""
    return re.sub(r"[*_`#>]", "", line).strip()


def headers(reply):
    """[(n, m)] of every 'Item N of M' in the reply."""
    return [(int(n), int(m)) for n, m in HEADER_RE.findall(reply)]


def label_of(line):
    """The section label that starts a line ('what it is', 'one question', ...), or None."""
    text = plain(line).lower().lstrip("-• ").strip()
    for label in LABELS + WRAPUP_LABELS:
        if text.startswith(label):
            return label
    return None


def sections(reply):
    """{label: [lines after the label line, up to the next label]}; the label line itself is the first entry."""
    found, current = {}, None
    for line in reply.splitlines():
        label = label_of(line)
        if label and label not in found:
            current = label
            found[label] = [line]
        elif current is not None:
            found[current].append(line)
    return found


def check_skill_loaded(case):
    skills = trace.init_skills(case.events)
    if skills is None:
        return ("skill-loaded", False, "the trace has no system/init event, so the loaded skills are unknown")
    ok = any(trace.skill_matches(s, case.skill) for s in skills)
    return ("skill-loaded", ok, f"system/init lists {len(skills)} skills; {case.skill} {'is' if ok else 'is missing'} among them")


def check_skill_trigger(case):
    invoked = [s for s in trace.skills_invoked(case.events) if trace.skill_matches(s, case.skill)]
    if case.row.should_trigger:
        return ("skill-trigger", bool(invoked), f"Skill tool called for {case.skill}: {len(invoked)} time(s); expected at least 1")
    return ("skill-trigger", not invoked, f"Skill tool called for {case.skill}: {len(invoked)} time(s); expected 0")


def check_completed(case):
    result = trace.result_event(case.events)
    if result is None:
        return ("completed", False, "no result event: the run did not finish")
    # on the tool path the item is in the tool call, so a turn with no text at all (no count line) is still a reply
    ok = result.get("subtype") == "success" and result.get("is_error") is False and (bool(case.reply.strip()) or case.tool_path)
    return ("completed", ok, f"result subtype {result.get('subtype')}, is_error {result.get('is_error')}, reply {len(case.reply.split())} words")


def check_one_header(case):
    found = headers(case.reply)
    return ("one-header", len(found) == 1, f"'Item N of M' headers in the reply: {len(found)}; expected exactly 1")


def check_header_number(case):
    found = headers(case.reply)
    expected = previous_header_number(case.history) + 1
    if len(found) != 1:
        return ("header-number", False, f"expected the header 'Item {expected} of M'; the reply has {len(found)} headers")
    return ("header-number", found[0][0] == expected, f"header says item {found[0][0]}; expected item {expected}")


def check_header_total(case):
    found = headers(case.reply)
    total = len(case.titles)
    if not found or not total:
        return ("header-total", False, f"no header or no pending items to compare (items in prompt: {total})")
    return ("header-total", found[0][1] == total, f"header says {found[0][1]} items; the prompt lists {total}")


def check_sections(case):
    found = sections(case.reply)
    missing = [label for label in LABELS if label not in found]
    order = [label for label in found if label in LABELS]
    ordered = order == [label for label in LABELS if label in found]
    ok = not missing and ordered
    return ("sections", ok, "all four sections in order" if ok else f"missing: {', '.join(missing) or 'none'}; in order: {ordered}")


def indent(line):
    return len(line) - len(line.lstrip())


def option_lines(reply):
    """The option lines under the Options label.

    The list ends at a blank line after the first option, or at a line with the indent of the label. A real reply nests its
    options under a `- **Options:**` bullet and follows them with `- **Question:**` and a closing block, and the options
    count must not include those lines. Without bullets, the indented lines under the label are the options.
    """
    body = sections(reply).get("options", [])
    if not body:
        return []
    label, rest = body[0], body[1:]
    base = indent(label) if BULLET_RE.match(label) else -1
    found, plain_lines = [], []
    for line in rest:
        if not line.strip():
            if found:
                break
            continue
        if plain(line).lower().lstrip("-• ").startswith(("recommend",) + QUESTION_LABELS):
            break
        if BULLET_RE.match(line) and indent(line) > base:
            found.append(line)
        elif indent(line) <= base:
            break
        elif not found:
            plain_lines.append(line)
    return found or plain_lines


def check_options(case):
    count = len(option_lines(case.reply))
    return ("options-max-3", 1 <= count <= MAX_OPTIONS, f"options listed: {count}; allowed 1 to {MAX_OPTIONS}")


def check_options_numbered(case):
    lines = option_lines(case.reply)
    numbers = [int(m.group(1)) for m in (NUMBER_RE.match(line) for line in lines) if m]
    ok = bool(lines) and numbers == list(range(1, len(lines) + 1))
    return ("options-numbered", ok, f"option lines {len(lines)}, numbered 1 to {len(numbers)}" if ok else f"option lines {len(lines)}; they must be numbered 1, 2, 3 (numbers found: {numbers or 'none'})")



def recommendation_region(reply):
    """The lines from the Options label up to the question: the label line, the options and the lines after them.

    The region ends at the first line that labels the question or holds a `?`.
    """
    body = sections(reply).get("options", [])
    region = body[:1]
    for line in body[1:]:
        if plain(line).lower().lstrip("-• ").startswith(QUESTION_LABELS) or "?" in line:
            break
        region.append(line)
    return region


def check_recommended(case):
    """The template puts the recommendation on one line of its own after the last option: not a bullet, not an option line."""
    region = recommendation_region(case.reply)
    last_option = max((i for i, line in enumerate(region) if i and BULLET_RE.match(line)), default=-1)
    marked = [(i, line) for i, line in enumerate(region) if "recommend" in line.lower()]
    if len(marked) != 1:
        return ("recommended-marked", False, f"lines with a recommendation between Options and the question: {len(marked)}; expected exactly 1")
    index, line = marked[0]
    if BULLET_RE.match(line):
        return ("recommended-marked", False, "the recommendation is a list item; it must be a line of its own after the options")
    if index <= last_option:
        return ("recommended-marked", False, "the recommendation comes before or inside the options; it must follow the last option")
    return ("recommended-marked", True, "one recommendation line after the options, not a list item")


def check_one_question(case, labelled=True):
    total = case.reply.count("?")
    if total != 1:
        return ("one-question", False, f"question marks in the reply: {total}; expected exactly 1")
    if not labelled:
        return ("one-question", True, "exactly 1 question mark")
    question = sections(case.reply).get("one question")
    if question is None:
        return ("one-question", True, "exactly 1 question mark; the reply has no 'One question' section, which the `sections` check reports")
    ok = "?" in "\n".join(question)
    return ("one-question", ok, "the single question mark is in the 'One question' section" if ok else "the question mark is outside the 'One question' section")


def parked_title(case):
    """On a skip, the skill confirms the answer in one line, so the reply may name the item that was just parked."""
    return previous_header_title(case.history).lower() if case.row.expect == "skip" else ""


def present_titles(case):
    reply = case.reply.lower()
    return [t for t in case.titles if t.lower() in reply and t.lower() != parked_title(case)]


def check_no_other_items(case):
    found = present_titles(case)
    ok = len(found) <= 1
    return ("no-other-items", ok, f"pending titles quoted in the reply: {len(found)} ({'; '.join(found) or 'none'}); allowed at most 1")


def check_no_preview(case):
    reply = case.reply
    allowed = {str(previous_header_number(case.history))} if case.row.expect == "skip" else set()
    stray = len([n for n in re.findall(r"\bitem\s+(\d+)\b", reply, re.IGNORECASE) if n not in allowed]) - len(headers(reply))
    phrases = [p for p in PREVIEW_PHRASES if p in reply.lower()]
    ok = stray <= 0 and not phrases
    return ("no-preview", ok, "no other item number and no look-ahead phrase" if ok else f"extra item numbers: {max(stray, 0)}; look-ahead phrases: {', '.join(phrases) or 'none'}")


def check_length(case):
    lines = [line for line in case.reply.splitlines() if line.strip()]
    words = len(case.reply.split())
    ok = len(lines) <= MAX_REPLY_LINES and words <= MAX_REPLY_WORDS
    return ("length-bound", ok, f"{len(lines)} non-empty lines (max {MAX_REPLY_LINES}), {words} words (max {MAX_REPLY_WORDS})")


def check_wrapup(case):
    found = sections(case.reply)
    missing = [label for label in WRAPUP_LABELS if label not in found]
    order = [label for label in found if label in WRAPUP_LABELS]
    ordered = order == [label for label in WRAPUP_LABELS if label in found]
    ok = not missing and ordered
    return ("wrapup-headings", ok, "Decided, Parked, Next action, in order" if ok else f"missing: {', '.join(missing) or 'none'}; in order: {ordered}")


# The tangent flag may name the current item as X ("This may be a tangent from Item 4 of 6, the retention rule. Continue or refocus?"):
# that is the skill's own sentence, not an item header. The pattern is the one that `tangent-flag` accepts: X holds no '.', '?' or
# newline, so a header between the start of the flag and a later "continue or refocus?" is NOT erased (no DOTALL, no loose span).
TANGENT_FLAG_RE = re.compile(r"this may be a tangent from [^.?\n]+\.\s*continue or refocus\?", re.IGNORECASE)


def without_flag(text):
    return TANGENT_FLAG_RE.sub("", text)


def check_no_header(case):
    """No 'Item N of M' in the reply, outside the tangent flag; on a turn that called AskUserQuestion, none in the question texts either."""
    found = headers(without_flag(case.reply))
    in_questions = [h for r in case.asks for q in ask_tool.questions(r["input"]) for h in headers(without_flag(ask_tool.question_text(q)))]
    return ("no-oaat-header", not found and not in_questions,
            f"'Item N of M' headers in the reply: {len(found)}, in AskUserQuestion questions: {len(in_questions)}; expected 0")


def check_tangent(case):
    ok = TANGENT_FLAG_RE.search(case.reply) is not None
    return ("tangent-flag", ok, "the reply flags the tangent: 'This may be a tangent from X. Continue or refocus?'" if ok else "the tangent flag is missing")


def check_all_listed(case):
    missing = [t for t in case.titles if t.lower() not in case.reply.lower()]
    return ("all-items-listed", not missing and bool(case.titles), f"pending titles missing from the reply: {len(missing)} of {len(case.titles)}")


def check_detours(case):
    """AskUserQuestion calls are the item presentations of the tool path, not detours: they are not counted. Neither are the calls that
    record a milestone answer (ask_tool.is_bookkeeping: the milestones skill, a Bash call running milestones.py, a Read of
    .cache/milestones.json): the oaat skill requires that recording in the same turn."""
    calls = trace.tool_calls(case.events)
    names = [name for name, _ in calls]
    kept = [(name, data) for name, data in calls if name not in ("Skill", ask_tool.TOOL)]
    others = [name for name, data in kept if not ask_tool.is_bookkeeping(name, data)]
    return ("tool-detours", len(others) <= MAX_OTHER_TOOL_CALLS,
            f"tool calls besides Skill and AskUserQuestion: {len(others)} (max {MAX_OTHER_TOOL_CALLS}); AskUserQuestion calls, not counted: "
            f"{names.count(ask_tool.TOOL)}; milestone bookkeeping, not counted: {len(kept) - len(others)}")


def check_turns(case):
    """Every AskUserQuestion call of the graded turn adds a model turn (call, tool_result, next message) to num_turns: not counted.
    The same holds for every milestone bookkeeping call of the graded turn (ask_tool.is_bookkeeping)."""
    turns, _ = trace.stats(case.events)
    if turns is None:
        return ("turns-bound", False, "the result event has no num_turns")
    asked, recorded = len(case.asks), len(ask_tool.bookkeeping_calls(case.turn))
    left = turns - asked - recorded
    return ("turns-bound", left <= MAX_TURNS,
            f"num_turns {turns} minus {asked} AskUserQuestion call(s) minus {recorded} milestone bookkeeping call(s) = {left} (max {MAX_TURNS})")


def check_no_tool_search(case):
    """Process check, for BOTH paths. Headless runs have no AskUserQuestion tool (measured), so a search for it is a wasted call."""
    found = ask_tool.searched_for_tool(case.events)
    return ("no-tool-search", not found, f"ToolSearch calls that mention AskUserQuestion: {len(found)}; expected 0 (the skill says do not search for it)")


def ask_question(record):
    """The first question of one AskUserQuestion record ({} when the input has none)."""
    return ask_tool.first_question_of(record["input"])


def per_call(case, check_id, judge):
    """One verdict for a check that grades every AskUserQuestion call of the turn on its own.

    judge(question, record, index) returns (problem or None, what was measured). The note names every call; the check passes
    only when every call passes, and the note of a failure says which call and why.
    """
    parts, ok = [], True
    for index, record in enumerate(case.asks, 1):
        problem, measured = judge(ask_question(record), record, index)
        ok = ok and problem is None
        parts.append(f"call {index}: {measured}" + (f" -> {problem}" if problem else ""))
    return (check_id, ok and bool(parts), "; ".join(parts) or "no AskUserQuestion call in the turn")


def check_tool_one_call_one_question(case):
    """Per call: exactly one question. Across calls: sequential. A call must get its tool_result before the next call is made;
    two calls in one assistant message, or two calls without a tool_result between them, are batching ("never batch")."""
    asks = case.asks
    counts = [len(ask_tool.questions(r["input"])) for r in asks]
    overlaps = ask_tool.unanswered_overlaps(case.turn)
    ok = bool(asks) and all(c == 1 for c in counts) and not overlaps
    return ("tool-one-call-one-question", ok, f"AskUserQuestion calls in the turn: {len(asks)}; questions per call: {counts}; "
                                              f"calls made before the previous call had its tool_result: {len(overlaps)}; expected 1 question each and 0")


def judge_header(question, record, index):
    header = question.get("header")
    if not isinstance(header, str) or not header.strip():
        return "no header, or it is empty", "no header"
    # len() of a Python str counts code points, which is what the tool's limit of 12 characters is read as here
    return (None if len(header) <= MAX_TOOL_HEADER else f"more than {MAX_TOOL_HEADER}"), f"header {header!r} has {len(header)} characters"


def check_tool_header_short(case):
    return per_call(case, "tool-header-short", judge_header)


def judge_options_shape(question, record, index):
    options = question.get("options")
    if not isinstance(options, list):
        return "the question has no options list", "no options"
    bad = [i for i, o in enumerate(options, 1)
           if not (isinstance(o, dict) and all(isinstance(o.get(k), str) and o[k].strip() for k in ("label", "description")))]
    multi = question.get("multiSelect", False)
    ok = MIN_TOOL_OPTIONS <= len(options) <= MAX_TOOL_OPTIONS and not bad and multi is False
    measured = (f"{len(options)} options (allowed {MIN_TOOL_OPTIONS} to {MAX_TOOL_OPTIONS}), without label and description: {bad or 'none'}, "
                f"multiSelect {multi!r}")
    return (None if ok else "options must be 2 to 4, each with label and description, multiSelect false or absent"), measured


def check_tool_options_shape(case):
    return per_call(case, "tool-options-shape", judge_options_shape)


def option_labels(question):
    return ask_tool.option_labels(question)


def judge_recommended(question, record, index):
    labels = option_labels(question)
    if not labels:
        return "the question has no option labels", "no labels"
    # strip: a trailing space after the suffix is accepted, as one after `Skip for now` is
    marked = [i for i, label in enumerate(labels, 1) if label.strip().endswith(RECOMMENDED_SUFFIX)]
    return (None if marked == [1] else "only option 1 may carry the suffix"), f"options whose label ends with {RECOMMENDED_SUFFIX}: {marked or 'none'}"


def check_tool_recommended_first(case):
    return per_call(case, "tool-recommended-first", judge_recommended)


def judge_skip_last(question, record, index):
    labels = option_labels(question)
    last = labels[-1] if labels else None
    return (None if isinstance(last, str) and last.strip() == SKIP_LABEL else f"the last label must be {SKIP_LABEL!r}"), f"last option label {last!r}"


def check_tool_skip_last(case):
    return per_call(case, "tool-skip-last", judge_skip_last)


TOOL_QUESTION_RE = re.compile(r"\s*Item\s+(\d+)\s+of\s+(\d+),\s*([^:?\n]+?):\s*([^?\n]+?)\.(?:\s|$)", re.IGNORECASE)


def judge_question_shape(question, record, index):
    text = ask_tool.question_text(question)
    match = TOOL_QUESTION_RE.match(text)
    marks = text.count("?")
    if not match:
        return "the question must start with 'Item N of M, type: Title.'", f"starts with {text[:40]!r}, {marks} question mark(s)"
    return (None if marks == 1 else "exactly one question mark is allowed"), (
        f"Item {match.group(1)} of {match.group(2)}, type {match.group(3)!r}, title {match.group(4)!r}, {marks} question mark(s)")


def check_tool_question_shape(case):
    return per_call(case, "tool-question-shape", judge_question_shape)


def surface(question):
    """Everything of one call that the user reads: the question, the option labels and the option descriptions."""
    return "\n".join([ask_tool.question_text(question), *option_labels(question), *ask_tool.option_descriptions(question)])


def titles_in(case, text):
    return [t for t in case.titles if t.lower() in text.lower()]


def check_tool_header_number(case):
    """Per call, in order: the first call is item previous+1 (previous: the last header of the earlier turns, else 0); every later call is
    the previous call's item + 1, or the same item again when its answer was free text ("Other", a question, a disagreement) or it failed."""
    number = previous_header_number(case.history)
    repeat_ok = False

    def judge(question, record, index):
        nonlocal number, repeat_ok
        found = headers(ask_tool.question_text(question))
        allowed = {number + 1} | ({number} if repeat_ok and index > 1 else set())
        repeat_ok = not record["answered"] or record["label"] is None
        if len(found) != 1:
            return "the question needs exactly one 'Item N of M' header", f"{len(found)} headers, expected item {sorted(allowed)}"
        got = found[0][0]
        number = got
        return (None if got in allowed else f"expected item {sorted(allowed)}"), f"item {got}"

    return per_call(case, "header-number", judge)


def check_tool_header_total(case):
    total = len(case.titles)

    def judge(question, record, index):
        found = headers(ask_tool.question_text(question))
        if not found or not total:
            return "no header or no pending items to compare", f"no header (items in prompt: {total})"
        return (None if found[0][1] == total else f"the prompt lists {total}"), f"header says {found[0][1]} items"

    return per_call(case, "header-total", judge)


def check_tool_no_other_items(case):
    def judge(question, record, index):
        found = titles_in(case, surface(question))
        return (None if len(found) <= 1 else "at most 1 pending title may appear"), f"pending titles in the question and options: {len(found)} ({'; '.join(found) or 'none'})"

    return per_call(case, "no-other-items", judge)


def check_tool_no_preview(case):
    def judge(question, record, index):
        text = surface(question)
        stray = len(re.findall(r"\bitem\s+\d+\b", text, re.IGNORECASE)) - len(headers(ask_tool.question_text(question)))
        phrases = [p for p in PREVIEW_PHRASES if p in text.lower()]
        problem = None if stray <= 0 and not phrases else "no other item number and no look-ahead phrase"
        return problem, f"extra item numbers: {max(stray, 0)}, look-ahead phrases: {', '.join(phrases) or 'none'}"

    return per_call(case, "no-preview", judge)


LABEL_LINE_RE = re.compile(r"^(?:" + "|".join(LABELS + WRAPUP_LABELS + ("recommendation",)) + r")\s*:", re.IGNORECASE)


def labelled(line):
    """True for a line that starts with a template or wrap-up label and a colon ('**Parked:** x'), not for a plain 'Parked for later.'"""
    return LABEL_LINE_RE.match(plain(line).lstrip("-• ").strip()) is not None


def wrapup_ok(text):
    found = sections(text)
    order = [label for label in found if label in WRAPUP_LABELS]
    return all(label in found for label in WRAPUP_LABELS) and order == list(WRAPUP_LABELS)


def item_number(record):
    """The N of the 'Item N of M' that the question of one AskUserQuestion record starts with (None when it has no header)."""
    found = headers(ask_tool.question_text(ask_question(record)))
    return found[0][0] if found else None


def retried(case):
    """The 1-based indexes of the calls that FAILED (an error result) and were immediately retried, once, for the SAME item.

    The skill: a call that fails on its input is called again once with the input fixed. A failed call followed by a call for the same
    item is such a retry, and needs no text of its own after it. The retry of a retry is not exempt: a second failure of the same item
    is not retried a third time, so it needs the template text after it like any denied call. An error result cannot be told apart
    from a denial, so a denied call that is called again for the same item is allowed too.
    """
    asks, found, was_retry = case.asks, set(), False
    for index, record in enumerate(asks, 1):
        nxt = asks[index] if index < len(asks) else None
        failed_call = record["result"] is not None and record["result"]["error"]
        same = nxt is not None and item_number(record) is not None and item_number(record) == item_number(nxt)
        if failed_call and same and not was_retry:
            found.add(index)
            was_retry = True
        else:
            was_retry = False
    return found


def check_tool_no_extra_text(case):
    """The text outside the calls: before the first call the one-line count (or a plain line), between two calls ONE short plain line
    (a confirmation, an explanation or a correction), after the last call nothing, one plain line or the wrap-up.

    A plain line: one line, short, no list mark, no `Item N of M` header, no template label, no look-ahead phrase, no other item
    number, no question mark (the question is in the call; the tangent flag is the one sentence that may ask), and no pending
    title except the item of the call after it or the call before it. The wrap-up (Decided, Parked, Next action) may name any title.

    The tangent flag ends the turn: as the text after the last call it is allowed (the one sentence that may ask); anywhere before a
    call (before the first call, or between two calls) it is a violation, and its question mark counts. The text after a failed call
    that is retried for the same item is a plain line between two calls; the text after a denied call that is not retried is the
    fallback, graded by `tool-denied-fallback`.
    """
    asks, texts, retries = case.asks, ask_tool.segments(case.turn), retried(case)
    nearby = [{n for n, _ in headers(ask_tool.question_text(ask_question(r)))} for r in asks]
    current = [set(titles_in(case, surface(ask_question(r)))) for r in asks]
    problems, shown = [], []
    for gap, text in enumerate(texts):
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        if gap and not asks[gap - 1]["answered"] and gap not in retries:
            shown.append(f"after the denied or failed call {gap}: the text fallback, graded by `tool-denied-fallback` and not here")
            continue
        where = "before the first call" if gap == 0 else "after the last call" if gap == len(asks) else f"between call {gap} and call {gap + 1}"
        words = len(text.split())
        shown.append(f"{where}: {len(lines)} line(s), {words} words")
        if gap == len(asks) and wrapup_ok(text):
            if len(lines) > MAX_REPLY_LINES or words > MAX_REPLY_WORDS:
                problems.append(f"{where}: the wrap-up has {len(lines)} lines and {words} words (max {MAX_REPLY_LINES} and {MAX_REPLY_WORDS})")
            continue
        closing = gap == len(asks)
        bare = without_flag(text) if closing else text   # a flag before a call is not erased: it must not end up in a header or question test unseen
        limit = MAX_COUNT_LINE_WORDS if gap == 0 else MAX_PLAIN_LINE_WORDS
        found = []
        if not closing and TANGENT_FLAG_RE.search(text):
            found.append("the tangent flag before a call (the flag ends the turn)")
        if len(lines) > 1:
            found.append(f"{len(lines)} lines")
        if words > limit:
            found.append(f"{words} words (max {limit})")
        if any(BULLET_RE.match(line) for line in lines):
            found.append("a numbered or bulleted line")
        if headers(bare):
            found.append("an 'Item N of M' header")
        if any(labelled(line) for line in lines):
            found.append("a template or wrap-up label")
        if "?" in bare:
            found.append("a question mark")
        if [p for p in PREVIEW_PHRASES if p in text.lower()]:
            found.append("a look-ahead phrase")
        allowed_numbers = {n for index in (gap - 1, gap) if 0 <= index < len(asks) for n in nearby[index]}
        if [n for n in re.findall(r"\bitem\s+(\d+)\b", bare, re.IGNORECASE) if int(n) not in allowed_numbers]:
            found.append("another item's number")
        allowed_titles = set().union(*(current[index] for index in (gap - 1, gap) if 0 <= index < len(asks)))
        extra = [t for t in titles_in(case, text) if t not in allowed_titles]
        if extra:
            found.append(f"another pending title ({'; '.join(extra)})")
        if found:
            problems.append(f"{where}: " + ", ".join(found))
    return ("tool-no-extra-text", not problems, "text outside the calls: " + ("; ".join(shown) or "none") + "; " + ("; ".join(problems) or "only plain lines and a wrap-up"))


FALLBACK_CHECKS = (check_one_header, check_header_number, check_header_total, check_sections, check_options, check_options_numbered, check_recommended,
                   check_one_question, check_no_other_items, check_no_preview, check_length)


def check_tool_denied_fallback(case):
    """Process check, for BOTH paths. A call without a successful tool_result (an error result, or no result: denied or failed) is only
    recorded, because the skill sanctions the text template as the fallback. But the user saw nothing of the item unless the agent
    printed something after the call (before the next call or the end of the turn): a denied call with no text after it fails.

    One exception: a call that failed with an error and is immediately called again, once, for the SAME item (the retry after an input
    error, see `retried`) needs no text after it; the retry is judged on its own."""
    asks, texts, retries = case.asks, ask_tool.segments(case.turn), retried(case)
    denied = [i for i, r in enumerate(asks, 1) if not r["answered"] and i not in retries]
    bare = [i for i in denied if not texts[i].strip()]
    broken = []
    if case.tool_path:
        # a mixed turn (an answered call and a denied one) is graded on the tool path, but the text after a denied call is the sanctioned
        # fallback: it must be a good template for that item. A turn with only denied calls is graded on the text path as a whole.
        for i in denied:
            if i in bare:
                continue
            found = headers(ask_tool.question_text(ask_question(asks[i - 1])))
            history = f"Item {found[0][0] - 1} of {found[0][1]}" if found else case.history
            sub = replace(case, reply=texts[i], history=history)
            bad = [cid for cid, ok, _ in (check(sub) for check in FALLBACK_CHECKS) if not ok]
            if bad:
                broken.append(f"call {i}: {', '.join(bad)}")
    return ("tool-denied-fallback", not bare and not broken,
            f"AskUserQuestion calls: {len(asks)}; denied or failed: {len(denied)}; retried once for the same item, not counted: {len(retries)}; "
            f"of those with no text after them: {len(bare)}; "
            f"fallback templates in a tool turn failing their text checks: {'; '.join(broken) or 'none'}; expected 0")


def is_template_item(text):
    """A text item of the fallback: a text with an 'Item N of M' header outside the tangent flag AND at least one label of the template
    (What it is, Why it matters, Options, One question). A lone header line is not enough: that is an extra line, graded by
    `tool-no-extra-text`. A mangled item with a header and no label at all is not seen here (a known gap)."""
    bare = without_flag(text)
    return bool(headers(bare)) and any(label in sections(bare) for label in LABELS)


def check_template_ends_turn(case):
    """Process check, for BOTH paths. A template item printed as text (the fallback for a denied or unavailable tool) ends the turn: no
    AskUserQuestion call may follow it in the same turn, or the user would see the item twice and the walk would go on without them."""
    template_seen, after = 0, []
    for entry in ask_tool.timeline(case.turn):
        if entry["kind"] == "text" and is_template_item(entry["text"]):
            template_seen += 1
        elif entry["kind"] == "call" and entry["name"] == ask_tool.TOOL and template_seen:
            after.append(entry["id"])
    return ("tool-template-ends-turn", not after,
            f"template items printed as text in the turn: {template_seen}; AskUserQuestion calls after the first of them: {len(after)}; expected 0")


def check_tool_early_end(case):
    """Outcome check, tool path. When the last answered call was answered with a chosen option label or `Skip for now` (not free text, so
    not a stop, a question or a tangent), items remain (its 'Item N of M' has N < M) and nothing asked to stop, the turn must go on: with
    another AskUserQuestion call (answered or denied) or, as the fallback, a text template for a later item. A closing line or a wrap-up
    there is an early end: the user is left with nothing to answer."""
    asks = case.asks
    answered = [(i, r) for i, r in enumerate(asks, 1) if r["answered"]]
    if not answered:
        return ("tool-early-end", False, "no answered AskUserQuestion call in the turn")
    index, record = answered[-1]
    if record["label"] is None:
        return ("tool-early-end", True, f"call {index}, the last answered call, was answered with free text: a stop, a question or a tangent is handled by the other rules")
    found = headers(ask_tool.question_text(ask_question(record)))
    if not found:
        return ("tool-early-end", True, f"call {index} has no 'Item N of M' header, so the items left are unknown (`tool-question-shape` reports it)")
    n, m = found[0]
    if n >= m:
        return ("tool-early-end", True, f"call {index} (item {n} of {m}) was the last item: the wrap-up may follow")
    if asks[index:]:
        return ("tool-early-end", True, f"call {index} (item {n} of {m}, label {record['label']!r}) is followed by {len(asks) - index} more call(s)")
    closing = ask_tool.segments(case.turn)[-1]
    later = [h for h in headers(without_flag(closing)) if h[0] > n]
    return ("tool-early-end", bool(later),
            f"call {index} (item {n} of {m}) was answered with the label {record['label']!r}; {m - n} item(s) remain; "
            + ("the turn ends with a text template for a later item" if later else "the turn ends with no next call and no template for a later item"))


def check_no_tool_question(case):
    """On a row that allows no item presentation (stop, tangent, list, none) ANY AskUserQuestion call is a violation: the tangent
    rule says no item header and no repeat of the item, stop gives the wrap-up."""
    asks = case.asks
    return ("tool-not-allowed", not asks, f"AskUserQuestion calls in the turn: {len(asks)}; expected 0 on a {case.row.expect} row")


COMMON = (check_skill_loaded, check_skill_trigger, check_completed, check_detours, check_turns, check_no_tool_search, check_tool_denied_fallback,
          check_template_ends_turn)
BY_EXPECT = {
    "item": (check_one_header, check_header_number, check_header_total, check_sections, check_options, check_options_numbered, check_recommended,
             check_one_question, check_no_other_items, check_no_preview, check_length),
    "stop": (check_wrapup, check_no_header, check_length, check_no_tool_question),
    "tangent": (check_tangent, check_no_header, lambda case: check_one_question(case, labelled=False), check_length, check_no_tool_question),
    "list": (check_all_listed, check_no_header, check_no_tool_question),
    "none": (check_no_header, check_no_tool_question),
}
BY_EXPECT["skip"] = BY_EXPECT["item"]
TOOL_CHECKS = (check_tool_one_call_one_question, check_tool_header_short, check_tool_options_shape, check_tool_recommended_first,
               check_tool_skip_last, check_tool_question_shape, check_tool_no_extra_text, check_tool_early_end)
# On the tool path the text-template style checks (one-header, sections, options-*, recommended-marked, one-question, length-bound)
# do not apply. The outcome checks that read the item header and titles still apply, per call, on the question and its options.
TOOL_ITEM = (check_tool_header_number, check_tool_header_total, check_tool_no_other_items, check_tool_no_preview) + TOOL_CHECKS


def run_checks(case):
    """Every check that applies to the case, as [(id, passed, note)].

    A turn with an answered AskUserQuestion call for an item (expect item or skip) is graded on the tool path, call by call; every
    other turn (no call, or only denied or failed calls) on the text path.
    """
    return [check(case) for check in COMMON + (TOOL_ITEM if case.tool_path else BY_EXPECT[case.row.expect])]
