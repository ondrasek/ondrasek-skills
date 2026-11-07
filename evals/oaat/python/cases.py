"""The prompt table: one row per test prompt, and the pending items that a prompt carries.

evals/prompts.csv columns:
    id              unique name; the trace is saved as artifacts/<id>.jsonl
    should_trigger  true or false: must the agent call the Skill tool for this prompt?
    kind            explicit | implicit | contextual | followup | negative (what the prompt tests)
    expect          item | skip | stop | tangent | list | none (which checks apply to the reply)
    prompt          the text sent to the agent; a triggering prompt holds a fixture block of pending items
    reply           optional: the user's second message. A row with a reply runs in two turns: the agent answers `prompt`,
                    then `--resume` sends `reply` to the same session, as in a real conversation with the skill already loaded

The block of pending items has one fixed shape, so the checks read the item titles from the prompt itself:

    Pending items:
    - <title>: <one sentence>
    - <title>: <one sentence>
    <blank line>
"""
import csv
import re
from dataclasses import dataclass

EXPECTS = ("item", "skip", "stop", "tangent", "list", "none")
KINDS = ("explicit", "implicit", "contextual", "followup", "negative")
COLUMNS = ("id", "should_trigger", "kind", "expect", "prompt")
OPTIONAL_COLUMNS = ("reply",)
HEADER_RE = re.compile(r"item\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    id: str
    should_trigger: bool
    kind: str
    expect: str
    prompt: str
    reply: str = ""


def load_prompts(path):
    """The rows of a prompts CSV, validated; raises ValueError naming the first problem."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) not in (COLUMNS, COLUMNS + OPTIONAL_COLUMNS):
            raise ValueError(f"{path}: the columns must be {', '.join(COLUMNS)}, optionally followed by {', '.join(OPTIONAL_COLUMNS)}")
        rows, seen = [], set()
        for number, raw in enumerate(reader, start=2):
            where = f"{path} row {number}"
            if not raw["id"] or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw["id"]):
                raise ValueError(f"{where}: id must be lowercase letters, digits and hyphens")
            if raw["id"] in seen:
                raise ValueError(f"{where}: duplicate id {raw['id']}")
            seen.add(raw["id"])
            if raw["should_trigger"] not in ("true", "false"):
                raise ValueError(f"{where}: should_trigger must be true or false")
            if raw["kind"] not in KINDS:
                raise ValueError(f"{where}: kind must be one of {', '.join(KINDS)}")
            if raw["expect"] not in EXPECTS:
                raise ValueError(f"{where}: expect must be one of {', '.join(EXPECTS)}")
            if not raw["prompt"].strip():
                raise ValueError(f"{where}: the prompt is empty")
            rows.append(Row(raw["id"], raw["should_trigger"] == "true", raw["kind"], raw["expect"], raw["prompt"], (raw.get("reply") or "").strip()))
    return rows


def pending_titles(prompt):
    """The titles of the pending items in a prompt's fixture block (empty list when it has none)."""
    titles, inside = [], False
    for line in prompt.splitlines():
        if line.strip().lower() == "pending items:":
            inside = True
            continue
        if inside:
            match = re.match(r"\s*-\s+(.+?):\s", line + " ")
            if not match:
                break
            titles.append(match.group(1).strip())
    return titles


def previous_header_number(prompt):
    """The N of the last 'Item N of M' that the prompt quotes from an earlier turn, or 0 when it quotes none."""
    found = HEADER_RE.findall(prompt)
    return int(found[-1][0]) if found else 0


def previous_header_title(prompt):
    """The title in the last quoted 'Item N of M, <type>: <title>' header of the prompt, or '' when it quotes none."""
    found = re.findall(r"item\s+\d+\s+of\s+\d+[^:\n]*:\s*([^*\n]+)", prompt, re.IGNORECASE)
    return found[-1].strip() if found else ""
