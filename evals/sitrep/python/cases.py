"""The case table: one row per test prompt.

cases/cases.csv columns:
    id              unique name; the trace is saved as artifacts/<id>.jsonl, the snapshots as <id>.state.json
    should_trigger  true or false: must the agent call the Skill tool for this prompt?
    kind            explicit | implicit | milestones | feedback | negative (what the case tests)
    fixture         open-items | open-items-ms | bare: the workspace the agent runs in (see fixtures.py)
    expect          which checks apply (see checks.BY_EXPECT):
                      none          no skill call, no report anywhere
                      report        a valid report is built outside the project; the project is untouched
                      report-clean  report, then (second turn) the report directory is removed by report.sh
                      report-ms     report, plus the milestone tracker is read, mapped into the data and left untouched
                      feedback      pasted Copy-JSON feedback: the skill is called, the project is untouched
    prompt          the first user message
    reply           only for expect=report-clean: the user's second message, sent with --resume to the same session
"""
import csv
import re
from dataclasses import dataclass

import fixtures

EXPECTS = ("none", "report", "report-clean", "report-ms", "feedback")
KINDS = ("explicit", "implicit", "milestones", "feedback", "negative")
COLUMNS = ("id", "should_trigger", "kind", "fixture", "expect", "prompt")
OPTIONAL_COLUMNS = ("reply",)


@dataclass(frozen=True)
class Row:
    id: str
    should_trigger: bool
    kind: str
    fixture: str
    expect: str
    prompt: str
    reply: str = ""


def load_cases(path):
    """The rows of a case CSV, validated; raises ValueError naming the first problem."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) not in (COLUMNS, COLUMNS + OPTIONAL_COLUMNS):
            raise ValueError(f"{path}: the columns must be {', '.join(COLUMNS)}, optionally followed by {', '.join(OPTIONAL_COLUMNS)}")
        rows, seen = [], set()
        for number, raw in enumerate(reader, start=2):
            where = f"{path} row {number}"
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw["id"] or ""):
                raise ValueError(f"{where}: id must be lowercase letters, digits and hyphens")
            if raw["id"] in seen:
                raise ValueError(f"{where}: duplicate id {raw['id']}")
            seen.add(raw["id"])
            if raw["should_trigger"] not in ("true", "false"):
                raise ValueError(f"{where}: should_trigger must be true or false")
            if raw["kind"] not in KINDS:
                raise ValueError(f"{where}: kind must be one of {', '.join(KINDS)}")
            if raw["fixture"] not in fixtures.FIXTURES:
                raise ValueError(f"{where}: fixture must be one of {', '.join(fixtures.FIXTURES)}")
            if raw["expect"] not in EXPECTS:
                raise ValueError(f"{where}: expect must be one of {', '.join(EXPECTS)}")
            trigger = raw["should_trigger"] == "true"
            if trigger == (raw["expect"] == "none"):
                raise ValueError(f"{where}: expect=none goes with should_trigger=false, and only with it")
            if (raw["expect"] == "report-ms") != (raw["fixture"] == "open-items-ms"):
                raise ValueError(f"{where}: expect=report-ms goes with the open-items-ms fixture, and only with it")
            reply = (raw.get("reply") or "").strip()
            if (raw["expect"] == "report-clean") != bool(reply):
                raise ValueError(f"{where}: a reply is required for expect=report-clean and not allowed otherwise")
            if not raw["prompt"].strip():
                raise ValueError(f"{where}: the prompt is empty")
            rows.append(Row(raw["id"], trigger, raw["kind"], raw["fixture"], raw["expect"], raw["prompt"], reply))
    return rows
