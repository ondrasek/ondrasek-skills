# Fixture traces

Every file under `traces/after/` and `traces/before/` is **INVENTED**. No model produced it, and none of it is a recording of a real `claude` run. `traces/real/` is the exception: one real run, described at the end.

- `traces/after/<id>.jsonl`: what a good version of the `oaat` skill could produce for the prompt `<id>` of `evals/prompts.csv`.
- `traces/before/<id>.jsonl`: the same run with five planted defects (see the docstring of `tests/build_fixtures.py`).

The events follow the stream-json shape that `python/agent_trace.py` reads. The marks of invention: `session_id` starts with
`INVENTED`, the model is `invented-model`, the Claude Code version is `INVENTED`, and the costs and durations are made up.
`tests/build_fixtures.py` writes the files (`python3 tests/build_fixtures.py`); `tests/test_fixtures.py` checks that the
committed files equal its output.

`traces/real/explicit-name.jsonl` is a REAL trace: one `claude -p` run on 2026-11-04 (Claude Code 2.1.318, `claude-sonnet-5-5`), cut by `tests/trim_real_trace.py` to the fields the checks read. Its `session_id` starts with `REAL`. `tests/test_fixtures.py` (`RealTraceTests`) states which checks it fails and why; the README of the unit, section "The first real run", tells the story.
