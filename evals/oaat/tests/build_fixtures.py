"""Builds the INVENTED fixture traces in tests/fixtures/traces/{after,before}/<id>.jsonl.

Nothing here comes from a model. Every event is written by hand in the stream-json shape (see python/agent_trace.py), marked by
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


def build():
    """{(set, prompt id): jsonl text} for both sets."""
    files = {}
    for index, prompt_id in enumerate(AFTER, start=1):
        files[("after", prompt_id)] = turns(prompt_id, AFTER[prompt_id], prompt_id not in NO_SKILL, index)
        before_calls = (prompt_id not in NO_SKILL and prompt_id != "implicit-walk") or prompt_id in SKILL_CALLED_BEFORE
        files[("before", prompt_id)] = turns(prompt_id, BEFORE[prompt_id], before_calls, index)
    return {key: "".join(json.dumps(e, sort_keys=True) + "\n" for e in value) for key, value in files.items()}


def write(directory=FIXTURES):
    for (name, prompt_id), text in build().items():
        target = Path(directory) / name / f"{prompt_id}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write()
    print(f"wrote {2 * len(AFTER)} traces under {FIXTURES}", file=sys.stderr)
