# Evals of the `sitrep` plugin

Run the skill on fixed prompts in throwaway directories and grade what it left behind. The files are the ground truth: the skill promises a temporary HTML report outside the project, made and removed by `report.sh`, with `index.html` untouched and nothing written into the repository. A transcript can only claim that, so every run snapshots the workspace and the report directory after each turn.

**Status: offline part verified, real run never executed.** The 168 unit tests exercise the checks on invented traces and the runner against a stub agent that runs the plugin's real `report.sh` in real temporary directories. Nobody has run `make eval` against a live `claude`: the regex checks may need loosening after the first real trace, and the facts under "Unconfirmed" decide whether a real run can say anything at all.

The plugin has no unit tests of its own (its only code is a 30-line shell script), so `tests/test_plugin.py` also exercises `report.sh` once against a throwaway `TMPDIR`: `new`, `clean`, what `clean` refuses, the day-old sweep.

## Why not `claude plugin eval`

The CLI's own runner ([docs](https://code.claude.com/docs/en/plugin-evals.md), checked 2026-11-22 as reported by the planning research; not re-read while building) fits triggering and reply-text cases. This skill needs what it does not document:

- **State assertions.** No git-state or hash grader, so "`index.html` equals the template", "the report directory is gone" and "the tracker is byte-identical" cannot be asserted.
- **A custom grader.** The report's `data.js` must be parsed and checked against `schema.md`; no custom-code grader is documented.
- **Offline grading.** No grader replay is documented, so the graders could not be unit-tested without model calls.
- **A scripted second turn.** The cleanup turn needs a reply after the report exists; the documented route is replaying a saved transcript.

## The cases (`cases/cases.csv`)

| id | fixture | turns | tests |
|---|---|---|---|
| `explicit-report` | open-items | 2 | the whole life cycle with the skill named: valid report, project untouched; after "I'm done" the directory is gone, removed by `report.sh clean`. **Canary: run this one first.** |
| `implicit-big-picture` | open-items | 1 | implicit trigger ("big picture ... as something visual rather than a list"); same report checks |
| `with-milestones` | open-items-ms | 1 | `.cache/milestones.json` is read, mapped as SKILL.md step 1 says (item `ms-3`, progress strip) and not written |
| `feedback-paste` | open-items | 1 | a pasted Copy-JSON export triggers the skill; the project is untouched; no report is required |
| `negative-plain-list` | open-items | 1 | near miss: the user wants exactly the text list the skill replaces |
| `negative-code` | bare | 1 | unrelated work (it edits a file, so no project check applies) |
| `negative-clean-tree` | bare | 1 | near miss on "state of things" |

Fixtures are built in code (`python/fixtures.py`) in a temporary directory, never stored as files. `open-items` holds `docs/open-questions.md` with five planted items (database decision, SSO vendor, font licence audit, data retention question, and a schema-migration blocker that waits for the database) and a README that points to it. `open-items-ms` adds a tracker (1 done, 2 doing, 3 blocked and needs you, 4 todo) and a `.gitignore` line for `.cache/`, because the user's global gitignore does not apply in the throwaway repo.

## The checks (`python/checks.py`)

- **precondition:** `run-completed` (every turn the case needs ended in a `success` result event that is not an error, the runner noted nothing such as a non-zero exit or a timeout, and the snapshot after turn 2 exists; without it a crashed run would pass every negative case, which only look for something absent); `report-located` (the harness found the report directory, from the path `report.sh new` printed, else a `sitrep.*` directory in the trial `TMPDIR`; it fails only when `report.sh new` was tried, or refused, and left nothing: a skill that never ran step 2 passes it and fails `created-by-script` instead); `not-permission-blocked` (no denial of `report.sh` or of a write of `data.js`, read from `result.permission_denials`, from system `permission_denied` events and from the `tool_result` text of a Bash, Write or Edit call that names `report.sh` or `data.js` and reads like a refusal: `permission`, `denied`, `not allowed`, `not granted`, `disallowed`, `requires approval`, `not permitted`; denials of `open`, `xdg-open`, `start` are expected, they are not granted).
- **process:** `skill-loaded` (plugin listed and its skill among the init skills; exactly one plugin named `sitrep`, whose init `path` is the run's `--plugin-dir`, recorded as `plugin` in the state file; every `report.sh` call in the trace lies under that directory, so an installed copy is not graded as the working copy; a recording without `plugin` skips the path tests); `skill-trigger' (a Skill call exactly when the case says so); `created-by-script` (`report.sh new` ran, no `mkdir` or `mktemp`); `cleaned-by-script` (turn 2 ran `report.sh clean` naming the directory and none of `rm`, `/bin/rm`, `rmdir`, `unlink`, `find ... -delete`, `rmtree` did, also in a compound command).
- **gate:** `report-outside-project`; `project-untouched` (snapshot after every turn equals the one before: files, HEAD, `git status` minus the `?? .claude/` marker; a missing snapshot after turn 2 is not a write, `run-completed` reports the turn that did not run); `index-untouched` (sha256 equals the plugin's `template.html`); `only-data-added` (the directory holds exactly `index.html` and `data.js`); `tracker-untouched` (it also trips `project-untouched`, by design: it names the cause); `no-report-when-negative` (no `sitrep.*` in the trial `TMPDIR`, no `report.sh` call and no report-like file added to the workspace: a name ending in `.html` or `.htm`, `data.js`, or containing `sitrep`; other edits are allowed, `negative-code` edits `src/textutil.py`).
- **outcome:** `data-defines-sitrep`, `data-parses`, `data-valid` (against `schema.md`), `items-cover-planted` (five distinct items, one per planted keyword, assigned one-to-one: a keyword that only appears in another item's detail does not count), `dependency-recorded` (each item is classified once, by its title and only then its detail, as migration or database; a migration item must list a database item in `dependsOn`, and that item must not list it back), `start-here-set`, `milestone-item`, `milestone-strip`, `reply-hands-over` (names the `sitrep.XXXXXX` directory of the recorded report, mentions Copy JSON, at most 4 list lines, and names at most 2 planted topics or item titles of `data.js`: a table or bold lines pasting every item are caught; a reply that only says `index.html` without the directory is not a hand-over), `report-cleaned`.

Which checks run for which case: every case gets `run-completed`; `none` cases get `skill-loaded`, `skill-trigger` and `no-report-when-negative`; `feedback` gets `skill-loaded`, `skill-trigger` and `project-untouched`; report cases get every other check except `no-report-when-negative`, with the milestone and tracker checks only in `with-milestones` and the cleanup checks (`cleaned-by-script`, `report-cleaned`) only in `explicit-report`.

**Verdict.** A trial is PASS, FAIL or INCONCLUSIVE. A failed precondition (`run-completed`, `report-located`, `not-permission-blocked`), or a plugin that did not load or is not the one under test (`skill-loaded`), makes it INCONCLUSIVE (the behaviour was never exercised, which says nothing about the skill) and the report still lists every failed check of that trial, because the gate and outcome failures may follow from it. Three failures stand on their own and make the trial FAIL anyway: `project-untouched` (a write into the project does not depend on a report existing), `index-untouched` (only when `report.sh` ran and its directory was found, so the page was edited) and `skill-trigger` (the skill was not called when it should have been), the last only when the plugin did load and the run finished (a crashed run says nothing about triggering). Exit code 1 for FAIL or INCONCLUSIVE; a run that crashed is never green.

Every check has an invented failing run (`tests/test_checks.py`, `MUTATIONS`; `MORE_MUTATIONS` holds further failing runs for the same checks: a reversed dependency, `/bin/rm`, a refusal visible only in a tool result, a pasted table, and so on), a test fails when a check has none, and each mutation must fail exactly its own check plus a cascade that is written down next to it (no report found, unparsed data, the tracker also being part of the project). Cost is read from the last result event of a trial, on the unconfirmed assumption (item 9 below) that a resumed session reports its whole cost there; `agent_trace.stats` sums over result events and would count turn 1 twice if so.

## Run

```
make -C evals/sitrep test                                          # offline, free
make -C evals/sitrep eval ARGS="--only explicit-report --trials 1" # REAL: model calls
python3 evals/sitrep/python/run_evals.py --fixtures DIR            # grade recorded runs offline
```

`--keep` keeps each trial root (workspace, `TMPDIR` with the report) for inspection; by default it is deleted after the last snapshot.

## A real run

- **Cost:** model calls on your own account; not measured yet. Each call is capped at `--max-budget-usd 2`. `explicit-report` is two calls, the other six are one each. Start with one case, `--trials 1`, and read the cost from the result events: `explicit-report` first, because it exercises the whole life cycle.
- **Isolation:** every trial runs in a fresh temporary root: `ws` (the cwd, a git repo whose local config sets identity, no signing and an empty hooks directory), `tmp` (the child's `TMPDIR`, granted with `--add-dir`, so `report.sh` is meant to create and sweep `sitrep.*` only there: item 3 below), `hooks`. `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` point at nothing. `--setting-sources project,local` is meant to keep your user settings, hooks and plugins out (item 12 below), and `env -u` removes `CLAUDE_PROJECT_DIR`, `CLAUDE_HOOK_RUNNING` and the `MILESTONES_*` variables so that nothing looks at the repository the harness was started in. `--bare` is not used: `claude --help` says it skips hooks, plugin sync and more, and what that does to `--plugin-dir` has not been checked.
- **Permissions, stated plainly:** `Write` and `Edit` are granted on purpose, so a write into the project can happen and be measured. The granted Bash commands are `report.sh` (two spellings, because SKILL.md puts the closing quote of the script path before the verb), `ls` and `cat`; `open`, `xdg-open` and `start` are not granted, which should keep a browser window from opening (if `dontAsk` denies what is not granted: item 13). If headless Bash turns out to be unsandboxed, those commands run with your user's rights, with a temporary directory as cwd.
- **Artifacts** (`artifacts/`, git-ignored: traces, state snapshots, `results.json`) may contain whatever your own user-level memory put into the session: review before sharing.
- **First things to check when a real run fails or is inconclusive:**
  1. INCONCLUSIVE on every report case: read the "Unconfirmed" list, items 1 to 3 first.
  2. `feedback-paste` may fail `project-untouched` although the behaviour is good: SKILL.md step 4 says to apply decisions the agent can apply, and "go with Postgres" may be written into `docs/open-questions.md`. If so, narrow the check, not the skill.
  3. `items-cover-planted` and `dependency-recorded` match keywords; the skill allows merging items, so a model that merges the database decision with the migration blocker fails them while behaving well. Loosen to four of five.
  4. `reply-hands-over` is a text check on one reply and the likeliest to need loosening.
  5. `implicit-big-picture` and `with-milestones` do not name `docs/open-questions.md`; the fixture README points to it, but the agent may not read it.
- **Timeout:** 300 s per model call.

## Limits of the reader

`data.js` is read by a small pure-Python reader (`python/sitrep_data.py`); no JavaScript is ever executed, no `node`. It reads what data files use: comments, bare keys, single, double and backtick strings with escapes, trailing commas, numbers, `true`/`false`/`null`. Only comments and a `"use strict"` directive may come before `window.SITREP =`: any other statement before it (an `import`, a call, a variable) fails `data-parses`, as a browser may reject it. It does not read expressions: string concatenation, variables, function calls, spreads, computed keys, `${}` in template strings. A valid-looking file that uses them fails `data-parses` with the reason in the note, although a browser would run it. The validator follows `schema.md` and is stricter in one place: every item needs a unique string `id` (`schema.md` calls only the titles essential, but `dependsOn`, stable regeneration and the feedback export are keyed by it).

## Unconfirmed

Nothing here has run against a live `claude` build; these are the facts the design leans on, none verified:

1. Whether a `Write` into a directory outside the cwd is allowed with a bare `Write` grant plus `--add-dir` in a headless `dontAsk` run. If it is refused and the refusal is recorded (in `permission_denials`, in a `permission_denied` event, or as `tool_result` text that reads as a refusal: the wording is a guess, matched on the words listed under the checks), `not-permission-blocked` fails: INCONCLUSIVE. A refusal that is recorded in none of these shows up as a FAIL of the data checks, blaming the skill for the sandbox.
2. Whether `Bash(*report.sh *)` and `Bash(*report.sh" *)` match a command that starts with a quoted absolute path (`test_plugin` checks this only with `*` modelled as "anything"). Fallback: an `--agent-cmd` with the absolute script path in the rule.
3. Whether headless Bash is sandboxed on this machine and which `TMPDIR` the Bash tool sees. If the child's `TMPDIR` does not reach it, `report.sh` creates the directory elsewhere and sweeps day-old `sitrep.*` directories there, on your own stale reports, as in normal use. The harness still finds the directory from the trace and snapshots it, but a write there may be denied.
4. The shape of a Bash `tool_result` (a string and a list of text blocks are both read) and that the Bash input field is `command`. The recorded traces in this repository hold only Skill calls.
5. The Skill input field (`skill`) and the spelling `plugin:skill` in init `skills[]` and `<name>@inline` in `plugins[]` are seen in recorded `oaat` traces (Claude Code 2.1.319), so they are expected to hold for `sitrep`; `plugin_errors` was absent from every init event in them.
6. Whether `${CLAUDE_SKILL_DIR}` is substituted in the skill text in a headless run, so the agent gets an absolute path to `report.sh`.
7. The element shape of `result.permission_denials` (empty in all 42 result events of the recorded `oaat` traces): matched on `tool_name` and on the raw JSON text.
8. Whether headless runs create `.claude/.cc-writes` in the workspace: ignored by the snapshot, and `?? .claude/` is dropped from the status.
9. Whether a resumed session's result event reports the cost of the whole session (the cost is read from the last result event on that assumption).
10. Whether `--resume` gives the model the report path from turn 1, which the cleanup turn depends on. If the session id cannot be read, the run is reported with a note and the cleanup checks fail.
11. Whether setting `TMPDIR` for the child changes where Claude Code itself keeps scratch files.
12. Whether `--setting-sources project,local` keeps user-level settings, hooks, plugins and memory out of the run, and still loads the plugin given by `--plugin-dir`. If an installed `sitrep` loads as well, `skill-loaded` fails (INCONCLUSIVE) because two plugins answer to the name; that the init `path` equals `--plugin-dir` is seen for `oaat` in recorded traces (Claude Code 2.1.319), not yet for `sitrep`.
13. Whether `--permission-mode dontAsk` denies a tool that is not granted, instead of stopping or asking (so that `open` is refused and a refusal shows up in `permission_denials`).

Verified in Claude Code 2.1.330 with `claude --help`: `--include-hook-events`, `--permission-mode` (with `dontAsk`), `--max-budget-usd`, `--add-dir` (variadic, so the runner never puts it last and a flag always follows its value), `--setting-sources`, `--plugin-dir`, `--allowedTools`, `--resume`.

## Provenance

`python/agent_trace.py` and `python/runner_cmd.py` are byte-identical copies of the files in [`evals/agent-md-audit/python/`](../agent-md-audit/python) (themselves copies of [`evals/oaat/python/`](../oaat/python)); `tests/test_plugin.py` fails if the first pair drifts. `python/hook_trace.py` (plugins, tool results, Bash calls, permission denials) is local to this harness. `run_evals.py`, `state.py`, `cases.py`, `fixtures.py`, `checks.py` and the test helpers follow the `agent-md-audit` harness; the rest is specific to this skill. If you fix a parser bug, fix every copy.
