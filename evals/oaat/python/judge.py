"""The rubric pass: a second model call grades what regular expressions cannot (meaning, tone, a paraphrased preview).

    judge.py ID TRACE.jsonl                              print the grading prompt for one trace (offline, no model call)
    judge.py ID TRACE.jsonl --judge-cmd "claude -p {prompt}"   send it to the judge command and print the validated verdict

The judge command is a parameter. It receives the grading prompt as its last argument (or in place of `{prompt}`) and
prints one JSON object on stdout. `validate` checks that object against rubric.schema.json with a small validator
(standard library only; it supports exactly the keywords that the schema file uses and refuses any other).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import cases
import agent_trace as trace
import runner_cmd

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "rubric.schema.json"

CRITERIA = {
    "item": [
        ("one-item", "The reply presents exactly one pending item. It names no other pending item, not even in other words, and does not say what comes next."),
        ("shape", "The reply has the six parts in order: the 'Item N of M, type' header with a short title, What it is, Why it matters, Options (at most 3, numbered 1, 2, 3, each with its trade-off), Recommendation (one line of its own after the last option), One question. It stays short."),
        ("question", "The reply ends with one concrete question that the user can answer in a few words."),
        ("recommendation", "The reply recommends one option and says why in a few words, and it leaves the decision to the user."),
        ("plain-words", "The sentences are short and plain, with no background story and no wall of text."),
    ],
    "stop": [
        ("wrapup-content", "The wrap-up lists each decision made, each item parked, and names one next action."),
        ("stops-at-once", "The reply presents no new item and asks no new question."),
        ("plain-words", "The sentences are short and plain."),
    ],
    "tangent": [
        ("flags-tangent", "The reply says the request may be a tangent from the current item and asks whether to continue or refocus."),
        ("parks-it", "The reply does not follow the tangent (no long answer to it) and does not present a new item."),
        ("plain-words", "The sentences are short and plain."),
    ],
    "list": [
        ("gives-list", "The reply gives the user the full list the user asked for."),
        ("no-one-at-a-time", "The reply does not switch to one-item-at-a-time mode."),
    ],
    "none": [
        ("answers-directly", "The reply answers the user's question directly."),
        ("no-one-at-a-time", "The reply does not use the one-item-at-a-time format."),
    ],
}
CRITERIA["skip"] = CRITERIA["item"]


def criteria_for(expect):
    return CRITERIA[expect]


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def build_prompt(row, reply, skill="oaat", skill_text=None):
    """The grading prompt: the rubric, the user's prompt, the agent's reply, the schema, and optionally the skill text."""
    lines = [
        f"You grade one reply of an AI agent that has a skill named {skill}.",
        "Grade only against the criteria below. Judge meaning, not wording. Do not reward a reply for being long.",
        "",
    ]
    if skill_text:
        lines += [f"The skill text (the behaviour under test):", "<skill>", skill_text.strip(), "</skill>", ""]
    lines += ["Criteria (one check per line, use these ids):"]
    lines += [f"- {cid}: {text}" for cid, text in criteria_for(row.expect)]
    lines += ["", "The user's message to the agent:", "<user_message>", row.prompt.strip(), "</user_message>", ""]
    if row.reply:
        lines += ["The agent answered it, then the user wrote this second message, and the reply below answers the second one:",
                  "<user_second_message>", row.reply.strip(), "</user_second_message>", ""]
    lines += ["The agent's reply:", "<reply>", (reply or "(empty reply)").strip(), "</reply>", "",
              "Answer with one JSON object and nothing else. It must match this JSON Schema, and `checks` must hold one entry per criterion id:",
              json.dumps(load_schema(), indent=2)]
    return "\n".join(lines)


def extract_json(text):
    """The first JSON object in a model reply (the reply may wrap it in a code fence or add a sentence)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("the judge reply holds no JSON object")


def validate(value, schema, path="$"):
    """Errors of `value` against the schema; [] when it conforms. Supports type, properties, required, additionalProperties,
    items, minItems, minimum, maximum; any other keyword in the schema (besides annotations) raises ValueError."""
    known = {"type", "properties", "required", "additionalProperties", "items", "minItems", "minimum", "maximum"}
    notes = {"$schema", "title", "description"}
    unknown = set(schema) - known - notes
    if unknown:
        raise ValueError(f"validator does not support: {', '.join(sorted(unknown))}")
    errors, kind = [], schema.get("type")
    py = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if kind:
        ok = isinstance(value, py[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {kind}, got {type(value).__name__}"]
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing {name}")
        props = schema.get("properties", {})
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        for index, item in enumerate(value):
            if "items" in schema:
                errors += validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above {schema['maximum']}")
    return errors


def parse_verdict(text, expect):
    """(verdict dict, []) for a conforming reply, (None, errors) otherwise. The check ids must be the criteria ids."""
    try:
        verdict = extract_json(text)
    except ValueError as error:
        return None, [str(error)]
    errors = validate(verdict, load_schema())
    if not errors:
        want = [cid for cid, _ in criteria_for(expect)]
        got = [c["id"] for c in verdict["checks"]]
        if sorted(got) != sorted(want):
            errors.append(f"check ids {got} differ from the criteria {want}")
    return (None, errors) if errors else (verdict, [])


def grade(row, reply, judge_cmd, skill="oaat", skill_text=None, timeout=300):
    """Run the judge command on one reply. Returns (verdict or None, errors)."""
    prompt = build_prompt(row, reply, skill, skill_text)
    code, out, err = runner_cmd.run(runner_cmd.build(judge_cmd, prompt), timeout=timeout)
    if code != 0 and not out.strip():
        return None, [f"judge command exited {code}: {err.strip()[:200]}"]
    return parse_verdict(out, row.expect)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("id")
    parser.add_argument("trace")
    parser.add_argument("--prompts", default=str(HERE.parent / "evals" / "prompts.csv"))
    parser.add_argument("--judge-cmd")
    parser.add_argument("--skill", default="oaat")
    args = parser.parse_args(argv)
    rows = {r.id: r for r in cases.load_prompts(args.prompts)}
    if args.id not in rows:
        parser.error(f"no prompt with id {args.id}")
    reply = trace.final_reply(trace.load(args.trace))
    if not args.judge_cmd:
        print(build_prompt(rows[args.id], reply, args.skill))
        return 0
    verdict, errors = grade(rows[args.id], reply, args.judge_cmd, args.skill)
    if errors:
        print("judge reply invalid: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
