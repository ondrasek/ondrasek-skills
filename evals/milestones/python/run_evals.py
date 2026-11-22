"""Run the milestones evals: case CSV -> fixture workspace -> headless agent -> trace + file snapshots -> deterministic checks.

    python3 run_evals.py --fixtures DIR                 grade recorded traces (<id>.jsonl + <id>.state.json) in DIR: offline, free
    python3 run_evals.py --only reminder-finishes       one real run: model calls on your own account
    python3 run_evals.py --trials 3                     every case, 3 trials each

Each trial gets a fresh temporary directory (state.py): a git repository built from the case's fixture, in which the agent runs. The
agent command gets the prompt in place of `{prompt}` (else as its last argument), `--plugin-dir <plugin>`, and the environment
variables EVAL_PROMPT_ID, EVAL_TRIAL, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM (both the null device). Its stdout is the
trace, saved as artifacts/<id>.jsonl; the two workspace snapshots (before, after) are saved as artifacts/<id>.state.json. The trial
directory is removed afterwards unless --keep is given.

A trial is PASS, FAIL or INCONCLUSIVE: INCONCLUSIVE when a precondition check failed (the behaviour was never exercised: the run did
not complete, a snapshot is missing, the script was permission-denied, the Stop hook did not run or its report did not reach the
model), FAIL when any other check failed.
The exit code is 0 when every trial passes, 1 when a trial fails or is inconclusive, 2 when a run cannot start.
"""
import argparse
import json
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
PLUGIN = REPO / "plugins" / "milestones"
GROUP_ORDER = ("precondition", "process", "gate", "outcome")
# Read-only tools, the script, and Edit/Write plus git add/commit: those four are granted on purpose, so that a direct tracker write or a
# commit of the tracker can happen and be measured. Whether this pattern matches the quoted absolute script path is unconfirmed;
# a denial is reported as INCONCLUSIVE (not-permission-blocked).
ALLOWED = ("Skill,Read,Glob,Grep,Edit,Write,Bash(python3 *milestones.py *),Bash(ls *),Bash(cat *),Bash(git status *),"
           "Bash(git add *),Bash(git commit *)")
# `env -u` because runner_cmd.run merges os.environ and cannot unset: an inherited CLAUDE_PROJECT_DIR would point the Stop hook and
# milestones.py at the repository this harness was started from. `project,local` keeps user settings, hooks and plugins out (unconfirmed
# for hooks); never --bare, which skips plugin hooks. --plugin-dir is appended by run_agent.
DEFAULT_AGENT_CMD = ("env -u CLAUDE_PROJECT_DIR -u CLAUDE_HOOK_RUNNING -u MILESTONES_FILE -u MILESTONES_NOW -u MILESTONES_STALE_MINUTES "
                     f'claude -p {{prompt}} --output-format stream-json --verbose --include-hook-events --permission-mode dontAsk '
                     f'--max-budget-usd 2 --allowedTools "{ALLOWED}" --setting-sources project,local')


def trace_path(directory, row_id, trial, trials, suffix="jsonl"):
    return Path(directory) / (f"{row_id}.{suffix}" if trials == 1 else f"{row_id}.{trial}.{suffix}")


def recorded(directory, row_id, trial):
    """(events, states, note) of a recorded run in `directory`, or None when its trace is missing. A missing or unreadable state file
    gives empty states and a note, so that the trial grades INCONCLUSIVE (agent-completed) instead of comparing None with None."""
    for stem in (f"{row_id}.{trial}", row_id):
        path = Path(directory) / f"{stem}.jsonl"
        if path.is_file():
            states_path = Path(directory) / f"{stem}.state.json"
            states, note = {}, ""
            if not states_path.is_file():
                note = f"no {states_path.name} next to the trace"
            else:
                try:
                    loaded = json.loads(states_path.read_text(encoding="utf-8"))
                except ValueError:
                    loaded = None
                if isinstance(loaded, dict):
                    states = loaded
                    note = str(states.pop("run_note", "") or "")
                else:
                    note = f"{states_path.name} is not a JSON object"
            return trace.load(path), states, note
    return None


def run_agent(row, trial, args):
    """Run one trial; returns (trace text, states dict, error note)."""
    root = Path(tempfile.mkdtemp(prefix=f"milestones-eval-{row.id}-")).resolve()
    try:
        workdir = state.prepare(row.fixture, root)
        states = {"before": state.snapshot(workdir)}
        argv = runner_cmd.build(args.agent_cmd, row.prompt, ["--plugin-dir", str(Path(args.plugin).resolve())])
        env = {**state.GIT_ENV, "EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial)}
        try:
            code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=workdir)
        except FileNotFoundError as error:
            raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
        states["after"] = state.snapshot(workdir)
        return out, states, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"
    finally:
        if args.keep:
            print(f"run_evals: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


def verdict(results):
    """INCONCLUSIVE when a precondition check failed, else FAIL when any check failed, else PASS."""
    failed = [r["id"] for r in results if not r["pass"]]
    if any(checks.GROUPS[cid] == "precondition" for cid in failed):
        return "INCONCLUSIVE"
    return "FAIL" if failed else "PASS"


def grade_trial(row, events, states, note="", plugin=""):
    """The graded trial. `note` is what the run itself reported (a non-zero exit, a missing state file): any note, a missing or failed
    result event or a missing snapshot makes the trial INCONCLUSIVE through the agent-completed check."""
    case = checks.Case.build(row, events, states, note, plugin)
    results = [{"id": cid, "pass": ok, "notes": note} for cid, ok, note in checks.run_checks(case)]
    result = trace.result_event(events) or {}
    # the cost of the LAST result event: agent_trace.stats() sums every result event, and a resumed or continued session reports the whole
    return {"verdict": verdict(results), "checks": results, "turns": result.get("num_turns"), "cost": result.get("total_cost_usd"),
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
                events, states, note = found if found else ([], {}, f"no recorded trace for {row.id} in {args.fixtures}")
            else:
                text, states, note = run_agent(row, trial, args)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                    saved = {**states, "run_note": note} if note else states   # so that an offline regrade still sees a failed run
                    trace_path(out_dir, row.id, trial, args.trials, "state.json").write_text(json.dumps(saved, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, states, note, str(Path(args.plugin).resolve()))
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "expect": row.expect, "trials": trials})
    return {"label": args.label, "trials": args.trials, "prompts": prompts}


def count(prompt):
    """(trials that passed, trials run)."""
    return sum(1 for t in prompt["trials"] if t["verdict"] == "PASS"), len(prompt["trials"])


def prompt_verdict(prompt):
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
    lines = [f"{'id':<{w}}{'expect':<17}{'trials':<8}{'result':<14}failed checks"]
    for p in results["prompts"]:
        passed, total = count(p)
        lines.append(f"{p['id']:<{w}}{p['expect']:<17}{f'{passed}/{total}':<8}{prompt_verdict(p):<14}{', '.join(failed_ids(p)) or '-'}")
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
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in GROUP_ORDER if g in groups)]
    trials = [t for p in results["prompts"] for t in p["trials"]]
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    verdicts = [t["verdict"] for t in trials]
    prompts_ok = sum(1 for p in results["prompts"] if prompt_verdict(p) == "PASS")
    rate = 100 * verdicts.count("PASS") // len(trials) if trials else 0
    lines.append(f"summary: cases passed {prompts_ok}/{len(results['prompts'])} (every trial), trials passed {verdicts.count('PASS')}/{len(trials)}, "
                 f"failed {verdicts.count('FAIL')}, inconclusive {verdicts.count('INCONCLUSIVE')}, pass rate {rate}%")
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
    parser.add_argument("--timeout", type=int, default=600, help="seconds per agent call")
    parser.add_argument("--keep", action="store_true", help="keep each trial's temporary directory (default: remove it after the last snapshot)")
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
    return 0 if all(t["verdict"] == "PASS" for p in results["prompts"] for t in p["trials"]) else 1


if __name__ == "__main__":
    sys.exit(main())
