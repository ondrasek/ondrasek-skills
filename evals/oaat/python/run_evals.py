"""Run the oaat skill evals: prompt CSV -> headless agent -> JSONL trace -> deterministic checks -> optional rubric judge.

    python3 run_evals.py --fixtures ../tests/fixtures/traces/after        grade recorded (invented) traces: offline, free
    python3 run_evals.py --only explicit-1 --trials 1                     one real run: ONE model call on your own account
    python3 run_evals.py --trials 3 --judge                               all prompts, 3 trials each, plus a judge call per trial

The agent command gets the prompt in place of `{prompt}` (else as its last argument), and the environment variables
EVAL_PROMPT_ID, EVAL_TRIAL and EVAL_TURN (a row with a `reply` runs two turns; turn 2 adds `--resume <session id>`). Its stdout is the trace and is saved as artifacts/<id>.jsonl (trials > 1: artifacts/<id>.<k>.jsonl).
The exit code is 0 when every trial passes every check, 1 when a trial fails, 2 when a run cannot start.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import cases
import checks
import judge
import runner_cmd
import agent_trace as trace

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
BUNDLED_PLUGIN = REPO / "plugins" / "oaat"   # the live oaat plugin of this repository: the skill under test
BUNDLED_SKILL = BUNDLED_PLUGIN / "skills" / "oaat" / "SKILL.md"
AGENT_CMD = "claude -p {prompt} --output-format stream-json --verbose --allowedTools Skill"
# `project,local` keeps the user-level CLAUDE.md, settings and plugins of the machine out of the run (README, "Isolating the run")
ISOLATION = " --setting-sources project,local"
DEFAULT_AGENT_CMD = AGENT_CMD + ISOLATION
DEFAULT_JUDGE_CMD = "claude -p {prompt}"


def trace_path(directory, row_id, trial, trials):
    return Path(directory) / (f"{row_id}.jsonl" if trials == 1 else f"{row_id}.{trial}.jsonl")


def fixture_events(directory, row_id, trial):
    """Events of a recorded trace: <id>.<trial>.jsonl when it exists, else <id>.jsonl; None when neither exists."""
    for name in (f"{row_id}.{trial}.jsonl", f"{row_id}.jsonl"):
        path = Path(directory) / name
        if path.is_file():
            return trace.load(path)
    return None


def run_turn(prompt, row, trial, turn, args, workdir, extra):
    """Run the agent command once with `prompt`; returns (stdout text, error note or '')."""
    argv = runner_cmd.build(args.agent_cmd, prompt, extra)
    env = {"EVAL_PROMPT_ID": row.id, "EVAL_TRIAL": str(trial), "EVAL_TURN": str(turn)}
    try:
        code, out, err = runner_cmd.run(argv, timeout=args.timeout, env=env, cwd=workdir)
    except FileNotFoundError as error:
        raise SystemExit(f"run_evals: cannot start the agent command ({error}). Is it installed? Set --agent-cmd.") from None
    return out, "" if code == 0 else f"agent command exited {code}: {err.strip()[:120]}"


def run_agent(row, trial, args, workdir):
    """Run the agent for one row; returns (stdout text, error note or '').

    A row with a `reply` runs in two turns in one working directory: the first call answers `prompt` (and loads the skill), the
    second resumes that session with `reply`. The trace is both streams, one after the other.
    """
    # the agent runs in another directory, so a relative plugin path is resolved here, against where the runner was started
    extra = ["--plugin-dir", str(Path(args.skill_path).resolve())] if args.skill_path else []
    out, note = run_turn(row.prompt, row, trial, 1, args, workdir, extra)
    if not row.reply:
        return out, note
    session = trace.session_id(trace.parse(out)[0])
    if note or not session:
        return out, note or "turn 1 printed no session id, so the follow-up cannot resume it"
    second, second_note = run_turn(row.reply, row, trial, 2, args, workdir, extra + ["--resume", session])
    return out + second, second_note


def grade_trial(row, events, args, skill_text):
    case = checks.Case.build(row, events, args.skill)
    results = [{"id": cid, "pass": ok, "notes": note} for cid, ok, note in checks.run_checks(case)]
    turns, cost = trace.stats(events)
    verdict, judge_errors = None, []
    if args.judge:
        verdict, judge_errors = judge.grade(row, case.reply, args.judge_cmd, args.skill, skill_text, args.timeout)
    ok = all(r["pass"] for r in results)
    if args.judge:
        ok = ok and verdict is not None and verdict["overall_pass"]
    return {"passed": ok, "checks": results, "judge": verdict, "judge_errors": judge_errors, "turns": turns, "cost": cost}


def apply_isolation(args):
    """The default agent command runs isolated and loads the bundled skill; --user-setup runs with the machine's own setup.

    A custom --agent-cmd is left as it is, and so is --skill-path when the caller gave one.
    """
    if args.agent_cmd != DEFAULT_AGENT_CMD:
        return
    if args.user_setup:
        args.agent_cmd = AGENT_CMD
    elif not args.skill_path and args.skill == "oaat" and BUNDLED_SKILL.exists():
        args.skill_path = str(BUNDLED_PLUGIN)


def run_all(rows, args):
    skill_file = args.skill_file or (BUNDLED_SKILL if args.skill == "oaat" and BUNDLED_SKILL.exists() else None)
    skill_text = Path(skill_file).read_text(encoding="utf-8") if skill_file else None
    if not args.fixtures:
        apply_isolation(args)
    out_dir = Path(args.out) if args.out and not args.fixtures else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    workdir = args.workdir or tempfile.mkdtemp(prefix="oaat-eval-")
    prompts = []
    for row in rows:
        trials = []
        for trial in range(1, args.trials + 1):
            note = ""
            if args.fixtures:
                events = fixture_events(args.fixtures, row.id, trial)
                if events is None:
                    events, note = [], f"no recorded trace for {row.id} in {args.fixtures}"
            else:
                text, note = run_agent(row, trial, args, workdir)
                if out_dir:
                    trace_path(out_dir, row.id, trial, args.trials).write_text(text, encoding="utf-8", newline="\n")
                events = trace.parse(text)[0]
            result = grade_trial(row, events, args, skill_text)
            result.update(trial=trial, run_note=note)
            trials.append(result)
        prompts.append({"id": row.id, "kind": row.kind, "expect": row.expect, "should_trigger": row.should_trigger, "trials": trials})
    return {"label": args.label, "skill": args.skill, "trials": args.trials, "judge": bool(args.judge), "prompts": prompts}


def count(prompt):
    passed = sum(1 for t in prompt["trials"] if t["passed"])
    return passed, len(prompt["trials"])


def failed_ids(prompt):
    ids = []
    for t in prompt["trials"]:
        for c in t["checks"]:
            if not c["pass"] and c["id"] not in ids:
                ids.append(c["id"])
        if t["judge"] is None and t["judge_errors"]:
            ids.append("judge")
        elif t["judge"] is not None and not t["judge"]["overall_pass"] and "judge" not in ids:
            ids.append("judge")
    return ids


def format_report(results):
    """The per-prompt table, the failures, the per-group counts and the summary line, as a list of lines."""
    w = max([len(p["id"]) for p in results["prompts"]] + [2]) + 2
    lines = [f"{'id':<{w}}{'kind':<12}{'expect':<9}{'trigger':<9}{'trials':<8}{'result':<8}failed checks"]
    for p in results["prompts"]:
        passed, total = count(p)
        failed = failed_ids(p)
        lines.append(f"{p['id']:<{w}}{p['kind']:<12}{p['expect']:<9}{'yes' if p['should_trigger'] else 'no':<9}"
                     f"{f'{passed}/{total}':<8}{'PASS' if passed == total else 'FAIL':<8}{', '.join(failed) or '-'}")
    problems = []
    for p in results["prompts"]:
        for t in p["trials"]:
            if t["run_note"]:
                problems.append(f"  {p['id']} trial {t['trial']}: {t['run_note']}")
            for c in t["checks"]:
                if not c["pass"]:
                    problems.append(f"  {p['id']} trial {t['trial']}: {c['id']}: {c['notes']}")
            for error in t["judge_errors"]:
                problems.append(f"  {p['id']} trial {t['trial']}: judge: {error}")
            if t["judge"] is not None:
                for c in t["judge"]["checks"]:
                    if not c["pass"]:
                        problems.append(f"  {p['id']} trial {t['trial']}: judge {c['id']}: {c['notes']}")
    if problems:
        lines += ["", "failures:"] + problems
    groups = {}
    for p in results["prompts"]:
        for t in p["trials"]:
            for c in t["checks"]:
                done, total = groups.get(checks.GROUPS[c["id"]], (0, 0))
                groups[checks.GROUPS[c["id"]]] = (done + c["pass"], total + 1)
    lines += ["", "checks passed by group: " + ", ".join(f"{g} {groups[g][0]}/{groups[g][1]}" for g in ("outcome", "process", "style", "efficiency") if g in groups)]
    trials = [t for p in results["prompts"] for t in p["trials"]]
    prompts_ok = sum(1 for p in results["prompts"] if count(p)[0] == count(p)[1])
    trials_ok = sum(1 for t in trials if t["passed"])
    costs = [t["cost"] for t in trials if t["cost"] is not None]
    if costs:
        lines.append(f"cost reported by the result events: ${sum(costs):.4f} over {len(costs)} runs")
    rate = 100 * trials_ok // len(trials) if trials else 0
    lines.append(f"summary: prompts passed {prompts_ok}/{len(results['prompts'])} (every trial), trials passed {trials_ok}/{len(trials)}, pass rate {rate}%")
    return lines


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--prompts", default=str(UNIT / "evals" / "prompts.csv"), help="the prompt CSV")
    parser.add_argument("--out", default=str(UNIT / "artifacts"), help="where the traces and results.json go")
    parser.add_argument("--skill", default="oaat", help="the skill's name (default oaat)")
    parser.add_argument("--skill-path", help="a plugin directory that holds the skill; passed to the agent as --plugin-dir (default for oaat: the live plugins/oaat of this repository)")
    parser.add_argument("--skill-file", help="the skill's SKILL.md; the judge reads it (default for oaat: plugins/oaat/skills/oaat/SKILL.md)")
    parser.add_argument("--user-setup", action="store_true", help="run with your own setup: the user-level CLAUDE.md, settings and installed plugins load, "
                        "and the skill is the one you installed (default: isolated, with the bundled skill)")
    parser.add_argument("--agent-cmd", default=DEFAULT_AGENT_CMD, help=f"default: {DEFAULT_AGENT_CMD}")
    parser.add_argument("--trials", type=int, default=1, help="runs per prompt (the model is not deterministic)")
    parser.add_argument("--only", help="comma-separated prompt ids to run")
    parser.add_argument("--judge", action="store_true", help="add the rubric judge (one more model call per trial)")
    parser.add_argument("--judge-cmd", default=DEFAULT_JUDGE_CMD, help=f"default: {DEFAULT_JUDGE_CMD}")
    parser.add_argument("--fixtures", help="grade recorded traces from this directory instead of running the agent")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per model call")
    parser.add_argument("--workdir", help="working directory of the agent (default: a fresh empty temporary directory)")
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
        rows = cases.load_prompts(args.prompts)
    except (OSError, ValueError) as error:
        print(f"run_evals: {error}", file=sys.stderr)
        return 2
    if args.only:
        wanted = [name for name in args.only.split(",") if name]
        unknown = [name for name in wanted if name not in {r.id for r in rows}]
        if unknown:
            print(f"run_evals: no prompt with id {', '.join(unknown)}", file=sys.stderr)
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
