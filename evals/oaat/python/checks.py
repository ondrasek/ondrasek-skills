"""Deterministic checks of one run of the oaat skill: on the trace (what the agent did) and on the reply (what it said).

Every check is a pure function of a `Case` and returns (id, passed, note). The note says what was measured, so a failure
explains itself. The checks fall into the four groups of the OpenAI post (https://developers.openai.com/blog/eval-skills):

    outcome     did the reply do what the skill promises?
    process     did the agent load and call the skill (a tool call, not a claim in prose)?
    style       does the reply keep the skill's fixed shape?
    efficiency  did the agent stay short and avoid detours?

Which checks apply to a prompt depends on its `expect` column (see run_checks).

A second path exists: when the turn calls the `AskUserQuestion` tool for an item (expect item or skip), the item is in the tool
input and not in the reply text, so the text-template style checks give way to the tool checks (see `Case.tool_path` and
`run_checks`). A turn without that call is graded exactly as before. The tool path can only be exercised on invented traces: a
headless `claude -p` run has no such tool (README, "The tool path").
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
MAX_COUNT_LINE_WORDS = 20  # the one-line count before the first item ("5 items need you. First one:")

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
    "no-oaat-header": "outcome",
    "one-header": "style", "sections": "style", "options-max-3": "style", "options-numbered": "style", "recommended-marked": "style", "one-question": "style",
    "length-bound": "efficiency", "tool-detours": "efficiency", "turns-bound": "efficiency",
    # the tool path (AskUserQuestion): offline-only checks, see the README section "The tool path (AskUserQuestion)"
    "no-tool-search": "process", "tool-one-call-one-question": "process",
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
    def tool_path(self):
        """True when the item of this turn is presented with AskUserQuestion: the text-template checks then do not apply."""
        return self.row.expect in ("item", "skip") and bool(ask_tool.ask_calls(self.turn))


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


# The tangent flag may name the current item as X ("This may be a tangent from Item 1 of 5, the database decision. Continue or refocus?"):
# that is the skill's own sentence, not an item header. A real run (2026-11-22) was wrongly failed for it.
TANGENT_FLAG_RE = re.compile(r"this may be a tangent from.*?continue or refocus\?", re.IGNORECASE | re.DOTALL)


def check_no_header(case):
    found = headers(TANGENT_FLAG_RE.sub("", case.reply))
    return ("no-oaat-header", not found, f"'Item N of M' headers in the reply: {len(found)}; expected 0")


def check_tangent(case):
    ok = re.search(r"this may be a tangent from [^.?\n]+\.\s*continue or refocus\?", case.reply, re.IGNORECASE) is not None
    return ("tangent-flag", ok, "the reply flags the tangent: 'This may be a tangent from X. Continue or refocus?'" if ok else "the tangent flag is missing")


def check_all_listed(case):
    missing = [t for t in case.titles if t.lower() not in case.reply.lower()]
    return ("all-items-listed", not missing and bool(case.titles), f"pending titles missing from the reply: {len(missing)} of {len(case.titles)}")


def check_detours(case):
    others = [name for name, _ in trace.tool_calls(case.events) if name != "Skill"]
    return ("tool-detours", len(others) <= MAX_OTHER_TOOL_CALLS, f"tool calls besides Skill: {len(others)} (max {MAX_OTHER_TOOL_CALLS})")


def check_turns(case):
    turns, _ = trace.stats(case.events)
    if turns is None:
        return ("turns-bound", False, "the result event has no num_turns")
    return ("turns-bound", turns <= MAX_TURNS, f"num_turns {turns} (max {MAX_TURNS})")


def check_no_tool_search(case):
    """Process check, for BOTH paths. Headless runs have no AskUserQuestion tool (measured), so a search for it is a wasted call."""
    found = ask_tool.searched_for_tool(case.events)
    return ("no-tool-search", not found, f"ToolSearch calls that mention AskUserQuestion: {len(found)}; expected 0 (the skill says do not search for it)")


def tool_item(case):
    """(the AskUserQuestion calls of the turn, the first question of the first call or {})."""
    return ask_tool.ask_calls(case.turn), ask_tool.first_question(case.turn)


def check_tool_one_call_one_question(case):
    calls, _ = tool_item(case)
    asked = len(ask_tool.questions(calls[0])) if calls else 0
    ok = len(calls) == 1 and asked == 1
    return ("tool-one-call-one-question", ok, f"AskUserQuestion calls in the turn: {len(calls)}; questions in the first call: {asked}; expected 1 and 1")


def check_tool_header_short(case):
    header = tool_item(case)[1].get("header")
    if not isinstance(header, str) or not header.strip():
        return ("tool-header-short", False, "the question has no header, or it is empty")
    return ("tool-header-short", len(header) <= MAX_TOOL_HEADER, f"header {header!r}: {len(header)} characters (max {MAX_TOOL_HEADER})")


def check_tool_options_shape(case):
    question = tool_item(case)[1]
    options = question.get("options")
    if not isinstance(options, list):
        return ("tool-options-shape", False, "the question has no options list")
    bad = [i for i, o in enumerate(options, 1)
           if not (isinstance(o, dict) and all(isinstance(o.get(k), str) and o[k].strip() for k in ("label", "description")))]
    multi = question.get("multiSelect", False)
    ok = MIN_TOOL_OPTIONS <= len(options) <= MAX_TOOL_OPTIONS and not bad and multi is False
    return ("tool-options-shape", ok, f"options: {len(options)} (allowed {MIN_TOOL_OPTIONS} to {MAX_TOOL_OPTIONS}); without label and description: "
                                       f"{bad or 'none'}; multiSelect {multi!r} (must be false or absent)")


def option_labels(question):
    options = question.get("options")
    return [o["label"] for o in options if isinstance(o, dict) and isinstance(o.get("label"), str)] if isinstance(options, list) else []


def check_tool_recommended_first(case):
    labels = option_labels(tool_item(case)[1])
    if not labels:
        return ("tool-recommended-first", False, "the question has no option labels")
    marked = [i for i, label in enumerate(labels, 1) if label.rstrip().endswith(RECOMMENDED_SUFFIX)]
    return ("tool-recommended-first", marked == [1], f"options whose label ends with {RECOMMENDED_SUFFIX}: {marked or 'none'}; expected only option 1")


def check_tool_skip_last(case):
    labels = option_labels(tool_item(case)[1])
    last = labels[-1] if labels else None
    return ("tool-skip-last", last == SKIP_LABEL, f"last option label: {last!r}; expected {SKIP_LABEL!r}")


TOOL_QUESTION_RE = re.compile(r"\s*Item\s+(\d+)\s+of\s+(\d+),\s*([^:?\n]+?):\s*([^?\n]+?)\.(?:\s|$)", re.IGNORECASE)


def check_tool_question_shape(case):
    text = ask_tool.question_text(tool_item(case)[1])
    match = TOOL_QUESTION_RE.match(text)
    marks = text.count("?")
    if not match:
        return ("tool-question-shape", False, f"the question does not start with 'Item N of M, type: Title.' ({text[:50]!r}); question marks: {marks}")
    return ("tool-question-shape", marks == 1, f"starts with Item {match.group(1)} of {match.group(2)}, type {match.group(3)!r}, title {match.group(4)!r}; "
                                              f"question marks: {marks}; expected exactly 1")


def check_tool_no_extra_text(case):
    """Outside the call the agent may print the one-line count before the first item, and nothing else."""
    text = ask_tool.text_outside(case.turn)
    lines = [line for line in text.splitlines() if line.strip()]
    words = len(text.split())
    problems = []
    if len(lines) > 1:
        problems.append(f"{len(lines)} lines")
    if words > MAX_COUNT_LINE_WORDS:
        problems.append(f"{words} words (max {MAX_COUNT_LINE_WORDS})")
    if any(BULLET_RE.match(line) for line in lines):
        problems.append("a numbered or bulleted line")
    if headers(text):
        problems.append("an 'Item N of M' header")
    if any(label_of(line) for line in lines):
        problems.append("a template label")
    return ("tool-no-extra-text", not problems, f"text outside the tool call: {len(lines)} line(s), {words} words; " + (", ".join(problems) or "only a count line, or nothing"))


def on_question(check):
    """The outcome check `check`, run on the text of the tool question instead of the reply (the item is in the tool input)."""
    def run(case):
        return check(replace(case, reply=ask_tool.question_text(tool_item(case)[1])))
    return run


COMMON = (check_skill_loaded, check_skill_trigger, check_completed, check_detours, check_turns, check_no_tool_search)
BY_EXPECT = {
    "item": (check_one_header, check_header_number, check_header_total, check_sections, check_options, check_options_numbered, check_recommended,
             check_one_question, check_no_other_items, check_no_preview, check_length),
    "stop": (check_wrapup, check_no_header, check_length),
    "tangent": (check_tangent, check_no_header, lambda case: check_one_question(case, labelled=False), check_length),
    "list": (check_all_listed, check_no_header),
    "none": (check_no_header,),
}
BY_EXPECT["skip"] = BY_EXPECT["item"]
TOOL_CHECKS = (check_tool_one_call_one_question, check_tool_header_short, check_tool_options_shape, check_tool_recommended_first,
               check_tool_skip_last, check_tool_question_shape, check_tool_no_extra_text)
# On the tool path the text-template style checks (one-header, sections, options-*, recommended-marked, one-question, length-bound)
# do not apply. The outcome checks that read the item header and titles still apply, on the text of the question.
TOOL_ITEM = tuple(on_question(c) for c in (check_header_number, check_header_total, check_no_other_items, check_no_preview)) + TOOL_CHECKS


def run_checks(case):
    """Every check that applies to the case, as [(id, passed, note)].

    A turn that calls AskUserQuestion for an item (expect item or skip) is graded on the tool path; every other turn on the text path.
    """
    return [check(case) for check in COMMON + (TOOL_ITEM if case.tool_path else BY_EXPECT[case.row.expect])]
