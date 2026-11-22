"""Builders of invented traces and snapshots. No model call, no network.

`good(expect, **override)` returns (row, events, states) of a run that passes every check for that `expect`; each override breaks
one thing, so a test can show that a given check fails (see test_checks.MUTATIONS). The shapes follow Claude Code 2.1.319 traces
(system/init with a `plugins` list of {name, source}, a Skill call with input {"skill": "plugin:skill"}, result events) except the
Bash tool_result, whose real shape is not evidenced: it is written as a plain string, the form the Skill result has.
"""
import hashlib
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import cases  # noqa: E402
import fixtures  # noqa: E402
import state  # noqa: E402

SKILL = "sitrep:sitrep"
ROOT, WS, TMP = "/r", "/r/ws", "/r/tmp"
DIR = "/r/tmp/sitrep.Ab12Cd"
PLUGIN = "/p"
SCRIPT = f"{PLUGIN}/skills/sitrep/scripts/report.sh"
NEW = f'"{SCRIPT}" new'
CLEAN = f'"{SCRIPT}" clean {DIR}'
TEMPLATE = "<html>the template</html>\n"
TEMPLATE_SHA = hashlib.sha256(TEMPLATE.encode()).hexdigest()
TRACKER = ".cache/milestones.json"
GOOD_REPLY = (f"The report is ready: {DIR}/index.html\n\nI could not open it here, so run this yourself:\n\n! open \"{DIR}/index.html\"\n\n"
              "Two decisions block the rest; start with the database. Write comments on items in the page and press Copy JSON to paste them back here.")
LISTED_REPLY = "Here are the items:\n1. Pick the database\n2. SSO vendor\n3. Licence audit\n4. Data retention\n5. Schema migration"
_ids = itertools.count(1)


def item(item_id, title, detail, kind="decision", status="open", depends=(), impact=3, urgency=2):
    return {"id": item_id, "type": kind, "title": title, "detail": detail, "impact": impact, "urgency": urgency, "status": status,
            "dependsOn": list(depends)}


def good_data(ms=False):
    """A data object that follows schema.md and holds the five planted items, the dependency and, with ms, the tracker's milestones."""
    items = [item("db", "Pick the database", "Postgres or SQLite for the sync service."),
             item("sso", "SSO vendor", "Choose between the two shortlisted vendors."),
             item("fonts", "Font licences", "Licence audit of bundled fonts.", kind="risk", impact=2),
             item("retention", "Data retention", "The retention period is unanswered.", kind="question", urgency=1),
             item("migration", "Schema migration", "Cannot start until the database is chosen.", kind="blocker", status="blocked", depends=["db"])]
    data = {"title": "Sync service: open items", "summary": "Two decisions block the rest.", "generated": "2026-11-22 10:00",
            "next": {"title": "Decide on the database", "why": "Unblocks the migration."}, "items": items}
    if ms:
        items.append(item("ms-3", "Release sign-off", "Waiting for the signing key from legal.", kind="blocker", status="blocked", urgency=3))
        data["milestones"] = {"title": "To release 1.0", "done": 1, "total": 4, "current": "Storage layer", "needsYou": 1, "blocked": 1}
    return data


def data_js(data):
    """The text of a data.js: a comment (the reader must skip it) and window.SITREP = {...};"""
    return "// written by the agent\nwindow.SITREP = " + json.dumps(data, indent=2) + ";\n"


def init(plugins=("sitrep",), skills=(SKILL,), session="stub-session", path=PLUGIN):
    """`path` is the directory every listed plugin claims (init plugins[] carries a `path`, seen in recorded traces)."""
    return {"type": "system", "subtype": "init", "session_id": session, "skills": list(skills),
            "plugins": [{"name": n, "path": path, "source": f"{n}@inline"} for n in plugins] + [{"name": "cc-plugin-x", "path": "builtin", "source": "cc-plugin-x@builtin"}]}


def tool_events(name, tool_input, output=None, mid=None):
    """The assistant tool_use event of a call and, when it has an output, the user event holding its tool_result."""
    call_id = f"toolu_{next(_ids)}"
    block = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    events = [{"type": "assistant", "parent_tool_use_id": None, "message": {"id": mid or f"m{next(_ids)}", "content": [block]}}]
    if output is not None:
        events.append({"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": output}]}})
    return events


def calls(entries):
    """Events of [(name, input) or (name, input, output)]."""
    return [e for entry in entries for e in tool_events(*entry)]


def result(text, session="stub-session", denials=(), subtype="success"):
    return {"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": text, "num_turns": 3, "total_cost_usd": 0.5,
            "duration_ms": 1000, "session_id": session, "permission_denials": list(denials)}


REPORT_CALLS = [("Skill", {"skill": SKILL}), ("Bash", {"command": NEW}, DIR + "\n"), ("Write", {"file_path": DIR + "/data.js", "content": "..."})]
CLEAN_CALLS = [("Bash", {"command": CLEAN}, f"deleted {DIR}\n")]
PLAIN_READ = [("Read", {"file_path": "/r/ws/docs/open-questions.md"})]
FIXTURE_OF = {"none": "bare", "feedback": "open-items", "report": "open-items", "report-clean": "open-items", "report-ms": "open-items-ms"}


def good(expect, plugins=("sitrep",), skills=(SKILL,), calls1=None, calls2=None, reply1=None, denials=(), data=None, index=TEMPLATE,
         extra_files=None, report_dir=DIR, report_found=True, report_patch=None, after1_extra=None, tmp_sitrep=None, report2_exists=False,
         should_trigger=True, result_subtype="success", plugin_path=PLUGIN, omit_data=False, result2_subtype="success", denials2=(), turn2=True):
    """A passing run for `expect`. `data` is a dict (written as data.js) or raw text; `report_patch` overrides fields of the report record;
    `result_subtype` other than success ends turn 1 with an error result; `plugin_path` is where init says the plugin is (the run's
    --plugin-dir is PLUGIN); `omit_data` leaves data.js out of the report directory. For `report-clean`: `result2_subtype` and `denials2`
    belong to the second turn's result event, and `turn2=False` leaves the second turn out (no events, no snapshot after it)."""
    row = cases.Row(f"t-{expect}", expect != "none" and should_trigger, "explicit", FIXTURE_OF[expect], expect, "p", "done" if expect == "report-clean" else "")
    has_report = expect.startswith("report")
    if calls1 is None:
        calls1 = REPORT_CALLS if has_report else [("Skill", {"skill": SKILL})] if expect == "feedback" else PLAIN_READ
    reply1 = reply1 if reply1 is not None else GOOD_REPLY if has_report else "Done."
    events = [init(plugins, skills, path=plugin_path)] + calls(calls1) + [result(reply1, denials=denials, subtype=result_subtype)]
    if expect == "report-clean" and turn2:
        events += [init(plugins, skills, path=plugin_path)] + calls(CLEAN_CALLS if calls2 is None else calls2) + [result("Deleted the report.", denials=denials2, subtype=result2_subtype)]
    original = fixtures.build(row.fixture)
    snapshot = state.snapshot_from(original, head="h1")
    states = {"layout": {"root": ROOT, "ws": WS, "tmp": TMP}, "plugin": PLUGIN, "before": snapshot, "after2": None, "report2": None,
              "after1": state.snapshot_from({**original, **(after1_extra or {})}, head="h1"),
              "tmp_sitrep": tmp_sitrep if tmp_sitrep is not None else [Path(DIR).name] if has_report else []}
    contents = {"index.html": index.encode(), "data.js": (data if isinstance(data, str) else data_js(data or good_data(expect == "report-ms"))).encode(),
                **{name: text.encode() for name, text in (extra_files or {}).items()}}
    if omit_data:
        del contents["data.js"]
    record = state.report_record(report_dir, contents, TEMPLATE_SHA, "trace") if has_report and report_found else state.report_record(None, None, TEMPLATE_SHA, "not found")
    states["report1"] = {**record, **(report_patch or {})}
    if expect == "report-clean" and turn2:
        states["after2"] = state.snapshot_from(original, head="h1")
        states["report2"] = state.report_record(report_dir, contents if report2_exists else None, TEMPLATE_SHA)
    return row, events, states


def schema_example():
    """The full JavaScript example of schema.md, as written there (comments and bare keys included)."""
    text = (Path(__file__).resolve().parents[3] / "plugins" / "sitrep" / "skills" / "sitrep" / "schema.md").read_text(encoding="utf-8")
    return text.split("```js\n", 1)[1].split("\n```", 1)[0]
