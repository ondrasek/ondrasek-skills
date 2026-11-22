"""A stand-in for `claude -p`: prints an invented stream-json trace and, like a real agent, changes files in its working directory.

The case is EVAL_PROMPT_ID, which the test CSV names after the `expect` it exercises. STUB_MODE (environment) picks what goes wrong, and
only in the case it concerns; every other case behaves well:

    good (default)    does the case right, calling the real milestones.py found from the --plugin-dir argument, in its cwd
    hand-edit         update: writes .cache/milestones.json itself with the Write tool, no timestamps
    wrong-item        update: finishes item 2 and starts item 4 instead of item 3
    commits-tracker   update: force-adds the tracker and commits it
    invents-update    reminded-quiet: finishes item 2 although nothing happened
    ignores-reminder  reminded-update: the hook fires and its report arrives, but the agent updates nothing
    no-marker         reminded-update: the hook never runs, and the agent updates nothing
    init-only         none: the run prints the init event and stops (an auth failure, a kill after start)
    budget-stop       none: init, then an error result (error_max_budget_usd, is_error)
    crash             none: a complete good trace, but the process exits 3

The script runs by subprocess, never imported (an import would write __pycache__ under plugins/), with every MILESTONES_* and CLAUDE_*
variable removed from its environment, so that it can only act on the project of its cwd.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import helpers

mode = os.environ.get("STUB_MODE", "good")
case = os.environ["EVAL_PROMPT_ID"]
plugin = Path(sys.argv[sys.argv.index("--plugin-dir") + 1])
script = plugin / "skills" / "milestones" / "scripts" / "milestones.py"
env = {k: v for k, v in os.environ.items() if not k.startswith(("MILESTONES_", "CLAUDE_"))}
events = [helpers.init()]
if case == "none" and mode == "init-only":
    print(json.dumps(events[0]))
    sys.exit(0)
if case == "none" and mode == "budget-stop":
    events.append({**helpers.result(""), "subtype": "error_max_budget_usd", "is_error": True})
    for event in events:
        print(json.dumps(event))
    sys.exit(0)


def run(argv, shown):
    event = helpers.call("Bash", command=shown)
    events.append(event)
    done = subprocess.run(argv, check=True, capture_output=True, text=True, env=env)
    events.append(helpers.tool_result(event["message"]["content"][0]["id"], done.stdout))


def milestones(*args):
    run([sys.executable, str(script), *args], f'python3 "{script}" ' + " ".join(shlex.quote(a) for a in args))


def skill():
    events.append(helpers.call("Skill", skill=helpers.SKILL_ID))


def edit_textutil():
    events.append(helpers.call("Edit", file_path="src/textutil.py"))
    with open("src/textutil.py", "a", encoding="utf-8") as handle:
        handle.write("\n\ndef slugify(text):\n    return text.lower()\n")


def reminder():
    """The Stop hook's side: the marker it touches and the report that reaches the model."""
    Path(".cache").mkdir(exist_ok=True)
    Path(".cache/.milestones-reminded").touch()
    events.append(helpers.user_text(helpers.HOOK_REPORT))


reply = helpers.REPLY[case]
if case == "show":
    skill()
    milestones("show")
elif case == "update":
    skill()
    if mode == "hand-edit":
        tracker = json.loads(Path(".cache/milestones.json").read_text(encoding="utf-8"))
        tracker["items"][1]["state"], tracker["items"][2]["state"] = "done", "doing"
        events.append(helpers.call("Write", file_path=".cache/milestones.json"))
        Path(".cache/milestones.json").write_text(json.dumps(tracker, indent=2) + "\n", encoding="utf-8")
    else:
        milestones("set", "2", "--state", "done")
        milestones("set", "4" if mode == "wrong-item" else "3", "--state", "doing")
    if mode == "commits-tracker":
        run(["git", "add", "-f", ".cache/milestones.json"], "git add -f .cache/milestones.json")
        run(["git", "commit", "-q", "-m", "tracker"], "git commit -m tracker")
elif case == "create":
    skill()
    milestones("init", "--empty", "--title", "Importer")
    for name in ("Design the schema", "Build the importer", "Write the migration guide"):
        milestones("add", "--name", name)
elif case == "reminded-update":
    edit_textutil()
    if mode != "no-marker":
        reminder()
    if mode not in ("no-marker", "ignores-reminder"):
        skill()
        milestones("set", "2", "--state", "done", "--left", "")
elif case == "reminded-quiet":
    reminder()
    if mode == "invents-update":
        skill()
        milestones("set", "2", "--state", "done")
events.append(helpers.result(reply))
for event in events:
    print(json.dumps(event))
sys.exit(3 if case == "none" and mode == "crash" else 0)
