# evals/_common

The two modules every eval harness under `evals/` imports. Standard-library Python 3, no dependencies, no model call.

- `python/agent_trace.py` is the stream-json reader: it parses a Claude Code trace and answers questions about it (skills invoked, final reply, session id, `stats()`).
- `python/runner_cmd.py` is the command runner: it turns a command string plus a prompt into an argument list (`build`) and runs it with a timeout (`run`).

All six harnesses (oaat, agent-md-audit, auto-commit, block-force-push, milestones, sitrep) import them through a `sys.path` insert in `python/run_evals.py` and `tests/helpers.py` (oaat also in `demo.py`, `judge.py`, `measure.py`). Library modules such as `checks.py` do not touch the path.

## The rule

Fix a reader or runner bug **here**, then run `make -C evals/_common test-all`. Never put a copy back into a harness: `tests/test_layout.py` fails when one exists. Never add another module here whose name a harness also has (`cases`, `checks`, `state`, `helpers`, ...): `_common` is first on `sys.path` and would shadow the harness's own.

A harness directory does not run on its own: it needs this sibling.

## Cost semantics (measured, not documented)

`stats(events)` returns `(num_turns, total_cost_usd)` of the **last** result event (the cost: of the last one that carries a number). In the 22 recorded two-result traces (`evals/oaat/artifacts*`, `evals/oaat/python/artifacts-*`, `evals/sitrep/artifacts/explicit-report.jsonl`; local, git-ignored) the second result event's `modelUsage` equals the sum of both turns' usage, and its `total_cost_usd` is the session total (sitrep `explicit-report`: 0.0944, then 0.1238, same `session_id`). `num_turns` and `duration_ms` are per invocation. A sum over the result events therefore counts the first turn twice.

This rests on those traces, not on the Claude Code documentation. Not covered: `--fork-session`, and a resume that silently starts a new session id. If a future CLI reports a per-invocation cost on resume, the last-event rule would under-count; re-check by comparing the last result's `modelUsage` with the sum of the turns' usage.
