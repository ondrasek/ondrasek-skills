"""A stand-in for `claude -p`: prints an invented stream-json trace and, like a real agent, changes files in its working directory.

STUB_MODE (environment): good (default) | edit-early (edits CLAUDE.md in turn 1) | touch-agents (also edits AGENTS.md in turn 2).
The runner passes EVAL_TURN; the stub takes the prompt and every other argument as input it ignores.
"""
import json
import os
import sys
from pathlib import Path

import helpers

mode = os.environ.get("STUB_MODE", "good")
turn = os.environ.get("EVAL_TURN", "1")
events = []
if turn == "1":
    events = [helpers.init()] + [helpers.call(name, **tool_input) for name, tool_input in helpers.DEFAULT_CALLS]
    if mode == "edit-early":
        Path("CLAUDE.md").write_text("changed before approval\n", encoding="utf-8")
        events.append(helpers.call("Write", file_path="CLAUDE.md"))
    events.append(helpers.result(helpers.proposal_reply()))
else:
    Path("CLAUDE.md").write_text(helpers.SHORT_CLAUDE, encoding="utf-8")
    if mode == "touch-agents":
        with open("AGENTS.md", "a", encoding="utf-8") as handle:
            handle.write("- added by the stub\n")
    events = [helpers.init(), helpers.call("Edit", file_path="CLAUDE.md"), helpers.result("CLAUDE.md: 229 → 3 lines.")]
for event in events:
    print(json.dumps(event))
sys.exit(0)
