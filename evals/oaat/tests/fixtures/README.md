# Fixture traces

Every file under `traces/after/` and `traces/before/` is **INVENTED**. No model produced it, and none of it is a recording of a real `claude` run. `traces/real/` is the exception: one real run, described at the end.

- `traces/after/<id>.jsonl`: what a good version of the `oaat` skill could produce for the prompt `<id>` of `evals/prompts.csv`.
- `traces/before/<id>.jsonl`: the same run with five planted defects (see the docstring of `tests/build_fixtures.py`).

- `traces/tool/<id>.jsonl`: two INVENTED one-turn walks of the tool path (items presented with `AskUserQuestion`; every call is answered by a `tool_result` before the next call, and a skip is the `tool_result` `Skip for now`), for `explicit-dollar` and `followup-skip`. The input shape of the tool is documented. The text of every `tool_result` uses the wrapper OBSERVED once in an interactive Claude Code session (2026-11-22): `Your questions have been answered: "<question>"="<answer>". You can now continue with these answers in mind.` (one pair per question; the label verbatim, a free-text answer in the same position). That is one sample on one version, so it stays UNCONFIRMED across versions; the stream-json nesting around it is assumed, and a headless run cannot call the tool, so no real trace exists. Both walks end with the free text `stop` and the wrap-up (a chosen label with items left must be followed by another call: `tool-early-end`). See the README of the unit, section "The tool path (AskUserQuestion)".

The events follow the stream-json shape that `evals/_common/python/agent_trace.py` reads. The marks of invention: `session_id` starts with
`INVENTED`, the model is `invented-model`, the Claude Code version is `INVENTED`, and the costs and durations are made up.
`tests/build_fixtures.py` writes the files (`python3 tests/build_fixtures.py`); `tests/test_fixtures.py` checks that the
committed files equal its output.

`traces/real/explicit-name.jsonl` is a REAL trace: one `claude -p` run on 2026-11-04 (Claude Code 2.1.318, `claude-sonnet-5-5`), cut by `tests/trim_real_trace.py` to the fields the checks read. Its `session_id` starts with `REAL`. `tests/test_fixtures.py` (`RealTraceTests`) states which checks it fails and why; the README of the unit, section "The first real run", tells the story.
