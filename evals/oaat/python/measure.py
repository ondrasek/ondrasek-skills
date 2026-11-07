"""Measure what the deterministic checks cost: grading one recorded trace, and all of them. No key, no network.

    python3 measure.py

The checks are string work on a few hundred bytes; the cost of an eval run is the model calls, not the checks.
The numbers differ per machine.
"""
import statistics
import time
from pathlib import Path

import cases
import checks
import agent_trace as trace

UNIT = Path(__file__).resolve().parent.parent
REPEAT = 20


def grade_all(rows, directory):
    for row in rows:
        checks.run_checks(checks.Case.build(row, trace.load(directory / f"{row.id}.jsonl")))


def main():
    rows = cases.load_prompts(UNIT / "evals" / "prompts.csv")
    directory = UNIT / "tests" / "fixtures" / "traces" / "after"
    samples = []
    for _ in range(REPEAT):
        start = time.perf_counter()
        grade_all(rows, directory)
        samples.append((time.perf_counter() - start) * 1000)
    total = statistics.median(samples)
    print(f"grade {len(rows)} traces (parse, build, run every check): {total:.1f} ms (median of {REPEAT})")
    print(f"per trace: {total / len(rows):.2f} ms")


if __name__ == "__main__":
    main()
