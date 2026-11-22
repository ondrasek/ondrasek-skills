"""Run the block-force-push evals: case CSV -> throwaway git repositories -> headless agent -> trace + repository snapshots -> checks.

    python3 run_evals.py --fixtures DIR                      grade recorded traces (<id>.jsonl + <id>.state.json) in DIR: offline, free
    python3 run_evals.py --only plain-push-control           one real run: model calls on your own account (run this case first)
    python3 run_evals.py --trials 3                          every case, 3 trials each

Each trial gets a fresh temporary directory: ws (a git repository, the agent's working directory), origin.git (its local bare
remote) and an empty hooks directory (see state.py). The agent command gets the prompt in place of `{prompt}` (else as its last
argument), `--add-dir <origin.git>` and `--plugin-dir <plugin>` (never a bare positional after the variadic --add-dir), and the
environment variables EVAL_PROMPT_ID and EVAL_TRIAL, plus GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM pointing at the null device.
Its stdout is the trace, saved as artifacts/<id>.jsonl; the snapshots before and after are saved as artifacts/<id>.state.json.
The trial directory is deleted afterwards unless --keep is given.
A trial is PASS, FAIL, or INCONCLUSIVE (the behaviour under test was never exercised, or the run was cut short by a timeout, a
budget stop or an error, see checks.verdict). Advisory ("soft") checks that rest on the model's prose are listed as WARN and never
change the verdict or the exit code.
The exit code is 0 when every trial passes (warnings or not), 1 when a trial fails or is inconclusive, 2 when a run cannot start.
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
PLUGIN = REPO / "plugins" / "block-force-push"
# Read-only tools and git: `git *` is granted on purpose, so that a forced push CAN happen when the hook fails, and be measured.
ALLOWED = "Read,Glob,Grep,Bash(git *),Bash(ls *)"
# `env -u` drops variables a parent Claude Code session may have exported: runner_cmd.run merges os.environ and cannot unset.
# `project,local` is meant to keep user-level settings and hooks out; whether it does is unconfirmed (see README).
DEFAULT_AGENT_CMD = (f'env -u CLAUDE_PROJECT_DIR -u CLAUDE_HOOK_RUNNING -u MILESTONES_FILE -u MILESTONES_NOW -u MILESTONES_STALE_MINUTES '
                     f'claude -p {{prompt}} --output-format stream-json --verbose '
                     f'--include-hook-events --permission-mode dontAsk --max-budget-usd 2 --allowedTools "{ALLOWED}" --setting-sources project,local')
GROUP_ORDER = ("precondition", "process", "outcome")


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


def run_turn(row, trial, args, workdir, extra):
    argv = runner_cmd.build(args.agent_cmd, row.prompt, extra)
    env = {"EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial), **state.child_env()}
    try:
        code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=workdir)
    except FileNotFoundError as error:
        raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
    return out, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"


def run_agent(row, trial, args):
    """Run one trial; returns (trace text, states dict, error note)."""
    root = Path(tempfile.mkdtemp(prefix=f"block-force-push-eval-{row.id}-")).resolve()
    try:
        ws = state.prepare(row.fixture, root)
        states = {"before": state.snapshot(root)}
        # --add-dir is variadic: it must be followed by another flag, never by the prompt
        extra = ["--add-dir", str(root / state.ORIGIN), "--plugin-dir", str(Path(args.plugin).resolve())]
        out, note = run_turn(row, trial, args, ws, extra)
        states["after"] = state.snapshot(root)
    finally:
        if args.keep:
            print(f"run_evals: kept the trial directory {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return out, states, note


def abort_reason(events, run_note=""):
    """Why the run was cut short by the environment, or "": the agent command exited non-zero (timeout 124, killed ...), there is no
    result event, the result's subtype is not "success" (a budget stop is error_max_budget_usd) or it is flagged is_error."""
    result = trace.result_event(events)
    if run_note.startswith("agent command exited"):
        return run_note
    if result is None:
        return "no result event in the trace"
    if result.get("subtype") != "success":
        return f"result subtype {result.get('subtype')!r}, not 'success'"
    return "the result event has is_error set" if result.get("is_error") else ""


def grade_trial(row, events, states, run_note=""):
    case = checks.Case.build(row, events, states)
    results = [{"id": cid, "pass": ok, "notes": note, "group": checks.GROUPS[cid]} for cid, ok, note in checks.run_checks(case)]
    result = trace.result_event(events) or {}
    cost, turns = result.get("total_cost_usd"), result.get("num_turns")
    # no events at all: the agent never ran (not installed, not logged in, no recording): nothing was exercised;
    # no snapshots (a recording without its <id>.state.json): the repositories, the ground truth, were never seen
    complete = bool(events) and bool((states or {}).get("before")) and bool((states or {}).get("after"))
    aborted = abort_reason(events, run_note) if events else ""
    verdict = checks.verdict([r["id"] for r in results if not r["pass"]], bool(aborted)) if complete else "INCONCLUSIVE"
    return {"verdict": verdict, "aborted": aborted, "checks": results, "turns": turns if isinstance(turns, int) else None,
            "cost": cost if isinstance(cost, (int, float)) else None, "duration_ms": result.get("duration_ms")}


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
                elif not (states.get("before") and states.get("after")):
                    note = f"no repository snapshots recorded for {row.id} in {args.fixtures} (<id>.state.json missing or incomplete)"
            else:
                text, states, note = run_agent(row, trial, args)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                    trace_path(out_dir, row.id, trial, args.trials, "state.json").write_text(json.dumps(states, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, states, note)
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "expect": row.expect, "trials": trials})
    return {"label": args.label, "trials": args.trials, "prompts": prompts}


def is_soft(check):
    """True for a check result of an advisory (soft) check; an id this version does not know is hard."""
    return checks.GROUPS.get(check["id"]) == "soft"


def count(prompt):
    return sum(1 for t in prompt["trials"] if t["verdict"] == "PASS"), len(prompt["trials"])


def case_verdict(prompt):
    verdicts = {t["verdict"] for t in prompt["trials"]}
    return "FAIL" if "FAIL" in verdicts else "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts else "PASS"


def failed_ids(prompt, soft=False):
    """The ids of the failed checks of a case: the hard ones, or with `soft` the advisory ones (WARN)."""
    ids = []
    for t in prompt["trials"]:
        ids += [c["id"] for c in t["checks"] if not c["pass"] and is_soft(c) == soft and c["id"] not in ids]
    return ids


def format_report(results):
    """The per-case table, the failures, the per-group counts and the summary line, as a list of lines."""
    w = max([len(p["id"]) for p in results["prompts"]] + [2]) + 2
    lines = [f"{'id':<{w}}{'expect':<9}{'trials':<8}{'result':<14}{'failed checks':<40}warnings (advisory)"]
    for p in results["prompts"]:
        passed, total = count(p)
        lines.append(f"{p['id']:<{w}}{p['expect']:<9}{f'{passed}/{total}':<8}{case_verdict(p):<14}{', '.join(failed_ids(p)) or '-':<40}{', '.join(failed_ids(p, True)) or '-'}")
    problems, warns = [], []
    for p in results["prompts"]:
        for t in p["trials"]:
            if t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: {t['run_note']}")
            if t.get("aborted") and t["aborted"] != t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: run cut short ({t['aborted']}): INCONCLUSIVE unless a state check failed")
            for c in t["checks"]:
                if not c["pass"]:
                    (warns if is_soft(c) else problems).append(f"  {p['id']} trial {t['trial']} ({t['verdict']}): {c['id']}: {c['notes']}")
    if problems:
        lines += ["", "failures:"] + problems
    if warns:
        lines += ["", "warnings (advisory checks on the model's wording; they never change the verdict):"] + warns
    groups = {}
    for p in results["prompts"]:
        for t in p["trials"]:
            for c in t["checks"]:
                done, total = groups.get(checks.GROUPS[c["id"]], (0, 0))
                groups[checks.GROUPS[c["id"]]] = (done + c["pass"], total + 1)
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in GROUP_ORDER if g in groups)]
    if "soft" in groups:
        lines.append(f"advisory (soft) checks: {groups['soft'][0]}/{groups['soft'][1]} passed, WARN {groups['soft'][1] - groups['soft'][0]} (not part of any verdict)")
    trials = [t for p in results["prompts"] for t in p["trials"]]
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    tally = {v: sum(1 for t in trials if t["verdict"] == v) for v in ("PASS", "FAIL", "INCONCLUSIVE")}
    warned = sum(1 for t in trials if any(not c["pass"] and is_soft(c) for c in t["checks"]))
    lines.append(f"summary: trials PASS {tally['PASS']}, FAIL {tally['FAIL']}, INCONCLUSIVE {tally['INCONCLUSIVE']} of {len(trials)}; "
                 f"WARN in {warned} trial(s) (advisory only)")
    if tally["INCONCLUSIVE"]:
        lines.append("INCONCLUSIVE means the behaviour under test was not exercised (see the failed preconditions above) or the run was cut short "
                     "(timeout, budget, error): it is not a verdict on the plugin; run again")
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
    parser.add_argument("--keep", action="store_true", help="keep each trial's temporary directory (workspace and bare remote) for inspection")
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
