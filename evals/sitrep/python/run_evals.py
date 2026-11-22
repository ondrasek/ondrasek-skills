"""Run the sitrep evals: case CSV -> fixture workspace -> headless agent -> trace + state snapshots -> deterministic checks.

    python3 run_evals.py --fixtures DIR                 grade recorded runs (<id>.jsonl + <id>.state.json) in DIR: offline, free
    python3 run_evals.py --only explicit-report         one real run: model calls on your own account
    python3 run_evals.py --trials 3                     every case, 3 trials each

Each trial gets a fresh temporary root: root/ws is a git repository built from the case's fixture (the agent's cwd), root/tmp is the
child's TMPDIR (report.sh makes and sweeps its sitrep.* directories only there; granted to the agent with --add-dir), root/hooks is an
empty hooks directory. The agent command gets the prompt in place of `{prompt}` (else as its last argument), `--add-dir <root/tmp>`,
`--plugin-dir <plugin>` and, for a second turn, `--resume <session id>`; `--add-dir` takes a list, so it is never the last argument.
The environment gets EVAL_PROMPT_ID, EVAL_TRIAL, EVAL_TURN, TMPDIR and GIT_CONFIG_GLOBAL/SYSTEM pointing at nothing. Its stdout is the
trace, saved as artifacts/<id>.jsonl; the snapshots (workspace before and after each turn, the report directory after each turn) are
saved as artifacts/<id>.state.json together with the runner's note (non-zero exit, timeout, missing session id) as `run_note`. The root is deleted after the last snapshot unless --keep is given.
A trial is PASS, FAIL or INCONCLUSIVE (a precondition failed: the behaviour was never exercised; see checks.verdict).
A run that did not finish (non-zero exit, timeout, no success result event) is INCONCLUSIVE too (check run-completed).
The exit code is 0 when every trial passes, 1 when a trial fails or is inconclusive, 2 when a run cannot start.
"""
import argparse
import json
import shlex
import shutil
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
PLUGIN = REPO / "plugins" / "sitrep"
# Write and Edit are granted on purpose, so that a write into the project can happen and be measured. report.sh is granted in the
# two spellings SKILL.md gives it (the closing quote of the script path comes before the verb); open, xdg-open and start are not
# granted, which should keep a browser window from opening (assuming dontAsk denies what is not granted: unconfirmed).
ALLOWED = 'Skill,Read,Glob,Grep,Write,Edit,Bash(*report.sh *),Bash(*report.sh" *),Bash(ls *),Bash(cat *)'
# `env -u` removes the variables the milestones plugin and the hooks would use to look at the repository this harness was started in:
# runner_cmd.run can add to the environment but not remove from it. `project,local` is meant to keep the machine's user settings, hooks and
# plugins out (the intent; whether it does is unconfirmed, see the README). Not --bare: `claude --help` says it skips hooks, plugin sync
# and more, and what that does to --plugin-dir has not been checked.
DEFAULT_AGENT_CMD = ("env -u CLAUDE_PROJECT_DIR -u CLAUDE_HOOK_RUNNING -u MILESTONES_FILE -u MILESTONES_NOW -u MILESTONES_STALE_MINUTES "
                     "claude -p {prompt} --output-format stream-json --verbose --include-hook-events --permission-mode dontAsk "
                     f"--max-budget-usd 2 --allowedTools {shlex.quote(ALLOWED)} --setting-sources project,local")


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


def run_turn(prompt, row, trial, turn, args, layout, extra):
    argv = runner_cmd.build(args.agent_cmd, prompt, extra)
    env = {"EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial), "EVAL_TURN": str(turn), "TMPDIR": layout["tmp"], **state.GIT_ENV}
    try:
        code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=layout["ws"])
    except FileNotFoundError as error:
        raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
    return out, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"


def run_trial(row, trial, args, layout):
    """The two turns of a trial in `layout`; returns (trace text, states dict, error note)."""
    template = Path(args.plugin) / "skills" / "sitrep" / "template.html"
    states = {"layout": layout, "plugin": str(Path(args.plugin).resolve()), "before": state.snapshot(layout["ws"]), "after2": None, "report2": None}
    extra = ["--add-dir", layout["tmp"], "--plugin-dir", str(Path(args.plugin).resolve())]
    out, note = run_turn(row.prompt, row, trial, 1, args, layout, extra)
    directory, how = state.find_report(trace.parse(out)[0], layout["tmp"])
    states.update(after1=state.snapshot(layout["ws"]), tmp_sitrep=state.sitrep_dirs(layout["tmp"]),
                  report1=state.report_snapshot(directory, template, how))
    if not row.reply:
        return out, states, note
    session = trace.session_id(trace.parse(out)[0])
    if note or not session:
        return out, states, note or "turn 1 printed no session id, so the second turn cannot resume it"
    second, second_note = run_turn(row.reply, row, trial, 2, args, layout, extra + ["--resume", session])
    states.update(after2=state.snapshot(layout["ws"]), report2=state.report_snapshot(directory, template))
    return out + second, states, second_note


def run_agent(row, trial, args):
    root = Path(tempfile.mkdtemp(prefix=f"sitrep-eval-{row.id}-")).resolve()
    try:
        return run_trial(row, trial, args, state.prepare(row.fixture, root))
    finally:
        if args.keep:
            print(f"run_evals: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


def grade_trial(row, events, states, note=""):
    case = checks.Case.build(row, events, states, note)
    outcomes = checks.run_checks(case)
    result = trace.result_event(events) or {}
    # the cost of the LAST result event: assumed (unconfirmed) to be the whole session's on a resumed turn, in which case trace.stats
    # (a sum over result events) would count turn 1 twice
    cost = result.get("total_cost_usd")
    verdict = checks.verdict(outcomes)
    return {"verdict": verdict, "passed": verdict == "PASS", "checks": [{"id": c, "pass": ok, "notes": note} for c, ok, note in outcomes],
            "turns": result.get("num_turns"), "cost": cost if isinstance(cost, (int, float)) else None, "duration_ms": result.get("duration_ms")}


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
                note = states.get("run_note", "") if found else f"no recorded trace for {row.id} in {args.fixtures}"
            else:
                text, states, note = run_agent(row, trial, args)
                states["run_note"] = note       # saved with the snapshots, so that a recorded run is graded with it (a non-zero exit after a success result)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                    trace_path(out_dir, row.id, trial, args.trials, "state.json").write_text(json.dumps(states, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, states, note)
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "kind": row.kind, "expect": row.expect, "should_trigger": row.should_trigger, "trials": trials})
    return {"label": args.label, "trials": args.trials, "prompts": prompts}


def count(prompt):
    return sum(1 for t in prompt["trials"] if t["passed"]), len(prompt["trials"])


def case_verdict(prompt):
    verdicts = {t["verdict"] for t in prompt["trials"]}
    return "FAIL" if "FAIL" in verdicts else "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts else "PASS"


def failed_ids(prompt):
    ids = []
    for t in prompt["trials"]:
        ids += [c["id"] for c in t["checks"] if not c["pass"] and c["id"] not in ids]
    return ids


def format_report(results):
    """The per-case table, the failures, the per-group counts and the summary line, as a list of lines."""
    w = max([len(p["id"]) for p in results["prompts"]] + [2]) + 2
    lines = [f"{'id':<{w}}{'kind':<12}{'expect':<14}{'trigger':<9}{'trials':<8}{'result':<14}failed checks"]
    for p in results["prompts"]:
        passed, total = count(p)
        lines.append(f"{p['id']:<{w}}{p['kind']:<12}{p['expect']:<14}{'yes' if p['should_trigger'] else 'no':<9}"
                     f"{f'{passed}/{total}':<8}{case_verdict(p):<14}{', '.join(failed_ids(p)) or '-'}")
    problems = []
    for p in results["prompts"]:
        for t in p["trials"]:
            if t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: {t['run_note']}")
            problems += [f"  {p['id']} trial {t['trial']} [{t['verdict']}]: {c['id']}: {c['notes']}" for c in t["checks"] if not c["pass"]]
    if problems:
        lines += ["", "failures (an INCONCLUSIVE trial lists its failed gate and outcome checks too: they may follow from the failed precondition):"] + problems
    groups = {}
    for p in results["prompts"]:
        for t in p["trials"]:
            for c in t["checks"]:
                done, total = groups.get(checks.GROUPS[c["id"]], (0, 0))
                groups[checks.GROUPS[c["id"]]] = (done + c["pass"], total + 1)
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in ("precondition", "process", "gate", "outcome") if g in groups)]
    trials = [t for p in results["prompts"] for t in p["trials"]]
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    prompts_ok = sum(1 for p in results["prompts"] if case_verdict(p) == "PASS")
    trials_ok = sum(1 for t in trials if t["passed"])
    inconclusive = sum(1 for t in trials if t["verdict"] == "INCONCLUSIVE")
    rate = 100 * trials_ok // len(trials) if trials else 0
    lines.append(f"summary: cases passed {prompts_ok}/{len(results['prompts'])} (every trial), trials passed {trials_ok}/{len(trials)}, "
                 f"inconclusive {inconclusive}, failed {len(trials) - trials_ok - inconclusive}, pass rate {rate}%")
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
    parser.add_argument("--timeout", type=int, default=300, help="seconds per model call")
    parser.add_argument("--results", help="write the results JSON here (default: <out>/results.json; none with --fixtures)")
    parser.add_argument("--label", default="", help="a name for this run, stored in the results")
    parser.add_argument("--keep", action="store_true", help="keep each trial's temporary root (workspace, TMPDIR with the report) instead of deleting it")
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
