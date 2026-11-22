# Evals of the `agent-md-audit` plugin

Run the skill on fixed prompts in throwaway repositories and grade what it did. The files are the ground truth: the skill promises to change nothing until the user approves, and a transcript can only claim that, so every run snapshots the workspace.

**Status: verified offline and by a first real run of every case.** The unit tests (52) exercise the checks on invented traces and the runner against a stub agent. Real run (2026-11-22, Claude Code 2.1.330, outside the sandbox, 1 trial per case): **all 8 cases PASS** (`explicit-defects`, `implicit-defects`, `gate-fix-now`, `apply-defects`, `clean-file`, `negative-unit-test`, `negative-explain`, `negative-create`), 69 of 69 checks, about $1.1 in total (`explicit-defects` $0.19, the three-case batch with `apply-defects` $0.62, `clean-file` and the negatives $0.28). That run showed that the skill researches before reading, proposes without touching any file, and in the apply turn fixes CLAUDE.md, leaves AGENTS.md alone and commits nothing. Each case has had one trial: flakiness is unmeasured. Run real trials **outside the Claude Code Bash sandbox** (inside it Claude Code cannot save sessions, so the apply turn's `--resume` fails).

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

- **Cost:** model calls and web fetches on your own account, about $1.1 for the 8 cases once (see Status). Every audit fetches two documentation pages, and `apply-defects` is two turns. Start with one case, `--trials 1`; the result events report the cost.
- **Isolation:** `--setting-sources project,local` keeps your user settings, hooks and plugins out. The skill still reads `~/.claude/CLAUDE.md` for context, so `artifacts/` (traces and snapshots, git-ignored) can contain its contents: review before sharing.
- **Permissions:** Edit and Write are granted on purpose, so that a violation can happen and be measured. The granted Bash commands are read-only. No ungranted Bash command was attempted in the real run, so how the harness reports a denial is still unconfirmed here (the newer harnesses under `evals/` saw `dontAsk` deny an ungranted command and the agent carry on); set `--agent-cmd` if your Claude Code differs.
- **First thing to check when a real run fails** (all 8 cases passed on the first real run, so this is a caution, not an observed failure): the outcome checks read only the final result text of turn 1. If the model spreads the proposal over several messages, they fail although the behavior is fine; loosen the checks, not the skill.
- **Timeout:** 600 s per call; a dry run of an audit took about 2 minutes.

## Provenance

`agent_trace` (the stream-json parser) and `runner_cmd` (the command runner) are not in this directory: they are the shared modules in [`evals/_common/python/`](../_common/python), put on `sys.path` by `python/run_evals.py` and `tests/helpers.py`. Fix them there and run `make -C evals/_common test-all`; this directory does not run without that sibling. The rest is specific to this skill.
