"""The case table: one row per test prompt.

cases/cases.csv columns:
    id        unique name; the trace is saved as artifacts/<id>.jsonl, the two workspace snapshots as <id>.state.json
    expect    what the run should do, and so which checks apply (see checks.run_checks):
                show             answer a status question with the script's table; change nothing
                update           turn a sentence into tracker state, through the script
                create           start a tracker on a repository that has none
                none             not a tracker task: the skill must not trigger
                reminded-update  a stale tracker and finished work: the Stop reminder must lead to an update
                reminded-quiet   a stale tracker and nothing finished: the reminder must lead to no change
    fixture   bare | tracker | stale-task | stale-idle: the workspace the agent runs in (see fixtures.py)
    want      what the tracker must hold afterwards, comma separated tokens (empty for show, none and reminded-quiet):
                ID=STATE         item ID has that state (done, doing, todo, blocked)
                ID.needs=set     item ID has a non-empty `needs`
                ID.needs=you     item ID's `needs` is exactly "you" or "author" (case aside): the values that set the statusline flag, as the
                                 plugin's own is_you() reads them; "me", "user" or "you (the key from legal)" do not (the prompt says
                                 "waiting on me")
                title=TEXT       the tracker's title
                count=N          the number of items
                names=a|b|c      every word occurs in some item name (case-insensitive)
    prompt    the user message; it never mentions git, commits or (in the reminder cases) the tracker
"""
import csv
import re
from dataclasses import dataclass

import fixtures

EXPECTS = ("show", "update", "create", "none", "reminded-update", "reminded-quiet")
COLUMNS = ("id", "expect", "fixture", "want", "prompt")
STATES = ("done", "doing", "todo", "blocked")
# the fixtures an expect needs: the reminder cases need the tracker aged by the fixture, create needs none to exist
FIXTURE_FOR = {"show": ("tracker",), "update": ("tracker",), "create": ("bare",), "reminded-update": ("stale-task",), "reminded-quiet": ("stale-idle",)}


@dataclass(frozen=True)
class Want:
    states: tuple = ()      # ((id, state), ...)
    needs: tuple = ()       # ids whose `needs` must be non-empty
    title: str = ""
    count: int = 0
    names: tuple = ()
    needs_you: tuple = ()   # ids whose `needs` must name the user

    def __bool__(self):
        return bool(self.states or self.needs or self.needs_you or self.title or self.count or self.names)


def parse_want(text):
    """The Want of a `want` cell; raises ValueError naming the first bad token."""
    states, needs, needs_you, names, title, count = [], [], [], (), "", 0
    for token in (t.strip() for t in text.split(",") if t.strip()):
        key, sep, value = token.partition("=")
        if not sep or not value:
            raise ValueError(f"want token {token!r} is not KEY=VALUE")
        if re.fullmatch(r"\d+", key) and value in STATES:
            states.append((int(key), value))
        elif re.fullmatch(r"\d+\.needs", key) and value in ("set", "you"):
            (needs if value == "set" else needs_you).append(int(key.split(".")[0]))
        elif key == "title" and not title:
            title = value
        elif key == "count" and not count and re.fullmatch(r"[1-9]\d*", value):
            count = int(value)
        elif key == "names" and not names:
            names = tuple(value.split("|"))
        else:
            raise ValueError(f"want token {token!r} is unknown, repeated or has a bad value (states: {', '.join(STATES)})")
    if len({i for i, _ in states}) != len(states) or len(set(needs + needs_you)) != len(needs + needs_you):
        raise ValueError(f"want {text!r} names an item twice")
    return Want(tuple(states), tuple(needs), title, count, names, tuple(needs_you))


@dataclass(frozen=True)
class Row:
    id: str
    expect: str
    fixture: str
    want: Want
    prompt: str

    @property
    def should_trigger(self):
        """True when the case must load the skill; the reminder cases do not grade the trigger."""
        return self.expect in ("show", "update", "create")


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
            if raw["expect"] not in EXPECTS:
                raise ValueError(f"{where}: expect must be one of {', '.join(EXPECTS)}")
            if raw["fixture"] not in fixtures.FIXTURES:
                raise ValueError(f"{where}: fixture must be one of {', '.join(fixtures.FIXTURES)}")
            if raw["expect"] in FIXTURE_FOR and raw["fixture"] not in FIXTURE_FOR[raw["expect"]]:
                raise ValueError(f"{where}: expect={raw['expect']} needs fixture {' or '.join(FIXTURE_FOR[raw['expect']])}")
            try:
                want = parse_want(raw["want"])
            except ValueError as error:
                raise ValueError(f"{where}: {error}") from None
            needed = {"update": bool(want.states or want.needs or want.needs_you), "reminded-update": bool(want.states),
                      "create": bool(want.title and want.count and want.names)}.get(raw["expect"], not want)
            if not needed:
                raise ValueError(f"{where}: the want column does not fit expect={raw['expect']} (see cases.py)")
            if not raw["prompt"].strip():
                raise ValueError(f"{where}: the prompt is empty")
            rows.append(Row(raw["id"], raw["expect"], raw["fixture"], want, raw["prompt"]))
    return rows
