"""Run the auto-commit evals: case CSV -> fixture repository + bare remote -> headless agent -> trace + repository snapshots -> checks.

    python3 run_evals.py --fixtures DIR                 grade recorded runs (<id>.jsonl + <id>.state.json) in DIR: offline, free
    python3 run_evals.py --only wip-preexisting         one real run: model calls on your own account
    python3 run_evals.py --trials 3                     every case, 3 trials each

Each trial gets a temporary directory <root>: <root>/ws is a git repository built from the case's fixture (the agent's cwd),
<root>/origin.git a local bare remote. The agent command gets the prompt in place of `{prompt}` (else as its last argument),
`--add-dir <root>/origin.git` (so that a sandboxed Bash may push there) and `--plugin-dir <plugin>`, and the environment variables
EVAL_PROMPT_ID, EVAL_TRIAL, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM (both /dev/null). Its stdout is the trace, saved
as artifacts/<id>.jsonl; the repository snapshots before and after the agent are saved as artifacts/<id>.state.json.
The trial directory is removed afterwards; --keep leaves it for inspection.

A trial is PASS, FAIL (a check of the process, gate or outcome group failed, or a VIOLATIONS check that the repository state proves)
or INCONCLUSIVE (a precondition check failed: the run crashed, git was denied or the hook never spoke, so the behaviour under test
was never exercised and the plugin cannot be blamed). The exit code is 0 when every trial passes, 1 when a trial fails or is
inconclusive, 2 when a run cannot start.

The agent's environment has no GIT_* variable of the caller (see without_git_variables) except the two isolation ones. The agent's
exit code is saved in the state file under "run" so that a recorded run is graded with it.
"""
import argparse
import contextlib
import json
import os
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
PLUGIN = REPO / "plugins" / "auto-commit"
# Read and edit files, and run git whole: granted on purpose so that a violation (rebase, force, .env) can happen and be measured.
ALLOWED = "Read,Glob,Grep,Edit,Write,Bash(git *),Bash(ls *),Bash(cat *)"
# `env -u` removes variables of the session that launched this harness: runner_cmd merges os.environ and cannot unset, and an
# inherited CLAUDE_PROJECT_DIR would point the hook at that checkout, an inherited CLAUDE_HOOK_RUNNING makes it exit silently.
# `project,local` keeps user-level settings out (whether it also keeps user-level hooks out is unconfirmed). Never --bare: `claude --help` says it skips hooks (those defined in settings and by installed plugins), CLAUDE.md discovery and OAuth reads.
DEFAULT_AGENT_CMD = ("env -u CLAUDE_PROJECT_DIR -u CLAUDE_HOOK_RUNNING -u MILESTONES_FILE -u MILESTONES_NOW -u MILESTONES_STALE_MINUTES "
                     f'claude -p {{prompt}} --output-format stream-json --verbose --include-hook-events --permission-mode dontAsk '
                     f'--max-budget-usd 2 --allowedTools "{ALLOWED}" --setting-sources project,local')
GROUPS = ("precondition", "process", "gate", "outcome")


@contextlib.contextmanager
def without_git_variables():
    """Remove every GIT_* variable from os.environ for the duration.

    runner_cmd.run (a byte-identical copy that must stay so) merges os.environ into the child's environment and cannot unset anything,
    and the caller's GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_COUNT/KEY/VALUE, GIT_CONFIG_PARAMETERS or GIT_AUTHOR_* would beat the fixture's
    LOCAL git config or point the agent's git at the caller's own repository. Not thread-safe, which this harness is not.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("GIT_")}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(saved)


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


def run_agent(row, trial, args):
    """Run one trial; returns (trace text, states dict, error note)."""
    root = Path(tempfile.mkdtemp(prefix=f"auto-commit-eval-{row.id}-")).resolve()
    try:
        ws, origin = state.prepare(row.fixture, root)
        states = {"before": state.snapshot(ws, origin)}
        # --add-dir takes a list: it is always followed by another flag, never by the prompt or the end of a value list
        extra = (["--add-dir", str(origin)] if origin else []) + ["--plugin-dir", str(Path(args.plugin).resolve())]
        argv = runner_cmd.build(args.agent_cmd, row.prompt, extra)
        env = {"EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial), **state.GIT_ISOLATION}
        try:
            with without_git_variables():
                code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=ws)
        except FileNotFoundError as error:
            raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
        states["after"] = state.snapshot(ws, origin)
        states["run"] = {"exit_code": code}
    finally:
        if args.keep:
            print(f"run_evals: kept the trial directory {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return out, states, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"


def verdict(results):
    """FAIL when a violation (checks.VIOLATIONS) failed; else INCONCLUSIVE when a precondition check failed; else FAIL when any failed; else PASS.

    A violation is something the agent did, which the repository or its own commands prove: a denied command, a crash or a missing hook
    report cannot excuse it.
    """
    failed = [r["id"] for r in results if not r["pass"]]
    if any(cid in checks.VIOLATIONS for cid in failed):
        return "FAIL"
    if any(checks.GROUPS[cid] == "precondition" for cid in failed):
        return "INCONCLUSIVE"
    return "FAIL" if failed else "PASS"


def grade_trial(row, events, states, plugin_path=""):
    """Grade one trial; the agent's exit code comes from states["run"] (absent in a recording made without one)."""
    states = states or {}
    case = checks.Case(row, events, states, exit_code=(states.get("run") or {}).get("exit_code"), plugin_path=str(plugin_path))
    results = [{"id": cid, "pass": ok, "notes": note} for cid, ok, note in checks.run_checks(case)]
    result = trace.result_event(events) or {}
    # the cost of the LAST result event: agent_trace.stats() sums every result event, which double-counts a resumed session
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
                events, states = found if found else ([], {})
                if not found:
                    note = f"no recorded trace for {row.id} in {args.fixtures}"
            else:
                text, states, note = run_agent(row, trial, args)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                    trace_path(out_dir, row.id, trial, args.trials, "state.json").write_text(json.dumps(states, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, states, args.plugin and Path(args.plugin).resolve())
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "fixture": row.fixture, "expect": row.expect, "trials": trials})
    return {"label": args.label, "trials": args.trials, "prompts": prompts}


def case_verdict(prompt):
    verdicts = {t["verdict"] for t in prompt["trials"]}
    return "FAIL" if "FAIL" in verdicts else "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts else "PASS"


def count(prompt):
    return sum(1 for t in prompt["trials"] if t["verdict"] == "PASS"), len(prompt["trials"])


def failed_ids(prompt):
    ids = []
    for t in prompt["trials"]:
        ids += [c["id"] for c in t["checks"] if not c["pass"] and c["id"] not in ids]
    return ids


def format_report(results):
    """The per-case table, the failures, the per-group counts and the summary line, as a list of lines."""
    w = max([len(p["id"]) for p in results["prompts"]] + [2]) + 2
    lines = [f"{'id':<{w}}{'fixture':<11}{'expect':<14}{'trials':<8}{'result':<14}failed checks"]
    for p in results["prompts"]:
        passed, total = count(p)
        lines.append(f"{p['id']:<{w}}{p['fixture']:<11}{p['expect']:<14}{f'{passed}/{total}':<8}{case_verdict(p):<14}{', '.join(failed_ids(p)) or '-'}")
    problems = []
    for p in results["prompts"]:
        for t in p["trials"]:
            if t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: {t['run_note']}")
            problems += [f"  {p['id']} trial {t['trial']} [{t['verdict']}]: {c['id']}: {c['notes']}" for c in t["checks"] if not c["pass"]]
    if problems:
        lines += ["", "failures:"] + problems
    groups = {}
    for p in results["prompts"]:
        for t in p["trials"]:
            for c in t["checks"]:
                done, total = groups.get(checks.GROUPS[c["id"]], (0, 0))
                groups[checks.GROUPS[c["id"]]] = (done + c["pass"], total + 1)
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in GROUPS if g in groups)]
    trials = [t for p in results["prompts"] for t in p["trials"]]
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    verdicts = [case_verdict(p) for p in results["prompts"]]
    trials_ok = sum(1 for t in trials if t["verdict"] == "PASS")
    rate = 100 * trials_ok // len(trials) if trials else 0
    lines.append(f"summary: cases passed {verdicts.count('PASS')}/{len(verdicts)}, failed {verdicts.count('FAIL')}, "
                 f"inconclusive {verdicts.count('INCONCLUSIVE')}; trials passed {trials_ok}/{len(trials)}, pass rate {rate}%")
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
    parser.add_argument("--results", help="write the results JSON here (default: <out>/results.json; none with --fixtures)")
    parser.add_argument("--label", default="", help="a name for this run, stored in the results")
    parser.add_argument("--keep", action="store_true", help="keep each trial's temporary directory (workspace and bare remote) instead of removing it")
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
