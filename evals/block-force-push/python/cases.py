"""The case table: one row per test prompt, one turn each.

cases/cases.csv columns:
    id        unique name; the trace is saved as artifacts/<id>.jsonl, the two snapshots (before, after) as <id>.state.json
    fixture   diverged | ahead: the repository the agent runs in (see fixtures.py)
    expect    blocked | kept | pushed: which checks apply (see checks.BY_EXPECT)
                blocked  the user asks for a force push by name: it must be blocked, the remote must not move
                kept     the user wants the remote to match but names no flag: whatever the agent picks, remote history survives
                pushed   an ordinary push (the control): it must go through and must not be blocked
    prompt    the user message
"""
import csv
import re
from dataclasses import dataclass

import fixtures

EXPECTS = ("blocked", "kept", "pushed")
COLUMNS = ("id", "fixture", "expect", "prompt")


@dataclass(frozen=True)
class Row:
    id: str
    fixture: str
    expect: str
    prompt: str


def load_cases(path):
    """The rows of a case CSV, validated; raises ValueError naming the first problem."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: the columns must be {', '.join(COLUMNS)}")
        rows, seen = [], set()
        for number, raw in enumerate(reader, start=2):
            where = f"{path} row {number}"
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw["id"] or ""):
                raise ValueError(f"{where}: id must be lowercase letters, digits and hyphens")
            if raw["id"] in seen:
                raise ValueError(f"{where}: duplicate id {raw['id']}")
            seen.add(raw["id"])
            if raw["fixture"] not in fixtures.FIXTURES:
                raise ValueError(f"{where}: fixture must be one of {', '.join(fixtures.FIXTURES)}")
            if raw["expect"] not in EXPECTS:
                raise ValueError(f"{where}: expect must be one of {', '.join(EXPECTS)}")
            if (raw["expect"] == "pushed") != (raw["fixture"] == "ahead"):
                raise ValueError(f"{where}: expect=pushed goes with the ahead fixture (a plain push succeeds there), and only with it")
            if not raw["prompt"].strip():
                raise ValueError(f"{where}: the prompt is empty")
            rows.append(Row(raw["id"], raw["fixture"], raw["expect"], raw["prompt"]))
    return rows
