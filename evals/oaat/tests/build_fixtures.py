"""Builds the INVENTED fixture traces in tests/fixtures/traces/{after,before}/<id>.jsonl.

Nothing here comes from a model. Every event is written by hand in the stream-json shape (see evals/_common/python/agent_trace.py), marked by
session ids that start with INVENTED, the model name invented-model and a made-up cost. The `after` set is what a good
version of the skill would produce for each prompt. The `before` set is the same run with five defects, as if the skill
text were not edited yet:

    implicit-walk      the agent never calls the Skill tool and dumps the list
    contextual-review  four options, a preview of the next item, two questions
    followup-skip      the reply to the skip shows item 1 again
    followup-stop      the wrap-up has no 'Next action'

A follow-up row has two turns, as in a real session (a `reply` in evals/prompts.csv): the trace holds the agent's answer to the
first message, which loads the skill and shows item 1, then the answer to the user's second message.
    negative-list      the skill triggers on a request for a full list (a false positive)

A third set, `tool/`, holds two INVENTED traces of the tool path (items presented with the AskUserQuestion tool). They exist because a
headless `claude -p` run cannot call that tool (measured), so the tool path can only be graded offline. The INPUT of the tool is
documented (code.claude.com/docs/en/agent-sdk/user-input); the tool_result envelope is the assumed standard one, UNCONFIRMED (README,
"The tool path (AskUserQuestion)"). Each trace is ONE turn: an interactive walk-through pauses at every call and the answer returns as
the tool_result in the same turn, so a skip is the tool_result `Skip for now` and never a new prompt:

    explicit-dollar    count line + call 1, answer a label, one plain line + call 2, `Skip for now`, one plain line + call 3, free text
                       `stop`, then the wrap-up
    followup-skip      call 1 skipped, call 2 answered with a free-text question, one plain line + call 2 again, a label, call 3,
                       free text `stop`, then the wrap-up (every call is answered before the next one is made)

Run `python3 tests/build_fixtures.py` to rewrite the files; a test checks that the committed files equal this output.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "traces"

SKILLS = ["oaat:oaat", "sitrep:sitrep"]


def item_reply(intro, n, m, kind, title, what, why, options, question):
    """A reply in the skill's shape. options: [(text, recommended reason or None)]."""
    lines = []
    if intro:
        lines += [intro, ""]
    lines += [f"**Item {n} of {m}, {kind}: {title}**", f"**What it is:** {what}", f"**Why it matters:** {why}", "**Options:**"]
    for number, (text, _) in enumerate(options, 1):
        lines.append(f"{number}. {text}")
    for number, (_, reason) in enumerate(options, 1):
        if reason:
            lines.append(f"**Recommendation:** Option {number}, because {reason}.")
    lines.append(f"**One question:** {question}")
    return "\n".join(lines)


AFTER = {
    "explicit-dollar": item_reply(
        "5 items need you. First one:", 1, 5, "decision", "Pick the database",
        "The first release needs one database.", "The choice sets hosting cost and the backup plan.",
        [("Postgres: more setup, handles many users", "growth is expected"), ("SQLite: no server, one writer at a time", None)],
        "Which database should the first release use?"),
    "explicit-name": item_reply(
        "4 items need you. First one:", 1, 4, "decision", "Choose the test framework",
        "The team is split on how to write tests.", "Two styles in one code base slow every review.",
        [("pytest: short tests, one new dependency", "most of the team already knows it"), ("unittest: no dependency, more typing", None)],
        "Which test framework should the project use?"),
    "implicit-walk": item_reply(
        "4 items need you. First one:", 1, 4, "risk", "Set the retry limit",
        "The client retries failed calls without a limit.", "One outage could turn into a retry storm.",
        [("Limit of 3 retries: bounded load", "it is the usual default"), ("Limit of 10 retries: more patient, more load", None),
         ("Keep no limit: nothing to build, high risk", None)],
        "Which retry limit should the client use?"),
    "implicit-overwhelmed": item_reply(
        "5 items need you. First one:", 1, 5, "decision", "Approve the budget",
        "The cloud estimate is 20 percent above plan.", "Every other cost decision depends on this number.",
        [("Approve the estimate: no delay, higher cost", None), ("Ask for a cut: slower, lower cost", "the gap is large enough to question")],
        "Do you approve the cloud estimate as it stands?"),
    "contextual-review": item_reply(
        "5 items need you. First one:", 1, 5, "risk", "Assign the security review",
        "Nobody owns the external audit yet.", "An audit without an owner can slip past the launch date.",
        [("Assign it to one engineer: fast, one person carries it", "it keeps one name on the work"), ("Book an outside firm: independent, slower", None)],
        "Who should own the security review?"),
    "followup-skip": item_reply(
        "Parked for later. Next:", 2, 5, "question", "Confirm the launch date",
        "Marketing needs a date by Friday.", "The announcement plan waits for it.",
        [("Friday the 14th: matches the audit plan", "it leaves a week of slack"), ("The following Monday: more slack, later announcement", None)],
        "Which launch date should marketing get?"),
    "followup-stop": "\n".join([
        "**Decided:**", "- Nothing yet.", "",
        "**Parked:**", "- Pick the database.", "",
        "**Next action:** Pick the database. It blocks the launch."]),
    "followup-tangent": "This may be a tangent from the database decision. Continue or refocus?",
    "negative-single": ("A Containerfile and a Dockerfile hold the same build instructions in the same format. "
                        "Podman reads both names; the name Containerfile does not tie the file to one tool."),
    "negative-list": "\n".join([
        "| # | Pending item |", "|---|---|", "| 1 | Pick the database |", "| 2 | Confirm the launch date |",
        "| 3 | Assign the security review |", "| 4 | Rename the staging branch |", "| 5 | Approve the logo change |"]),
    "negative-one-decision": ("Choose Postgres if you expect many users; choose SQLite if one process writes. "
                              "For a first release that plans to grow, pick Postgres."),
}
FIRST_TURN = item_reply(
    "5 items need you. First one:", 1, 5, "decision", "Pick the database",
    "The first release needs one database.", "The choice sets hosting cost and the backup plan.",
    [("Postgres: more setup, handles many users", "growth is expected"), ("SQLite: no server, one writer at a time", None)],
    "Which database should the first release use?")
TWO_TURNS = ("followup-skip", "followup-stop", "followup-tangent")
NO_SKILL = ("negative-single", "negative-list", "negative-one-decision")

BEFORE = dict(AFTER)
BEFORE["implicit-walk"] = "\n".join([
    "Here is what is pending:", "1. Choose the test framework", "2. Set the retry limit", "3. Decide on the licence", "4. Pick a release day",
    "Tell me which one to start with."])
BEFORE["contextual-review"] = "\n".join([
    "5 items need you. First one:", "", "**Item 1 of 5, risk: Assign the security review**",
    "**What it is:** Nobody owns the external audit yet.", "**Why it matters:** An audit without an owner can slip past the launch date.",
    "**Options:**", "1. Assign it to one engineer: fast, one person carries it.",
    "2. Book an outside firm: independent, slower.", "3. Split it between two people: shared, unclear owner.",
    "4. Delay the audit: no cost now, high risk.",
    "**Recommendation:** Option 1, because it keeps one name on the work.",
    "**One question:** Who should own the security review? Should we also set a deadline?",
    "Next up is the launch date."])
BEFORE["followup-skip"] = item_reply(
    "Parked for later. Next:", 1, 5, "decision", "Pick the database",
    "The first release needs one database.", "The choice sets hosting cost and the backup plan.",
    [("Postgres: more setup, handles many users", "growth is expected"), ("SQLite: no server, one writer at a time", None)],
    "Which database should the first release use?")
BEFORE["followup-stop"] = "\n".join(["**Decided:**", "- Nothing yet.", "", "**Parked:**", "- Pick the database."])
BEFORE["negative-list"] = AFTER["followup-skip"].replace("Parked for later. Next:", "5 items need you. First one:").replace(
    "Item 2 of 5", "Item 1 of 5")
SKILL_CALLED_BEFORE = {"negative-list"}


def events(prompt_id, reply, call_skill, index):
    session = f"INVENTED-{prompt_id}"
    init = {"type": "system", "subtype": "init", "session_id": session, "cwd": "/invented/fixture", "model": "invented-model",
            "claude_code_version": "INVENTED", "tools": ["Skill", "Read"], "skills": SKILLS, "plugins": [], "mcp_servers": []}
    out = [init]
    if call_skill:
        out.append({"type": "assistant", "parent_tool_use_id": None, "session_id": session,
                    "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_INVENTED_{index}", "name": "Skill",
                                                                  "input": {"skill": "oaat:oaat"}}]}})
        out.append({"type": "user", "parent_tool_use_id": None, "session_id": session,
                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_INVENTED_{index}",
                                                             "content": "INVENTED tool result"}]}})
    out.append({"type": "assistant", "parent_tool_use_id": None, "session_id": session,
                "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
    out.append({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": reply,
                "num_turns": 2 if call_skill else 1, "duration_ms": 3000 + 100 * index, "total_cost_usd": round(0.01 + 0.001 * index, 4),
                "stop_reason": "end_turn"})
    return out


def turns(prompt_id, reply, call_skill, index):
    """The events of a row: one turn, or for a two-turn row the first turn (the skill loads) and the resumed second turn."""
    if prompt_id not in TWO_TURNS:
        return events(prompt_id, reply, call_skill, index)
    return events(prompt_id, FIRST_TURN, call_skill, index) + events(prompt_id, reply, False, index)


def tool_input(n, m, kind, title, what, why, question, options, header=None):
    """The input of one AskUserQuestion call in the skill's shape. options: [(label, description)], the recommended one first."""
    skip = {"label": "Skip for now", "description": "Park this item and move on."}
    return {"questions": [{"header": header or kind.capitalize(), "multiSelect": False,
                           "question": f"Item {n} of {m}, {kind}: {title}. {what} {why} {question}",
                           "options": [{"label": label, "description": description} for label, description in options] + [skip]}]}


TOOL_FIRST = tool_input(
    1, 5, "decision", "Pick the database", "The first release needs one database.", "The choice sets hosting cost and the backup plan.",
    "Which database should the first release use?",
    [("Postgres (Recommended)", "More setup, handles many users; growth is expected."), ("SQLite", "No server, one writer at a time.")])
TOOL_SECOND = tool_input(
    2, 5, "question", "Confirm the launch date", "Marketing needs a date by Friday.", "The announcement plan waits for it.",
    "Which launch date should marketing get?",
    [("Friday the 14th (Recommended)", "Matches the audit plan; leaves a week of slack."), ("The following Monday", "More slack, later announcement.")])
COUNT = "5 items need you. First one:"
TOOL_THIRD = tool_input(
    3, 5, "risk", "Assign the security review", "Nobody owns the external audit yet.", "An audit without an owner can slip past the launch date.",
    "Who should own the security review?",
    [("Assign it to one engineer (Recommended)", "Fast; one person carries it."), ("Book an outside firm", "Independent, slower.")])
SKIP_ANSWER = "Skip for now"
# one turn each: a message is a list of blocks (a str is text, a (call input, tool_result content) pair is an AskUserQuestion call and its answer)
TOOL = {
    "explicit-dollar": [
        [COUNT, (TOOL_FIRST, "Postgres (Recommended)")],
        ["Noted: Postgres for the first release.", (TOOL_SECOND, SKIP_ANSWER)],
        ["Parked.", (TOOL_THIRD, "stop")],
        ["\n".join(["**Decided:** Postgres for the first release.", "**Parked:** Confirm the launch date.",
                    "**Next action:** Assign the security review, it has no owner yet."])]],
    "followup-skip": [
        [COUNT, (TOOL_FIRST, SKIP_ANSWER)],
        ["Parked for later.", (TOOL_SECOND, "Which date does marketing prefer?")],
        ["Marketing wants an answer by Friday, and the 14th leaves a week of slack.", (TOOL_SECOND, "Friday the 14th (Recommended)")],
        ["Noted: Friday the 14th.", (TOOL_THIRD, "stop")],
        ["\n".join(["**Decided:** Friday the 14th for the launch.", "**Parked:** Pick the database.",
                    "**Next action:** Assign the security review, it has no owner yet."])]],
}


def tool_walk(prompt_id, messages, index):
    """One INVENTED turn of an interactive walk on the tool path: the Skill call, then every message with its tool_results, the result event."""
    session = f"INVENTED-tool-{prompt_id}"
    out = [{"type": "system", "subtype": "init", "session_id": session, "cwd": "/invented/fixture", "model": "invented-model",
            "claude_code_version": "INVENTED", "tools": ["Skill", "AskUserQuestion"], "skills": SKILLS, "plugins": [], "mcp_servers": []},
           {"type": "assistant", "parent_tool_use_id": None, "session_id": session,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_INVENTED_{index}", "name": "Skill", "input": {"skill": "oaat:oaat"}}]}},
           {"type": "user", "parent_tool_use_id": None, "session_id": session,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_INVENTED_{index}", "content": "INVENTED tool result"}]}}]
    serial, last_text = 0, ""
    for blocks in messages:
        content, results, last_text = [], [], ""
        for block in blocks:
            if isinstance(block, str):
                content.append({"type": "text", "text": block})
                last_text = block
            else:
                tool_input_, answer = block
                tool_id = f"toolu_INVENTED_Q{index}_{serial}"
                serial += 1
                content.append({"type": "tool_use", "id": tool_id, "name": "AskUserQuestion", "input": tool_input_})
                results.append({"type": "tool_result", "tool_use_id": tool_id, "content": answer})
        out.append({"type": "assistant", "parent_tool_use_id": None, "session_id": session, "message": {"role": "assistant", "content": content}})
        if results:
            out.append({"type": "user", "parent_tool_use_id": None, "session_id": session, "message": {"role": "user", "content": results}})
    out.append({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": last_text,
                "num_turns": 1 + len(messages), "duration_ms": 9000 + 100 * index, "total_cost_usd": round(0.02 + 0.001 * index, 4),
                "stop_reason": "end_turn"})
    return out


def build():
    """{(set, prompt id): jsonl text} for the sets after, before and tool."""
    files = {}
    for index, prompt_id in enumerate(AFTER, start=1):
        files[("after", prompt_id)] = turns(prompt_id, AFTER[prompt_id], prompt_id not in NO_SKILL, index)
        before_calls = (prompt_id not in NO_SKILL and prompt_id != "implicit-walk") or prompt_id in SKILL_CALLED_BEFORE
        files[("before", prompt_id)] = turns(prompt_id, BEFORE[prompt_id], before_calls, index)
    for index, (prompt_id, messages) in enumerate(TOOL.items(), start=1):
        files[("tool", prompt_id)] = tool_walk(prompt_id, messages, index)
    return {key: "".join(json.dumps(e, sort_keys=True) + "\n" for e in value) for key, value in files.items()}


def write(directory=FIXTURES):
    for (name, prompt_id), text in build().items():
        target = Path(directory) / name / f"{prompt_id}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write()
    print(f"wrote {2 * len(AFTER) + len(TOOL)} traces under {FIXTURES}", file=sys.stderr)
