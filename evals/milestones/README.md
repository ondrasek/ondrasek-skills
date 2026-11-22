# Evals of the `milestones` plugin

Run the plugin on fixed prompts in throwaway git repositories and grade what it left behind. The ground truth is the workspace and its git state (the tracker file, `HEAD`, which paths git ever held, which files changed); the transcript is secondary evidence, because it shows only the main conversation.

**Status: the offline part is verified, the real run was never executed.** The unit tests (166) exercise the checks on invented traces and the whole runner against a stub agent in real temporary git repositories. Nobody has run `make eval` against a live `claude`: every fact in "Unconfirmed" below is open until the first real run, and the regex checks may need loosening after the first real trace.

## What this adds to `tests/milestones/`

`tests/milestones/` (68 tests) covers the script, the schema-vs-validator agreement, the statusline and the reminder script's firing conditions, all with invented payloads and no model. The evals add what only a live session shows:

1. the skill **triggers** on the description's phrasings and not on near misses;
2. the agent **translates a sentence into tracker state** (ids, states, `needs`) and the file ends valid against the plugin's schema;
3. it writes the tracker **only through `milestones.py`**, never with Edit/Write or a shell redirect, and never commits it;
4. in a real session the **Stop reminder** leads to an update when a milestone did finish and to no change when nothing happened.

The statusline is not evaluated: it is a user setting with no model behaviour.

## Why not `claude plugin eval`

Docs ([code.claude.com/docs/en/plugin-evals.md](https://code.claude.com/docs/en/plugin-evals.md), checked 2026-11-22 **as reported by the research that planned this harness; not re-read when it was built**) are reported to lack: a git-state or hash grader (so "the tracker is byte-identical", "nothing was committed" and "no stray file" cannot be asserted), a custom-code grader (the schema check, the `started`/`finished`/`updated` bookkeeping and the tri-state verdict are code), and offline grading (the graders could not be unit-tested without model calls). A scripted second turn, the fourth gap the sibling harnesses cite, does not matter here: every case is one turn.

## The cases (`cases/cases.csv`)

| id | fixture | expect | tests |
|---|---|---|---|
| `show-where-are-we` | tracker | show | implicit trigger on "where are we"; `show` is read-only; the reply names the milestone in progress |
| `show-progress-bar` | tracker | show | the shortest trigger phrase of the description |
| `update-finish-and-start` | tracker | update, `2=done,3=doing` | a sentence becomes state, through the script, with `finished`/`started` written |
| `update-blocked-needs-you` | tracker | update, `4=blocked,4.needs=you` | blocked plus `needs` naming the user (`you`, `author`, `me` or `user`; not `legal`), the field behind the statusline flag |
| `create-tracker` | bare | create, `title=Importer,count=3,names=...` | starting a tracker with `init --empty`/`title`/`add` rather than writing JSON |
| `negative-code` | tracker | none | unrelated work in a repository that has a tracker: no trigger, tracker byte-identical and not committed, only `src/textutil.py` changed |
| `negative-explain` | bare | none | the word "milestone" alone must not trigger |
| `negative-github` | bare | none | near miss: milestones of another system (`gh`) |
| `reminder-finishes` | stale-task | reminded-update, `2=done` | the Stop reminder turns finished work into a tracker update although the prompt never mentions the tracker |
| `reminder-nothing-changed` | stale-idle | reminded-quiet | "if nothing changed, do nothing": the reminder must not provoke an invented update |

Fixtures are built in code (`python/fixtures.py`) in a temporary directory, never stored: a committed `.cache/milestones.json` under `evals/` would be read by the plugin's own hook and statusline, a `CLAUDE.md` or `.claude/` by Claude Code, for anyone working in this repository. A test walks this directory and fails if one is there.

- `bare`: `README.md`, `src/textutil.py` (one function) and a `.gitignore` holding `.cache/`; no tracker.
- `tracker`: bare plus a tracker, "To release 1.0": 1 Schema design done, 2 Storage layer doing (started 3 h ago), 3 Sync API todo, 4 Release sign-off todo.
- `stale-task`: a tracker whose item 2 is "Add slugify to textutil", doing.
- `stale-idle`: the `tracker` fixture again.

Both stale fixtures get their tracker mtime set two hours back with `os.utime` **after** the fixture commit, so the commit is newer than the tracker (the hook's activity condition). Staleness therefore comes from the mtime, not from `MILESTONES_STALE_MINUTES`. A test runs the real `milestones-reminder.sh` on the fixtures (when `jq` is installed): it exits 2 on both stale ones and 0 on `tracker`.

The reminder prompts never mention git, commits, saving, milestones or a tracker, so an outcome can only come from the hook's report.

## The checks (`python/checks.py`)

Every check is a pure function of the case. Which ones apply depends on the case's `expect`.

- **precondition** (a failure makes the trial **INCONCLUSIVE**, not FAIL): `agent-completed` (runs in **every** case: no run note such as a non-zero exit or a timeout, a result event that is present, has subtype `success` and no `is_error`, a non-empty reply, and both snapshots present with files; without it a run that died after the init event passes every negative case, because it leaves the files untouched); `not-permission-blocked` (no denial of a Bash call running `milestones.py`, unless an identical call, path and quotes aside, later succeeded in a tool_result without `is_error`); `reminder-fired` (the marker `.cache/.milestones-reminded` exists after and not before: proof the plugin's hook ran, independent of how the trace encodes it); `reminder-delivered` (the hook's report is **in the conversation** (a user, assistant or result event), **or** the tracker changed, whatever the change; a Stop `hook_response` with exit code 2 and nothing else is not delivery, because a headless run that does not continue on Stop would show exactly that).
- **process:** `skill-loaded` (system/init lists the skill and has exactly **one** `milestones` entry in `plugins[]`, and that entry is the `--plugin-dir` copy: source `milestones@inline`, or a path equal to `--plugin`; an installed copy, or an installed plus an inline one, fails); `skill-trigger` (a Skill call for `milestones` exists exactly when the case says; not graded in the reminder cases); `shown-by-script` (a Bash command runs `milestones.py show`, also with a quoted path, a `cd ... &&` prefix or `--json`, **and its tool_result holds the tracker's title and is not an error**: a failed call followed by a hand-formatted table fails); `written-by-script` (when the tracker changed, a Bash command ran `set`, `add`, `remove`, `title` or `init`).
- **gate:** `show-reads-only` (tracker sha256 unchanged); `no-direct-tracker-write` (no Edit/Write/NotebookEdit/MultiEdit of a path ending `milestones.json`, and no Bash command segment that writes the file: the command line is split on unquoted `&&`, `||`, `;`, `|` and newlines, and a segment naming the tracker fails on a redirect into it, `tee`/`mv`/`rm`/`truncate`/`sed -i`, `cp`/`install`/`ln` with the tracker as destination, or a Python `open(..., 'w')`, `write_text`, `json.dump(`, `shutil` or `os` file move. A segment that runs the script is checked for a redirect only. Reads such as `cat .cache/milestones.json` or `json.dumps(json.load(open(...)))` pass. A denied attempt still counts: the trace holds attempts); `tracker-not-committed` (no `.cache/` path in `git ls-files` or anywhere in `git log --all`, and, in the show, update, create and none cases, `HEAD` did not move: in the reminder cases an agent may legitimately commit its own edit); `nothing-else-changed` (no file added, removed or modified except the tracker and the hook's marker: **any other file under `.cache/` counts**; the code file `src/textutil.py` may change in `reminder-finishes` and in a `none` case on a tracker fixture); `no-tracker-when-negative` (no tracker appears on the bare fixture).
- **outcome:** `reply-names-current` (the reply names the milestone in progress and does not say it is finished: the name followed at once by finished, complete(d), done, shipped or a check mark fails; heuristic, an unusual phrasing of a wrong status can pass); `tracker-valid` (parses and passes `python/schema_check.py`, a standard-library validator driven by the plugin's `milestones.schema.json`, plus unique ids); `wanted-state` (the case's `want` ids, states and `needs`: `ID.needs=set` is any non-empty value, `ID.needs=you` must start with you, author, me or user); `others-untouched` (items not named are equal to before; items named keep their name; none removed or renumbered; same title); `timestamps-by-script` (an item that became doing has `started`, one that became done has `finished`, and the top-level `updated` was refreshed: bookkeeping only the script does); `tracker-created`; `quiet-when-nothing-changed`.

A `none` case runs `agent-completed`, `skill-loaded`, `skill-trigger`, `no-direct-tracker-write`, `tracker-not-committed` and `nothing-else-changed`, plus `show-reads-only` on a fixture that has a tracker (the prompt may edit `src/textutil.py`) or `no-tracker-when-negative` on `bare`. The branch comes from the row's fixture, never from the snapshots.

**Verdict.** PASS when every check passes; **INCONCLUSIVE** when a precondition check fails (the behaviour was never exercised, so no other failure is blamed on the plugin: that includes a gate failure that a permission denial may have provoked); FAIL otherwise. The report prints INCONCLUSIVE apart from FAIL, and the exit code is 1 for either. How the attribution rule plays out in the reminder cases: no hook report in the conversation and an unchanged tracker is INCONCLUSIVE (a blocking `hook_response` alone does not help); the report in the conversation and no outcome is FAIL; a **changed tracker** without the report passes `reminder-delivered` with the note "attributed by state only", and the outcome checks then grade the change, so a wrong or invented update is a FAIL, not an INCONCLUSIVE. A run that did not complete (`agent-completed`: run note, error or missing result, no reply, missing snapshot) is INCONCLUSIVE in every case, offline regrades included: `state.json` also stores the run note, and a missing, unreadable or empty `state.json` is INCONCLUSIVE too.

Every check has an invented run that fails it, and a test fails when one has none (`tests/test_checks.py`, `MUTATIONS`; `test_every_check_is_mutated`). Each invented run fails exactly its own check. Regexes are tested against passing and failing commands.

## Run

```
make -C evals/milestones test                                              # offline, free, about 20 s
make -C evals/milestones eval ARGS="--only reminder-finishes --trials 1"   # REAL: one run, the canary
python3 evals/milestones/python/run_evals.py --fixtures DIR                # grade recorded runs offline (needs <id>.jsonl and <id>.state.json)
```

`--keep` keeps each trial's temporary directory (default: removed after the last snapshot); `--agent-cmd` replaces the whole command.

## A real run

- **Cost:** model calls on your own account; not measured yet. Each call is capped by `--max-budget-usd 2` (a ceiling per call, not an estimate). The suite is 10 single-turn cases. **Start with one case and `--trials 1`; the canary is `reminder-finishes`**: it is the only one that needs the Stop hook to run, to be delivered to the model and to be acted on, so it shows within one run which of the unconfirmed facts below hold. If it comes back INCONCLUSIVE, read the failures section of the report before spending on the rest.
- **Isolation:** each trial runs in `<tmp>/milestones-eval-<id>-*/ws`, a git repository whose **local** config sets identity, `commit.gpgsign=false`, `tag.gpgsign=false` and `core.hooksPath` to an empty directory beside the workspace; the child also gets `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` set to the null device. The command starts with `env -u` for `CLAUDE_PROJECT_DIR`, `CLAUDE_HOOK_RUNNING`, `MILESTONES_FILE`, `MILESTONES_NOW` and `MILESTONES_STALE_MINUTES` (the runner merges `os.environ` and cannot unset), because an inherited `CLAUDE_PROJECT_DIR` would point the hook and the script at the repository you started the harness from. It uses `--setting-sources project,local` and never `--bare` (which skips plugin hooks). The user's global gitignore does not apply in the fixture; the fixture ships its own `.gitignore`.
- **Permissions:** `--permission-mode dontAsk`. Edit and Write are granted on purpose, and so are `git add` and `git commit`, so that a direct tracker write or a commit of the tracker can happen and be measured. Plainly: the remote is not involved (there is none), but if headless Bash is not sandboxed, the agent's `git` runs with your rights in a temporary directory. Whether the sandbox applies is unconfirmed. The script is granted as `Bash(python3 *milestones.py *)`; if that pattern does not match the quoted absolute path the skill uses, the denial shows up as `not-permission-blocked` and the trial is INCONCLUSIVE.
- **Traces:** `artifacts/` (git-ignored) holds `<id>.jsonl`, `<id>.state.json` (before and after snapshots with the text of every small file) and `results.json`. They contain your paths and the model's session; review before sharing.
- **Needs `jq`** on the PATH for the reminder hook (the hook exits 0 silently without it, so `reminder-fired` would report INCONCLUSIVE).
- **Timeout:** 600 s per call, unmeasured.

## Limits

- `no-direct-tracker-write` and `written-by-script` read the trace, which shows only the main conversation. A subagent's write is caught by `timestamps-by-script` (state) only; a careful hand edit that fakes the timestamps is caught by nothing.
- `reminder-finishes` depends on the model judging that the slugify edit completes the milestone "Add slugify to textutil" (the names are made to match closely); expect variance and use `--trials`. The skill may also trigger without the hook if the agent reads `.cache`; `reminder-fired` proves the hook ran, not that it caused the update.
- The trigger cases deliberately avoid a `/milestones` prompt: the plan assumes a slash command expands without a Skill tool call (unconfirmed, not observed here), in which case the trigger check would fail on correct behaviour. Explicit invocation is not covered.
- `schema_check.py` is a second implementation of the schema beside `milestones.py validate()`. A test fails when the schema file uses a keyword it does not implement and compares both validators on a list of valid and invalid trackers; semantic disagreement outside that list is possible. It always reads the repository's schema, not the one under `--plugin`.
- `fed_to_model` matches the hook's text anywhere in a user, assistant or result event: an agent that reads `milestones-reminder.sh` would produce false evidence.
- `no-direct-tracker-write` is a pattern match on a command line, not a shell parser: a write hidden in a script file, a variable path (`open(p, 'w')` where `p` is set in another segment) or a decoded string passes. A quote left open in a heredoc body can merge segments.
- `reply-names-current` reads wording; `claims_done` knows a handful of phrasings.
- A recorded trace from another machine regrades with the same verdicts only when its `plugins[]` entry has source `milestones@inline` (otherwise pass `--plugin` with the recorded path).

## Unconfirmed

Nothing below was observed in a real run of this harness. The shapes marked "recorded" were read from the recorded traces in `evals/oaat/artifacts/` (Claude Code 2.1.319, another plugin) and not copied.

- **Whether a blocking Stop hook continues the session inside one `claude -p` call.** `reminder-delivered` degrades to INCONCLUSIVE if not.
- **How its report reaches the stream-json trace.** The stub's encoding (a user text event) is invented. The field names of a `hook_response` system event (`hook_event`, `exit_code`), the shape of a `permission_denials` element and whether a system `permission_denied` event exists are undocumented here; the code matches on text and tolerates missing fields. `--include-hook-events` is a flag in `claude --help` (2.1.330); what it emits was not seen.
- **Whether hook processes inherit the `claude` process environment.** The harness does not depend on it: no `MILESTONES_STALE_MINUTES`, mtime backdating instead, and `env -u` for the variables above.
- **Whether `CLAUDE_PROJECT_DIR` in a `-p` run equals the working directory.** If not, the hook and the script look for the tracker elsewhere and `reminder-fired` is INCONCLUSIVE.
- **The spelling of the plugin skill.** The init event's `plugins[]` (`{"name", "source": "<name>@inline"}`) and `skills[]` (`oaat:oaat`) and the Skill call's `input.skill` were seen in recorded traces for `oaat`; `milestones:milestones` is by analogy. `skill_matches` compares after the last colon.
- **Whether the skill's `allowed-tools` pre-approves the script call in a headless `dontAsk` run, and whether `Bash(python3 *milestones.py *)` matches a quoted absolute path.** Reported as INCONCLUSIVE on a denial.
- **Whether `--setting-sources project,local` keeps your installed `milestones` plugin and user-level Stop hooks out.** If the user copy loaded too, the hook could run twice.
- **Whether headless Bash is sandboxed**, and whether git there sees the fixture's local config as intended.
- **Whether a headless run creates `.claude/.cc-writes/` in the workspace** (only that path is ignored by the snapshot).
- **Whether a `/milestones` prompt produces a Skill tool call.** Assumed not; the cases avoid the slash form.
- **What `--max-budget-usd 2` does when it is hit** (exit code, result subtype) and what a run costs. `agent-completed` treats any non-zero exit, a result subtype other than `success` or `is_error` as INCONCLUSIVE, whichever the CLI does.
- **The shape of a failed Bash tool_result** (`is_error` set on a non-zero exit or a denied call, the text of a denial) and **whether `permission_denials` elements carry `tool_use_id`.** `shown-by-script` and `not-permission-blocked` read both; a recorded tool_result has `content` as a string and no `is_error` when the call succeeded.
- **That `milestones.py show` prints the tracker's title in a Bash tool_result** (it does when run by hand, 2026-11-22; a long output could be truncated by the CLI).

## Provenance

`python/agent_trace.py` and `python/runner_cmd.py` are byte-identical copies of the files in [`evals/agent-md-audit/python/`](../agent-md-audit/python) (themselves copies of [`evals/oaat/python/`](../oaat/python)); `tests/test_plugin.py` fails if either drifts, and skips if the source is missing. If you fix one copy, fix all of them. `agent_trace.stats()` sums `total_cost_usd` over every result event, which double-counts a resumed session; the copies are not fixed (they stay byte-identical), `run_evals.py` takes the cost of the **last** result event instead. `python/run_evals.py`, `state.py`, `cases.py`, `fixtures.py` and `checks.py` are adapted from agent-md-audit; `hook_trace.py` and `schema_check.py` are new and local. Nothing in `evals/oaat` or `evals/agent-md-audit` was changed.
