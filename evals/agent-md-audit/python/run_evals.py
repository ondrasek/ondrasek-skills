"""Run the agent-md-audit evals: case CSV -> fixture workspace -> headless agent -> trace + file snapshots -> deterministic checks.

    python3 run_evals.py --fixtures DIR                 grade recorded traces (<id>.jsonl + <id>.state.json) in DIR: offline, free
    python3 run_evals.py --only explicit-defects        one real run: model calls AND web fetches on your own account
    python3 run_evals.py --trials 3                     every case, 3 trials each

Each trial gets a fresh temporary git repository built from the case's fixture (tracked and clean). The agent command gets the
prompt in place of `{prompt}` (else as its last argument), `--plugin-dir <plugin>` and, for a second turn, `--resume <session id>`,
and the environment variables EVAL_PROMPT_ID, EVAL_TRIAL, EVAL_TURN. Its stdout is the trace, saved as artifacts/<id>.jsonl; the three
workspace snapshots (before, after turn 1, after turn 2) are saved as artifacts/<id>.state.json. Both can contain the contents of your
own ~/.claude/CLAUDE.md (the skill reads it for context): review them before you commit or share them.
The exit code is 0 when every trial passes every check, 1 when a trial fails, 2 when a run cannot start.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import agent_trace as trace
import cases
import checks
import runner_cmd
import state

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
PLUGIN = REPO / "plugins" / "agent-md-audit"
# Read-only tools, the web, and Edit/Write: edits are granted on purpose so that a violation can happen and be measured.
ALLOWED = "Skill,Read,Glob,Grep,Agent,WebFetch,WebSearch,Edit,Write,Bash(wc *),Bash(ls *),Bash(git status *),Bash(git ls-files *),Bash(claude --version)"
# `project,local` keeps the machine's user-level settings, hooks and plugins out of the run (the skill still reads ~/.claude/CLAUDE.md)
DEFAULT_AGENT_CMD = f'claude -p {{prompt}} --output-format stream-json --verbose --allowedTools "{ALLOWED}" --setting-sources project,local'


def trace_path(directory, row_id, trial, trials, suffix="jsonl"):
    return Path(directory) / (f"{row_id}.{suffix}" if trials == 1 else f"{row_id}.{trial}.{suffix}")


def recorded(directory, row_id, trial):
    """(events, states) of a recorded run in `directory`, or None when its trace is missing."""
    for stem in (f"{row_id}.{trial}", row_id):
        path = Path(directory) / f"{stem}.jsonl"
        if path.is_file():
            states_path = Path(directory) / f"{stem}.state.json"
            states = json.loads(states_path.read_text(encoding="utf-8")) if states_path.is_file() else {}
            return trace.load(path), states
    return None


def run_turn(prompt, row, trial, turn, args, workdir, extra):
    argv = runner_cmd.build(args.agent_cmd, prompt, extra)
    env = {"EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial), "EVAL_TURN": str(turn)}
    try:
        code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=workdir)
    except FileNotFoundError as error:
        raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
    return out, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"


def run_agent(row, trial, args):
    """Run one trial; returns (trace text, states dict, error note)."""
    workdir = Path(tempfile.mkdtemp(prefix=f"agent-md-audit-eval-{row.id}-"))
    state.prepare(row.fixture, workdir)
    states = {"before": state.snapshot(workdir), "after2": None}
    extra = ["--plugin-dir", str(Path(args.plugin).resolve())]
    out, note = run_turn(row.prompt, row, trial, 1, args, workdir, extra)
    states["after1"] = state.snapshot(workdir)
    if not row.reply:
        return out, states, note
    session = trace.session_id(trace.parse(out)[0])
    if note or not session:
        return out, states, note or "turn 1 printed no session id, so the approval turn cannot resume it"
    second, second_note = run_turn(row.reply, row, trial, 2, args, workdir, extra + ["--resume", session])
    states["after2"] = state.snapshot(workdir)
    return out + second, states, second_note


def grade_trial(row, events, states):
    case = checks.Case.build(row, events, states)
    results = [{"id": cid, "pass": ok, "notes": note} for cid, ok, note in checks.run_checks(case)]
    result = trace.result_event(events) or {}
    turns, cost = trace.stats(events)
    return {"passed": all(r["pass"] for r in results), "checks": results, "turns": turns, "cost": cost,
            "duration_ms": result.get("duration_ms")}


def run_all(rows, args):
    out_dir = Path(args.out) if args.out and not args.fixtures else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    prompts = []
    for row in rows:
        trials = []
        for trial in range(1, args.trials + 1):
            note = ""
            if args.fixtures:
                found = recorded(args.fixtures, row.id, trial)
                events, states = found if found else ([], {})
                if not found:
                    note = f"no recorded trace for {row.id} in {args.fixtures}"
            else:
                text, states, note = run_agent(row, trial, args)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                    trace_path(out_dir, row.id, trial, args.trials, "state.json").write_text(json.dumps(states, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, states)
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "kind": row.kind, "expect": row.expect, "should_trigger": row.should_trigger, "trials": trials})
    return {"label": args.label, "trials": args.trials, "prompts": prompts}


def count(prompt):
    return sum(1 for t in prompt["trials"] if t["passed"]), len(prompt["trials"])


def failed_ids(prompt):
    ids = []
    for t in prompt["trials"]:
        ids += [c["id"] for c in t["checks"] if not c["pass"] and c["id"] not in ids]
    return ids


def format_report(results):
    """The per-case table, the failures, the per-group counts and the summary line, as a list of lines."""
    w = max([len(p["id"]) for p in results["prompts"]] + [2]) + 2
    lines = [f"{'id':<{w}}{'kind':<10}{'expect':<10}{'trigger':<9}{'trials':<8}{'result':<8}failed checks"]
    for p in results["prompts"]:
        passed, total = count(p)
        lines.append(f"{p['id']:<{w}}{p['kind']:<10}{p['expect']:<10}{'yes' if p['should_trigger'] else 'no':<9}"
                     f"{f'{passed}/{total}':<8}{'PASS' if passed == total else 'FAIL':<8}{', '.join(failed_ids(p)) or '-'}")
    problems = []
    for p in results["prompts"]:
        for t in p["trials"]:
            if t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: {t['run_note']}")
            problems += [f"  {p['id']} trial {t['trial']}: {c['id']}: {c['notes']}" for c in t["checks"] if not c["pass"]]
    if problems:
        lines += ["", "failures:"] + problems
    groups = {}
    for p in results["prompts"]:
        for t in p["trials"]:
            for c in t["checks"]:
                done, total = groups.get(checks.GROUPS[c["id"]], (0, 0))
                groups[checks.GROUPS[c["id"]]] = (done + c["pass"], total + 1)
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in ("process", "gate", "outcome", "apply") if g in groups)]
    trials = [t for p in results["prompts"] for t in p["trials"]]
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    prompts_ok = sum(1 for p in results["prompts"] if count(p)[0] == count(p)[1])
    trials_ok = sum(1 for t in trials if t["passed"])
    rate = 100 * trials_ok // len(trials) if trials else 0
    lines.append(f"summary: cases passed {prompts_ok}/{len(results['prompts'])} (every trial), trials passed {trials_ok}/{len(trials)}, pass rate {rate}%")
    return lines


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--cases", default=str(UNIT / "cases" / "cases.csv"), help="the case CSV")
    parser.add_argument("--out", default=str(UNIT / "artifacts"), help="where the traces, snapshots and results.json go")
    parser.add_argument("--plugin", default=str(PLUGIN), help="the plugin directory under test; passed to the agent as --plugin-dir")
    parser.add_argument("--agent-cmd", default=DEFAULT_AGENT_CMD, help=f"default: {DEFAULT_AGENT_CMD}")
    parser.add_argument("--trials", type=int, default=1, help="runs per case (the model is not deterministic)")
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--fixtures", help="grade recorded runs from this directory instead of running the agent")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per model call (a real audit takes minutes: it fetches the docs)")
    parser.add_argument("--results", help="write the results JSON here (default: <out>/results.json; none with --fixtures)")
    parser.add_argument("--label", default="", help="a name for this run, stored in the results")
    return parser.parse_args(argv)


def main(argv=None, stream=None):
    args = parse_args(argv)
    stream = stream or sys.stdout
    if args.trials < 1:
        print("run_evals: --trials must be at least 1", file=sys.stderr)
        return 2
    try:
        rows = cases.load_cases(args.cases)
    except (OSError, ValueError) as error:
        print(f"run_evals: {error}", file=sys.stderr)
        return 2
    if args.only:
        wanted = [name for name in args.only.split(",") if name]
        unknown = [name for name in wanted if name not in {r.id for r in rows}]
        if unknown:
            print(f"run_evals: no case with id {', '.join(unknown)}", file=sys.stderr)
            return 2
        rows = [r for r in rows if r.id in wanted]
    results = run_all(rows, args)
    print("\n".join(format_report(results)), file=stream)
    target = args.results or (None if args.fixtures else str(Path(args.out) / "results.json"))
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(t["passed"] for p in results["prompts"] for t in p["trials"]) else 1


if __name__ == "__main__":
    sys.exit(main())
