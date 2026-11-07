"""The offline demo: grade the invented fixture traces, as the runner does for real traces. No key, no network, no model.

    python3 demo.py                 all four sections (what `make run` prints)
    python3 demo.py after           grade the traces of the edited skill: every prompt passes
    python3 demo.py before          grade the traces of the unedited skill: five prompts fail
    python3 demo.py real            grade the one recorded real trace: it fails two checks, each for a stated cause
    python3 demo.py compare         before against after, per prompt: better, worse or same
    python3 demo.py judge-prompt    the grading prompt that the rubric judge receives for one trace

`make grade-fixtures` writes each section to captures/. Paths in the output are relative to the unit directory.
"""
import io
import sys
import tempfile
from pathlib import Path

import judge
import report
import run_evals

UNIT = Path(__file__).resolve().parent.parent
PROMPTS = UNIT / "evals" / "prompts.csv"
TRACES = UNIT / "tests" / "fixtures" / "traces"
SETS = {"after": TRACES / "after", "before": TRACES / "before"}
SECTIONS = ("after", "before", "real", "compare", "judge-prompt")


def rel(path):
    return Path(path).relative_to(UNIT).as_posix()


def grade(name, out, results=None):
    args = ["--fixtures", str(SETS[name]), "--prompts", str(PROMPTS), "--label", name]
    if results:
        args += ["--results", str(results)]
    print(f"$ python3 python/run_evals.py --fixtures {rel(SETS[name])} --label {name}", file=out)
    code = run_evals.main(args, stream=out)
    print(f"(exit code {code})", file=out)


def grade_real(out):
    """The one REAL trace (a recorded `claude -p` run, trimmed); it fails two checks, each for a stated cause."""
    folder = TRACES / "real"
    print(f"$ python3 python/run_evals.py --fixtures {rel(folder)} --only explicit-name --label real", file=out)
    code = run_evals.main(["--fixtures", str(folder), "--prompts", str(PROMPTS), "--only", "explicit-name", "--label", "real"], stream=out)
    print(f"(exit code {code})", file=out)


def compare(out):
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name in SETS:
            paths[name] = Path(tmp) / f"{name}.json"
            run_evals.main(["--fixtures", str(SETS[name]), "--prompts", str(PROMPTS), "--label", name, "--results", str(paths[name])],
                           stream=io.StringIO())
        print("$ python3 python/report.py before.json after.json", file=out)
        code = report.main([str(paths["before"]), str(paths["after"])], stream=out)
    print(f"(exit code {code})", file=out)


def judge_prompt(out):
    trace_file = SETS["after"] / "followup-skip.jsonl"
    print(f"$ python3 python/judge.py followup-skip {rel(trace_file)}", file=out)
    print("(no --judge-cmd: the prompt is printed and no model is called)\n", file=out)
    args = ["followup-skip", str(trace_file), "--prompts", str(PROMPTS)]
    buffer = io.StringIO()
    stdout, sys.stdout = sys.stdout, buffer
    try:
        judge.main(args)
    finally:
        sys.stdout = stdout
    out.write(buffer.getvalue())


def render(section):
    out = io.StringIO()
    if section in ("after", "before"):
        grade(section, out)
    elif section == "real":
        grade_real(out)
    elif section == "compare":
        compare(out)
    else:
        judge_prompt(out)
    return out.getvalue()


def main(argv=None):
    wanted = (argv if argv is not None else sys.argv[1:]) or list(SECTIONS)
    for name in wanted:
        if name not in SECTIONS:
            print(f"demo: unknown section {name}; choose from {', '.join(SECTIONS)}", file=sys.stderr)
            return 2
    for index, name in enumerate(wanted):
        if index:
            print()
        sys.stdout.write(render(name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
