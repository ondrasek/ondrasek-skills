"""A stand-in for the judge model: it reads the grading prompt (last argument) and prints a verdict that follows the schema.

STUB_JUDGE_MODE: pass (default), fail, fenced (a pass inside a code fence and a sentence), garbage (no JSON),
badschema (a score of 101), wrongids (check ids that are not the criteria), crash (exit 3 without output).
"""
import json
import os
import re
import sys


def criteria_ids(prompt):
    ids, inside = [], False
    for line in prompt.splitlines():
        if line.startswith("Criteria"):
            inside = True
        elif inside and line.startswith("- "):
            ids.append(line[2:].split(":", 1)[0])
        elif inside and not line.strip():
            break
    return ids


def main():
    mode = os.environ.get("STUB_JUDGE_MODE", "pass")
    prompt = sys.argv[-1]
    ids = criteria_ids(prompt)
    ok = mode != "fail"
    verdict = {"overall_pass": ok, "score": 95 if ok else 40,
               "checks": [{"id": i, "pass": ok, "notes": "stub verdict (invented)"} for i in ids]}
    if mode == "garbage":
        print("I think the reply is fine.")
    elif mode == "fenced":
        print("Here is my verdict:\n```json\n" + json.dumps(verdict) + "\n```\nDone.")
    elif mode == "badschema":
        verdict["score"] = 101
        print(json.dumps(verdict))
    elif mode == "wrongids":
        verdict["checks"] = [{"id": "invented-id", "pass": True, "notes": "x"}]
        print(json.dumps(verdict))
    elif mode == "crash":
        sys.exit(3)
    else:
        print(json.dumps(verdict))


if __name__ == "__main__":
    main()
