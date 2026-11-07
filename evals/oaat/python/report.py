"""Compare two runs of the evals (before and after an edit of the skill): per prompt better, worse or same.

    python3 report.py before/results.json after/results.json

A prompt is better when a larger share of its trials passes, worse when a smaller share passes, same otherwise.
Exit code 1 when any prompt is worse (a regression), else 0.
"""
import argparse
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {p["id"]: p for p in data["prompts"]}, data


def share(prompt):
    passed = sum(1 for t in prompt["trials"] if t["passed"])
    return passed, len(prompt["trials"])


def failing(prompt):
    return {c["id"] for t in prompt["trials"] for c in t["checks"] if not c["pass"]}


def compare(before, after):
    """[(id, verdict, before share, after share, checks that now fail, checks that were failing and no longer do)]."""
    rows = []
    for pid in list(before) + [p for p in after if p not in before]:
        if pid not in after:
            rows.append((pid, "gone", share(before[pid]), None, [], []))
        elif pid not in before:
            rows.append((pid, "new", None, share(after[pid]), [], []))
        else:
            (bp, bt), (ap, at) = share(before[pid]), share(after[pid])
            left, right = bp * at, ap * bt   # compare bp/bt with ap/at without division
            verdict = "better" if right > left else "worse" if right < left else "same"
            rows.append((pid, verdict, (bp, bt), (ap, at), sorted(failing(after[pid]) - failing(before[pid])),
                         sorted(failing(before[pid]) - failing(after[pid]))))
    return rows


def fmt(value):
    return "-" if value is None else f"{value[0]}/{value[1]}"


def format_report(rows, labels=("before", "after")):
    w = max([len(r[0]) for r in rows] + [2]) + 2
    lw = max(len(labels[0]), len(labels[1]), 6) + 2
    lines = [f"{'id':<{w}}{labels[0]:<{lw}}{labels[1]:<{lw}}{'verdict':<9}check changes"]
    for pid, verdict, b, a, newly, fixed in rows:
        changes = [f"now failing: {', '.join(newly)}"] if newly else []
        changes += [f"fixed: {', '.join(fixed)}"] if fixed else []
        lines.append(f"{pid:<{w}}{fmt(b):<{lw}}{fmt(a):<{lw}}{verdict:<9}{'; '.join(changes) or '-'}")
    tally = {v: sum(1 for r in rows if r[1] == v) for v in ("better", "worse", "same", "new", "gone")}
    lines.append("")
    lines.append("summary: " + ", ".join(f"{n} {v}" for v, n in tally.items() if n or v in ("better", "worse", "same")))
    return lines


def main(argv=None, stream=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args(argv)
    try:
        before, before_data = load(args.before)
        after, after_data = load(args.after)
    except (OSError, ValueError, KeyError) as error:
        print(f"report: {error}", file=sys.stderr)
        return 2
    rows = compare(before, after)
    labels = (before_data.get("label") or "before", after_data.get("label") or "after")
    print("\n".join(format_report(rows, labels)), file=stream or sys.stdout)
    return 1 if any(r[1] == "worse" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
