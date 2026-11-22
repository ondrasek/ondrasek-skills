"""The case table: one row per test prompt.

cases/cases.csv columns:
    id        unique name; the trace is saved as artifacts/<id>.jsonl, the before/after repository snapshots as <id>.state.json
    fixture   synced | dirty | behind | env | no-remote: the repository the agent starts in (see fixtures.py)
    expect    saved | merged | saved-no-env | saved-local | untouched: which checks apply (see checks.BY_EXPECT)
    edits     the file the prompt makes the agent change ("" for a read-only prompt); its change must end up committed
    prompt    the only user message. It never mentions git, commits, saving or pushing: the Stop hook's report is the only thing
              in the run that asks for a commit (whether user-level memory also loads is unconfirmed).
"""
import csv
import re
from dataclasses import dataclass

import fixtures

EXPECTS = ("saved", "merged", "saved-no-env", "saved-local", "untouched")
# the fixture an expect is written for (the checks assume its topology); `saved` needs any fixture that has a remote
NEEDS = {"merged": "behind", "saved-no-env": "env", "saved-local": "no-remote", "untouched": "synced"}
COLUMNS = ("id", "fixture", "expect", "edits", "prompt")


@dataclass(frozen=True)
class Row:
    id: str
    fixture: str
    expect: str
    edits: str
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
            if raw["expect"] in NEEDS and raw["fixture"] != NEEDS[raw["expect"]]:
                raise ValueError(f"{where}: expect={raw['expect']} needs the {NEEDS[raw['expect']]} fixture")
            if raw["expect"] == "saved" and not fixtures.FIXTURES[raw["fixture"]].remote:
                raise ValueError(f"{where}: expect=saved needs a fixture with a remote")
            if raw["edits"] and raw["edits"] not in fixtures.build(raw["fixture"]):
                raise ValueError(f"{where}: edits names {raw['edits']}, which the {raw['fixture']} fixture does not have")
            if raw["expect"] == "untouched" and raw["edits"]:
                raise ValueError(f"{where}: expect=untouched goes with a read-only prompt")
            if not raw["prompt"].strip():
                raise ValueError(f"{where}: the prompt is empty")
            rows.append(Row(raw["id"], raw["fixture"], raw["expect"], raw["edits"], raw["prompt"]))
    return rows
