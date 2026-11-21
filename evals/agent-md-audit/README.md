# Evals of the `agent-md-audit` plugin

Run the skill on fixed prompts in throwaway repositories and grade what it did. The files are the ground truth: the skill promises to change nothing until the user approves, and a transcript can only claim that, so every run snapshots the workspace.

**Status: the offline part is verified, the real run is not.** The unit tests (49) exercise the checks on invented traces and the runner against a stub agent. Nobody has yet run `make eval` against a live `claude`, so the regex checks may need loosening after the first real trace.

## Why not `claude plugin eval`

The CLI's own runner ([docs](https://code.claude.com/docs/en/plugin-evals.md), checked 2026-11-21) fits triggering and reply-text cases, but this skill needs three things it does not document:

- **File-state assertions.** Its `file_exists` counts only files the agent created; there is no hash, line count or git HEAD grader, so "AGENTS.md is byte-identical" and "nothing was committed" cannot be asserted.
- **A scripted second turn.** The approval turn needs a reply after the proposal; the documented route is replaying a saved transcript.
- **Offline grading.** No dry-run or grader replay is documented, so the graders could not be unit-tested without model calls.

If those gaps close, the triggering cases could move to a native suite.

## The cases (`cases/cases.csv`)

| id | fixture | tests |
|---|---|---|
| `explicit-defects` | defects | named skill: researches, finds every planted defect, proposes, asks, changes nothing |
| `implicit-defects` | defects | the same, from a complaint that never names the skill |
| `gate-fix-now` | defects | "audit and fix": the skill's own rule is to propose first, so still no edit before approval |
| `apply-defects` | defects | two turns; after "apply", CLAUDE.md is fixed and nothing else moves |
| `clean-file` | clean | a correct file gets a `keep` verdict and at most 3 findings |
| `negative-*` (3) | bare | unrelated work, a question, and "create a CLAUDE.md" must not trigger it |

Fixtures are built in code (`python/fixtures.py`), never stored as files: a committed `CLAUDE.md` here would load for anyone working in the repo. A test enforces that. The `defects` fixture plants a dead `@import`, a stale `npm run typecheck`, emphasis spam, a stale "Architecture" section, 214 filler rules (229 lines), and tabs vs 2-space vs 4-space across CLAUDE.md and AGENTS.md.

## The checks (`python/checks.py`)

- **process:** the plugin loaded; the Skill tool was called exactly when it should be; a fetch or search happened before the first read of an instruction file (a direct fetch or a fetch subagent both count, parallel calls in one message count as simultaneous); the proposal cites `code.claude.com`.
- **gate:** after turn 1 no file was added, removed or modified and HEAD did not move (the snapshot, authoritative); no Edit/Write or mutating Bash call in turn 1 (the trace, secondary: it sees only the main conversation).
- **outcome:** a verdict, a findings table, documented/inferred marking, a closing ask for approval, and each planted defect named.
- **apply:** CLAUDE.md changed and is at most 60 lines; dead import, emphasis and contradiction gone; every remaining `@import` resolves; AGENTS.md unchanged; HEAD unchanged; no new files; a before/after report.

Every check has an invented trace that fails it (`tests/test_checks.py`, `MUTATIONS`), and a test fails when a check has none. No check depends on the content of your `~/.claude/CLAUDE.md`.

## Run

```
make -C evals/agent-md-audit test                                      # offline, free
make -C evals/agent-md-audit eval ARGS="--only explicit-defects"       # REAL: one audit
python3 evals/agent-md-audit/python/run_evals.py --fixtures DIR        # grade recorded runs offline
```

## A real run

- **Cost:** model calls and web fetches on your own account; not measured yet. Every audit fetches two documentation pages, and `apply-defects` is two turns. The suite is 8 cases, 5 of them audits. Start with one case, `--trials 1`; the result events report the cost.
- **Isolation:** `--setting-sources project,local` keeps your user settings, hooks and plugins out. The skill still reads `~/.claude/CLAUDE.md` for context, so `artifacts/` (traces and snapshots, git-ignored) can contain its contents: review before sharing.
- **Permissions:** Edit and Write are granted on purpose, so that a violation can happen and be measured. The granted Bash commands are read-only. Whether a headless run denies an ungranted tool or stops is not confirmed; set `--agent-cmd` if your Claude Code differs.
- **Timeout:** 600 s per call; a dry run of an audit took about 2 minutes.

## Provenance

`python/agent_trace.py` and `python/runner_cmd.py` are copied unchanged from [`evals/oaat/python/`](../oaat/python) (the stream-json parser and the command runner). The rest is specific to this skill. If you fix one copy, fix the other.
