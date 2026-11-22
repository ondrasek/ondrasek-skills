"""A stand-in for `claude -p`: prints an invented stream-json trace and, like a real agent, does its work for real: it runs the plugin's
own report.sh in the TMPDIR it was given, writes data.js there, and in turn 2 cleans the report up.

STUB_MODE (environment): good (default) | no-report | writes-into-project | bad-data | edits-index | misses-items | rm-instead-of-clean |
leaves-report | pastes-list (a good run whose reply pastes the item list: advisory warning only) | exits-nonzero (a good run whose process exits 3) | crash (prints init, exits 1). The runner passes EVAL_PROMPT_ID (n*: negative, f*: feedback, m*: with milestones, else a plain report), EVAL_TURN and
TMPDIR; the plugin directory comes from `--plugin-dir`. Every other argument is input the stub ignores.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import helpers

mode = os.environ.get("STUB_MODE", "good")
turn = os.environ.get("EVAL_TURN", "1")
case = os.environ.get("EVAL_PROMPT_ID", "r")[:1]
tmp = os.environ.get("TMPDIR")
if not tmp:
    sys.exit("stub_agent: TMPDIR is not set: report.sh would sweep and fill the real temporary directory")
plugin = sys.argv[sys.argv.index("--plugin-dir") + 1]
script = Path(plugin) / "skills" / "sitrep" / "scripts" / "report.sh"
quoted = f'"{script}"'


def script_call(verb, *args):
    """Run report.sh for real, as a Bash call whose tool_result is its output; returns (events, output)."""
    done = subprocess.run([str(script), verb, *args], capture_output=True, text=True, check=True)
    return helpers.tool_events("Bash", {"command": " ".join([quoted, verb, *args])}, done.stdout), done.stdout.strip()


def reports():
    return sorted(Path(tmp).glob("sitrep.*"))


events = [helpers.init(path=plugin)]
if mode == "crash":
    # an agent that prints init and dies (auth error, budget cap, timeout): the run did not finish and nothing was done
    for event in events:
        print(json.dumps(event))
    sys.exit("stub_agent: crashed on purpose")
if turn == "1":
    if case == "n":
        events += helpers.tool_events("Read", {"file_path": "README.md"}) + [helpers.result("Done.")]
    elif case == "f" or mode == "no-report":
        events += helpers.tool_events("Skill", {"skill": helpers.SKILL}) + [helpers.result("Noted your answers.")]
        if case == "f" and mode == "writes-into-project":
            Path("notes.md").write_text("decisions\n", encoding="utf-8")
    else:
        events += helpers.tool_events("Skill", {"skill": helpers.SKILL})
        made, directory = script_call("new")
        events += made
        data = helpers.good_data(ms=case == "m")
        if mode == "misses-items":
            data["items"] = data["items"][:2]
        text = helpers.data_js(data)[:200] if mode == "bad-data" else helpers.data_js(data)
        (Path(directory) / "data.js").write_text(text, encoding="utf-8")
        events += helpers.tool_events("Write", {"file_path": f"{directory}/data.js", "content": text})
        if mode == "edits-index":
            with open(Path(directory) / "index.html", "a", encoding="utf-8") as handle:
                handle.write("<!-- edited by the stub -->\n")
        if mode == "writes-into-project":
            Path("sitrep.html").write_text("<html></html>\n", encoding="utf-8")
            events += helpers.tool_events("Write", {"file_path": "sitrep.html", "content": "<html></html>"})
        reply = helpers.GOOD_REPLY.replace(helpers.DIR, directory)
        if mode == "pastes-list":     # the five planted items pasted into chat: only the advisory reply check minds
            reply += "\n" + "\n".join(f"- {t}" for t in ("Pick the database", "SSO vendor", "Font licences", "Data retention", "Schema migration"))
        events.append(helpers.result(reply))
elif not reports():
    events.append(helpers.result("There is no report to clean."))
elif mode == "rm-instead-of-clean":
    directory = str(reports()[0])
    shutil.rmtree(directory)
    events += helpers.tool_events("Bash", {"command": f"rm -rf {directory}"}, "") + [helpers.result("Removed it.")]
elif mode == "leaves-report":
    events.append(helpers.result("Fine, I will leave it."))
else:
    cleaned, _ = script_call("clean", str(reports()[0]))
    events += cleaned + [helpers.result("Deleted the report.")]
for event in events:
    print(json.dumps(event))
# exits-nonzero: the whole trace is printed, success result included, and the process still exits 3
sys.exit(3 if mode == "exits-nonzero" else 0)
