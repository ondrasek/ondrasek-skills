"""A stand-in for `claude -p ... --output-format stream-json --verbose`: it prints a recorded trace and calls no model.

Environment (set by run_evals.py and by the tests):
    EVAL_PROMPT_ID, EVAL_TRIAL   which prompt and trial the runner is on (run_evals.py sets both)
    EVAL_TURN                    1 or 2: a two-turn trace is printed in halves, split after its first result event
    STUB_TRACES                  directory with <id>.jsonl traces to print
    STUB_ALT                     optional second directory; used for every even trial (a flaky agent)
    STUB_EXIT                    exit code to finish with (default 0)
    STUB_SAVE_ARGS               optional file; the stub writes its argument list there as JSON
"""
import json
import os
import sys
from pathlib import Path


def main():
    if os.environ.get("STUB_SAVE_ARGS"):
        Path(os.environ["STUB_SAVE_ARGS"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    prompt_id, trial = os.environ["EVAL_PROMPT_ID"], int(os.environ.get("EVAL_TRIAL", "1"))
    directory = os.environ["STUB_TRACES"]
    if os.environ.get("STUB_ALT") and trial % 2 == 0:
        directory = os.environ["STUB_ALT"]
    path = Path(directory) / f"{prompt_id}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines) if json.loads(line).get("type") == "result"), len(lines) - 1)
    if first < len(lines) - 1:   # two turns recorded: the first call prints the first, the second call the rest
        lines = lines[first + 1:] if os.environ.get("EVAL_TURN") == "2" else lines[:first + 1]
    sys.stdout.write("".join(lines))
    sys.exit(int(os.environ.get("STUB_EXIT", "0")))


if __name__ == "__main__":
    main()
