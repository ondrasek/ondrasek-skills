# Evals of the `oaat` plugin

How do you know that `oaat` still works after you edit `plugins/oaat/skills/oaat/SKILL.md`? A reread of the text cannot tell you; an **eval** can: run the agent on fixed prompts and grade what it did. This directory is a small, complete eval harness for the live plugin in `plugins/oaat`. It runs offline on invented traces (free, no model call), and on your own agent when you ask.

It was developed as a teaching sample for the course PA015 (Masaryk University) and replicated here, pointed at the live plugin instead of a bundled copy. The record of the first real runs (sections "The first real run" to "Isolating the run") is from that sample, where the plugin was bundled as `skill/`.

**After the move of the trace reader and command runner to `evals/_common` (2026-11-22, Claude Code 2.1.330, outside the sandbox): one real run of all 11 prompts, 1 trial each: 11 of 11 PASS, 132 of 132 checks, $0.44 (the two-turn follow-up rows now use the shared last-result-event cost).**

`oaat` presents exactly one item in a fixed shape and waits for the answer:

```
**Item 1 of 5, decision: Pick the database**
**What it is:** one or two plain sentences
**Why it matters:** one sentence
**Options:**
1. first option, one line
2. second option, one line
3. third option, one line
**Recommendation:** Option N, because a few words
**One question:** one concrete question
```

Since `oaat` 0.3.0 that template is the **fallback**: when the `AskUserQuestion` tool is available the skill presents the item with that tool instead (see "The tool path (AskUserQuestion)"). Real eval runs are headless and only ever see the fallback.

Other rules: "skip" parks an item, "stop" gives a wrap-up (**Decided**, **Parked**, **Next action**), a tangent gets the flag "This may be a tangent from X. Continue or refocus?". The harness takes the skill's **name** (`--skill`, default `oaat`) and an optional **path** (`--skill-path`, `--skill-file`), so it grades any skill with a fixed reply shape.

Not to be confused with `claude plugin eval`, the Claude Code CLI's own eval runner (it reads `case.yaml` or `prompt.md` cases from an `evals/` directory *inside a plugin*). This harness is separate, lives at the repository root and ships with no plugin.

## The method, in five steps

The method follows two public posts (see Provenance): OpenAI's "Testing Agent Skills Systematically with Evals" and Google's "The Anatomy of Harness Engineering" (the behavioral eval).

| Step | What happens | Where |
|---|---|---|
| 1. Define success | Four groups: **outcome** (the reply does what the skill promises), **process** (the agent loads and calls the skill), **style** (the reply keeps the fixed shape), **efficiency** (short reply, no detours) | `python/checks.py` (`GROUPS`) |
| 2. Write prompts | A CSV with `should_trigger`: explicit, implicit, contextual, follow-up and negative-control prompts | `evals/prompts.csv` |
| 3. Capture a trace | Run the agent headless; save its JSONL event stream, one file per prompt | `python/run_evals.py` |
| 4. Check the trace | Deterministic checks on the **tool calls** and on the reply (a behavioral eval: the Skill tool call is the evidence, not a sentence in prose) | `python/checks.py` |
| 5. Grade with a rubric | A second model call fills a JSON schema (`overall_pass`, `score`, `checks[]`) for what code cannot judge | `python/judge.py`, `python/rubric.schema.json` |

Each prompt can run several times (`--trials N`), because the model is not deterministic. `python/report.py` compares two runs, for example before and after an edit of the skill.

## Run it

You need `make` and Python 3 (standard library only). Run everything from this directory (`evals/oaat`).

| What | Command |
|---|---|
| Offline demo on invented traces | `make run` |
| The tests (no model call) | `make test` (or `python3 -m unittest discover -s tests -v`) |
| Rewrite `captures/` from a run | `make grade-fixtures` |
| What the checks cost | `make measure` |
| **One real run on your agent** | `make eval ARGS="--only explicit-name --trials 1"` |
| Compare two real runs | `make compare BEFORE=artifacts-before/results.json AFTER=artifacts/results.json` |
| Every target | `make list` |

`make run`, `make test`, `make grade-fixtures` and `make measure` call **no model**. `make eval` writes traces and `results.json` to `artifacts/` (gitignored).

## What a real run costs

`make eval` starts `claude -p` once per prompt and trial. Each start is **one model call on your own account** (your login or `ANTHROPIC_API_KEY`). With `--judge`, each trial adds one more call. The table below counts calls; the price depends on your plan and model, so the price comes from your plan and your model.

| Command | Agent calls | Judge calls |
|---|---|---|
| `--only explicit-name --trials 1` | 1 | 0 |
| all 11 prompts, `--trials 1` | 11 | 0 |
| all 11 prompts, `--trials 3 --judge` | 33 | 33 |

Start with the first row. The runner prints the `total_cost_usd` that the result events report, so you see what a run cost after it ran. Cap a run with `--agent-cmd "claude -p {prompt} --output-format stream-json --verbose --allowedTools Skill --max-budget-usd 0.50"` (the flag is listed by `claude --help`).

The real run needs the skill to be loaded in the agent. Two ways:

1. The skill is installed on your machine (a plugin or `~/.claude/skills/`): run with `--user-setup`, and the agent uses your own setup, installed skill included.
2. The skill lives in a directory you name: `--skill-path /path/to/plugin-root` appends `--plugin-dir /path/to/plugin-root` to the agent command. The default for `oaat` is the live plugin `plugins/oaat` of this repository. The runner resolves a relative path before the agent starts, because the agent runs in another directory. One real run loaded the plugin this way (see "Isolating the run from your setup"). The judge reads the skill text from `--skill-file`; for the skill `oaat` the default is `plugins/oaat/skills/oaat/SKILL.md`.

The runner starts the agent in a fresh empty temporary directory (`--workdir` changes it), so the `CLAUDE.md` of the directory where you start the eval stays out of the run. **The default command keeps your user-level `CLAUDE.md` (in `~/.claude/`), your settings and your installed plugins out of the run** (`--setting-sources project,local`, see "Isolating the run from your setup"). `--user-setup` lets them load again; they then shape the reply, as the first real run showed. The runner sets `EVAL_PROMPT_ID` and `EVAL_TRIAL` in the agent's environment.

The first check of every trace, `skill-loaded`, reads the `skills` list of the `system/init` event. A trace that lacks the skill fails at once, with the reason, and a passing negative control then proves something: the skill was available and the agent left it alone.

## What to look for in the output

`make run` prints five sections; `captures/` holds one file per section. Four sections grade invented traces, so their numbers show how the harness reads a run and say nothing about the skill's real quality. The section `real` grades one recorded run.

- **`captures/grade-after.txt`**: the table of the run with the edited skill. All 11 prompts pass, every check of every group (`checks passed by group: outcome 48/48, process 55/55, style 37/37, efficiency 30/30`), `pass rate 100%`, exit code 0.
- **`captures/grade-before.txt`**: the same prompts with five planted defects, each caught by a named check:

  | Prompt | Defect | Failed checks |
  |---|---|---|
  | `implicit-walk` | the agent never calls the Skill tool and dumps the list | `skill-trigger` (and every shape check) |
  | `contextual-review` | four options, a second question, "Next up is …" | `options-max-3`, `one-question`, `no-preview` |
  | `followup-skip` | the reply shows item 1 again | `header-number` |
  | `followup-stop` | the wrap-up lacks **Next action** | `wrapup-headings` |
  | `negative-list` | the skill triggers on a request for the full list | `skill-trigger`, `all-items-listed`, `no-oaat-header` |

  The `failures:` block lists each failed check with the number that it measured, for example `options listed: 4; allowed 1 to 3`. The exit code is 1.
- **`captures/compare.txt`**: `report.py` on both runs. Five prompts are `better`, none `worse`, six `same`. Each `better` row names the checks that now pass (`fixed: wrapup-headings`). A `worse` row names `now failing: …` and the exit code is 1.
- **`captures/grade-real.txt`**: the one recorded real run (next section). It fails two checks, each for a stated cause, exit code 1.
- **`captures/judge-prompt.txt`**: the grading prompt that the judge receives for one trace: the criteria with their ids, the user's message, the reply and the JSON Schema.

## History: the real runs of the PA015 sample

The next three sections are the record of the real runs made in the PA015 sample (2026-11-04 and 2026-11-05), against the plugin text of that day, before the reply template below was applied to `plugins/oaat`. Paths such as `skill/` and `--skill-path skill` refer to the sample's bundled copy; here the same role is played by `plugins/oaat`. Their numbers are history, not the current state of the skill.

### The first real run (2026-11-04, Claude Code 2.1.318)

One model call: `make eval ARGS="--only explicit-name --trials 1 --agent-cmd 'claude -p {prompt} --output-format stream-json --verbose --allowedTools Skill --max-budget-usd 0.50'"`. The model was `claude-sonnet-5-5`, the run took 3 turns and `total_cost_usd` was 0.087. `tests/fixtures/traces/real/explicit-name.jsonl` holds the trace, cut by `tests/trim_real_trace.py` to the fields that the checks read (the full stream carries your paths, your session id and the skill text); the reply text is as the model wrote it.

**What the run confirmed**

- `--allowedTools Skill` lets the `Skill` call through in a headless run.
- `system/init` lists the skill as `oaat:oaat`, and the call carries `{"skill": "oaat:oaat"}`: the plugin name leads, as in the fixtures.
- On an explicit prompt the agent calls the skill. Both process checks pass.

**What the run graded.** Two of 19 checks fail, each for one cause:

| Check | Measured | Cause |
|---|---|---|
| `sections` | the reply labels its last section `**Question:**`; the skill's shape says `**One question:**` | the reply drifts from the skill text: a finding about the skill (or about the model that follows it) |
| `length-bound` | 168 words; the bound is 150 | the reply ends with a `**TLDR;**` block that the user-level `CLAUDE.md` of the machine asks for: a finding about the environment |

**What the run corrected in the harness.** The first grade of this trace showed five failed checks. Two were wrong, and both now have a test on the real trace. `options-max-3` counted 5 options where the reply lists 3, because the Options list ran on through the `Question:` line and the closing block: `option_lines` now ends the list at a blank line or at a line with the indent of the label. `one-question` failed a second time for the missing label: it now measures the count of question marks and leaves the label to `sections`, so one defect fails one check.

**What the run left open, and what later runs answered.** No run has used `--trials` or `--judge`. The two isolation flags of `claude --help` (2.1.318), `--setting-sources` and `--bare`, were tested in a later run: see "Isolating the run from your setup".


### The second and third real runs (2026-11-04)

**All 11 prompts once** (`--label real-all-11`, $0.79). 3 of 11 passed, the three negative controls. Every positive prompt failed `length-bound` (170 to 238 words, 12 to 17 lines against bounds of 150 and 12). 4 of 6 item replies wrote `Question:` for `One question:`. Three replies placed the recommendation on a line after the options. The user-level `CLAUDE.md` adds a TLDR and a "What's waiting for you" list to every reply. The three follow-up rows made 0 tool calls, because they replayed a conversation as text (see "The prompt table").

**The three follow-up rows as two turns** (`--label followup-two-turn`, $0.51). The skill loaded on all three (`process` 6/6). The dollar figures of runs that include two-turn follow-up rows were printed by a sum over the result events and overstate the cost by the first-turn cost of each such row (a resumed turn's result event reports the whole session; fixed in `evals/_common`, 2026-11-22). The traces of those three runs are not in this repository, so those figures are left as recorded. The traces of the three runs in "The reply template" below are on disk (git-ignored): their figures are regraded with the corrected sum, and the old value is named next to each. Findings of the replies:

| Row | Failed checks | Cause |
|---|---|---|
| `followup-skip` | `options-max-3`, `length-bound` | the reply lists the recommendation as a fourth bullet under Options, which the check counts; the closing TLDR makes it 13 lines |
| `followup-stop` | `length-bound` | 13 lines, 141 words: the wrap-up plus the environment's TLDR and an extra "Deadline" paragraph |
| `followup-tangent` | `no-oaat-header`, `one-question` | the reply writes "Back to item 1 of 5" (the header pattern matches prose) and asks two questions: the tangent flag and the item's question |

These traces live in `artifacts/` (gitignored). Nothing in `tests/fixtures/traces/real/` was added from them yet.


### Isolating the run from your setup

Two real runs of `explicit-name` on 2026-11-04 (Claude Code 2.1.318), both with the bundled plugin: `--skill-path skill`.

| Agent command adds | Result | Cost |
|---|---|---|
| `--setting-sources project,local` | The skill `oaat:oaat` loads from `--plugin-dir` and the agent calls it. The reply has no `TLDR;` block, so the user-level `CLAUDE.md` stayed out. Failed check: `sections` (the reply has no `One question:` label). `length-bound` passes. | $0.044 |
| `--bare` | The run ends at once with `Not logged in · Please run /login`: bare mode reads no keychain, so a subscription login does not reach it. It needs an API key in `ANTHROPIC_API_KEY`. | $0.000 |

A first attempt with `--skill-path ../skill` (a relative path) loaded no `oaat` skill: the agent runs in a temporary directory, and the path pointed at nothing. The runner now resolves the path.

Since 2026-11-04 this is the **default**: `python3 run_evals.py` runs `claude -p {prompt} --output-format stream-json --verbose --allowedTools Skill --setting-sources project,local` with `--plugin-dir skill` for the skill `oaat`. `--user-setup` runs with your own setup instead. A custom `--agent-cmd` is used as given. With `project,local` your own plugins and user settings stay out of the run, so only the bundled skill is under test.

**The clean baseline: all 11 prompts once with the default** (`--label baseline-isolated`, $0.40). 4 of 11 pass. `process` 22/22: the skill loaded and was called on all 8 positive rows, and on none of the 3 negative rows. `efficiency` 29/30. The replies run 9 to 10 lines and 109 to 163 words, and none ends with a TLDR block.

| Failed check | Rows | Cause |
|---|---|---|
| `sections` | 6 of the 7 item and skip rows | every reply labels its last section `Question:`; the skill's shape says `One question:` |
| `length-bound` | `contextual-review` | 163 words against 150 |
| `tangent-flag` | `followup-tangent` | the reply writes "This looks like a tangent from the database item"; the skill text gives the words "This may be a tangent from X. Continue or refocus?" |
| `no-oaat-header` | `followup-tangent` | the reply writes "Back to Item 1 of 5" |

The `Question:` label failed on the same rows in every earlier run, so a skill edit can fix it and `report.py` shows the change.

**The same traces under the stricter rules of 2026-11-05.** The author asked for one structure: options always numbered, and the recommendation on a line of its own after the options, never a bullet. `options-numbered` is a new check and `recommended-marked` follows the rule. Re-graded offline on the same eleven traces (no model call), the original skill fails `options-numbered` on 4 of the 7 item and skip rows (bullets or letters instead of numbers) and `recommended-marked` on 4 (the recommendation is a list item or sits inside an option). The template (below) is written to fix both. The rule that came first on the same day, "anywhere between the Options label and the question", was narrowed by this one.

## The reply template (applied 2026-11-07)

The original skill described the reply as a list of five parts, and real replies then differed from run to run: lettered or numbered options, the recommendation inside an option or in a bullet after the list, `Question:` for `One question:`. On 2026-11-05 the author set one structure: options always numbered, the recommendation on a line of its own after the options, never a bullet. The checks `options-numbered` and `recommended-marked` test it, and `plugins/oaat` (version 0.2.0) now carries the template in section 3 and the wrap-up. **First real run of the template (2026-11-07, all 11 prompts, 3 trials, no judge, $0.64; first recorded as $0.76 by the old sum):** 8 of 11 prompts passed every trial, 28 of 33 trials. `process` 66/66 and `style` 111/111: the template removed the `sections`, `options-numbered` and `recommended-marked` failures of the original. Failures: `followup-tangent` 0/3 (the replies paraphrased the flag as "This looks like a tangent…" and showed the item header again: the tangent bullet was still loose prose), and `length-bound` on two replies of 154 and 170 words (the bound was raised from 150 to 180 words). **The fix:** the tangent rule became a template (oaat 0.2.1: the exact flag sentence, nothing else). `followup-tangent` re-run, 3 trials ($0.11; first recorded as $0.20 by the old sum): 3/3 pass. **Full re-run on 0.2.1 (2026-11-07, 11 prompts, 3 trials, no judge, $0.65; first recorded as $0.81 by the old sum):** 11 of 11 prompts passed every trial, 33 of 33 trials; every group at 100% (outcome 129/129, process 66/66, style 111/111, efficiency 90/90). Caveat: the word bound (180) was raised after the first run showed two replies above 150, so part of this pass rate is that change; the replies are no longer than before. Three trials per prompt is a small sample: a prompt that passes 3/3 can still be flaky.

## The tool path (AskUserQuestion)

**What the skill does.** Since version 0.3.0 (`plugins/oaat/skills/oaat/SKILL.md`, sections 3 and 4), when the `AskUserQuestion` tool is in the agent's tool list the skill presents each item with ONE call with ONE question and makes the next call only after the answer to this one has come back (never batched). `header` is the item type in at most 12 characters; `question` is `Item N of M, type: Title.`, then what it is and why it matters in two short sentences, then one concrete question (the only question mark); `options` are the item's one to three choices, each with a short `label` and a one-line trade-off `description`, the recommended one first with a label ending in `(Recommended)`, plus a last option labelled `Skip for now` (description `Park this item and move on.`), so two to four options; `multiSelect` is `false`. Outside the calls the agent prints only the one-line count before the first item and ONE short plain line (a confirmation, an explanation or a correction) before the next call; it never lists the other items, not even in those lines. The answer comes back as the result of the call, in the same turn: a chosen option is its label, `Skip for now` is a skip, and text typed into the free-text field ("Other") is read like any reply ("stop", a question, a tangent). After a chosen label or a skip with items left, the agent goes on in that turn with the next call: it does not close or wrap up. If the tool is not in the list (headless `claude -p`, subagents), or the call is denied or fails, the agent uses the text template silently and must not search for the tool (no `ToolSearch` for it). Four rules of the skill were added in the last round, and each has a check: **a text-template item ends the turn** (no call after it in the same turn); **a call that fails on its input is retried once with the input fixed** (the retry of the same item needs no text after the failed call; a denied or unavailable call with no retry needs the template text); **the tangent flag ends the turn** (it is the closing text of the turn, never text before a call); **an answer to an item that came from a milestone is recorded through the `milestones` skill in the same turn**, and those calls are not detours.

**One turn, several calls (the same-turn model).** The documentation of the tool ([Agent SDK, "Handle approvals and user input"](https://code.claude.com/docs/en/agent-sdk/user-input)) says that the call pauses execution until the answer is returned, "different from normal conversation turns where Claude finishes and waits for your next message". So an interactive walk-through is ONE turn that holds several sequential calls: call, `tool_result`, one plain line and the next call, and so on. A skip is not a new prompt: it is the `tool_result` `Skip for now`. The first version of this harness modelled one call per turn and a skip as a second prompt; that cannot happen, and its committed fixture `followup-skip` (turn 1 ending right after a `tool_use` with no `tool_result`) was an impossible trace. The harness now grades **per call**: every `AskUserQuestion` call of the graded turn is checked on its own, and the note of a check names the call that failed (`call 2: header 'Database decision' has 17 characters -> more than 12`).

**Real headless runs cannot call it (measured).** A real probe on Claude Code 2.1.330: in a headless `claude -p` run the tool is not available at all; the model's `ToolSearch` for it returned `No matching deferred tools found`. So every real eval run exercises the text fallback only, and the tool-path checks are **offline-only**: they run on invented traces.

**What is documented, what was observed once, and what is UNCONFIRMED.** The INPUT shape IS documented at the URL above: `questions[]`, each with `question`, `header` (max 12 characters), `options` (2 to 4, each with `label` and `description`) and `multiSelect`. The text of the result was **observed once**, in an interactive Claude Code session on 2026-11-22: the `tool_result` text is a wrapper sentence, not the bare label,

```text
Your questions have been answered: "<question text>"="<label>", "<question 2>"="<label 2>". You can now continue with these answers in mind.
```

An answer **typed** into the free-text field comes back in a second wrapper, observed on 2026-11-28 (Claude Code 2.1.332) and parsed too:

```
The user answered: "<question text>"="<typed text>". Read the answers carefully — they may request clarification, ...
```

**Interactive walk, 2026-11-28** (driven in a terminal by the author's agent, plugin loaded from this repository, manual permission mode): three invented items, the recommended option, `Skip for now`, then `stop` typed into the free-text field. Observed: one question per call; header `Decision`; the recommended option first with the suffix; `Skip for now` last; one plain line between calls; the wrap-up after `stop`. The CLI adds `Type something.` and `Chat about this` to the dialog itself; they are not part of the tool input. The recorded session graded 19 of the 20 applicable checks as passing; the twentieth, `turns-bound`, needs `num_turns`, which a session transcript does not carry. The stream-json form of these events is still **UNCONFIRMED** (an interactive session writes a transcript, not stream-json).

one `"<question>"="<answer>"` pair per question, joined by `, `; the label is verbatim, a `(Recommended)` suffix included; a typed free-text answer comes in the second wrapper shown above. **Few samples on two versions: keep both UNCONFIRMED across versions.** `python/ask_tool.py` (`answer_of`) parses that envelope to extract the answer (an answer or question that holds quotes or commas is read whole, because the known question texts of the call are looked up first), and still accepts a bare label (the earlier assumption) and a JSON mapping of question to answer, plain or under `answers` (the shape of the SDK's answers). A chosen label is recognised as a label and anything else as free text; labels are matched ignoring case and surrounding whitespace, and the `(Recommended)` suffix is part of the label (the bare name is free text). The stream-json nesting is assumed, not observed (a session transcript showed the same content blocks; no real interactive trace is stored in this repository):

```json
{"type": "assistant", "message": {"content": [
  {"type": "text", "text": "5 items need you. First one:"},
  {"type": "tool_use", "id": "toolu_1", "name": "AskUserQuestion", "input": {"questions": [
    {"question": "Item 1 of 5, decision: Pick the database. ... Which database should the first release use?",
     "header": "Decision",
     "multiSelect": false,
     "options": [{"label": "Postgres (Recommended)", "description": "More setup; handles many users."},
                 {"label": "SQLite", "description": "No server; one writer at a time."},
                 {"label": "Skip for now", "description": "Park this item and move on."}]}]}}]}}
{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content":
  "Your questions have been answered: \"Item 1 of 5, decision: Pick the database. ... Which database should the first release use?\"=\"Postgres (Recommended)\". You can now continue with these answers in mind."}]}}
```

If another version words the envelope differently, `python/ask_tool.py` is the one place to change: an unrecognised wrapper falls back to reading the whole content as free text, which is the lenient reading for `header-number` (the same item may come again) and loses only the label strictness (and `tool-early-end` stops applying to that call). The tests then show what else moves. The committed tool fixtures and every invented trace of the tests carry their `tool_result` text in the observed wrapper (`tests/helpers.py` `wrap_answer`, `tests/build_fixtures.py` `observed_envelope`).

**How a turn is graded (the branching).** `Case.tool_path` is true when the row's `expect` is `item` or `skip` and the last turn of the trace (the events after the previous `result` event) holds at least one `AskUserQuestion` call of the main conversation that got a `tool_result` that is not an error. Then `run_checks` runs the common checks, the four outcome checks per call on the question and its options, and the eight tool checks (seven per call; `tool-early-end` once per turn, on the last answered call). Otherwise it runs what it ran before: the common checks plus the checks of the row's `expect`.

- **A denied or failed call is graded on the text path.** A call whose `tool_result` has `is_error: true`, or that has no `tool_result` at all, is only recorded (`Case.asks`, the note of `tool-denied-fallback`). The skill sanctions the text template as the fallback, so a turn with only such calls is graded on the template exactly as a headless run is. A denied call with nothing printed after it (before the next call or the end of the turn) fails `tool-denied-fallback`: the user saw nothing of the item. In a mixed turn (an answered call and a denied one) the text after the denied call is the fallback and is graded with the text-path checks for that item. **Retry after an input error:** a call that failed with an error result and is immediately called again, once, for the same item (the same `Item N`) needs no text after it; the retry is judged on its own, the text between the failed call and its retry is graded as the one plain line between two calls, and a second failure of the same item is not retried a third time, so it needs the template text. (An error result cannot be told apart from a denial, so a denied call that is called again for the same item is allowed too.)
- **A fallback template ends the turn (`tool-template-ends-turn`).** A text template item (an `Item N of M` header outside the tangent flag together with at least one template label) in a turn, on either path, must not be followed by any `AskUserQuestion` call in the same turn. A lone header line is not a template (it is an extra line, graded by `tool-no-extra-text`); a mangled item with a header and no label at all is not seen (known gap).
- **No early end (`tool-early-end`, tool path).** When the last answered call was answered with a chosen option label or `Skip for now` (not free text), its `Item N of M` has N < M, and nothing asked to stop, the turn must continue with another `AskUserQuestion` call (answered or denied) or, as the fallback, a text template for a later item. A closing line or a wrap-up there fails: the user is left with nothing to answer. Free text ("stop", a question, a tangent) is handled by the other rules, and after the last item (N equals M) the wrap-up is right. A turn that prints a template for a later item without trying the call is accepted (the skill's template is the sanctioned fallback; the harness cannot tell whether the tool was available).
- **The tangent flag ends the turn.** `tool-no-extra-text` allows the flag only as the text after the last call; before the first call or between two calls it fails (the flag is no longer erased before the question-mark test).
- **The selection follows the call, not the reply text:** a turn that makes an answered call and also prints the template fails `tool-no-extra-text`.
- **Rows that allow no item presentation.** On rows whose `expect` is `stop`, `tangent`, `list` or `none` ANY `AskUserQuestion` call is a violation (`tool-not-allowed`): the tangent rule says no item header and no repeat of the item, and `stop` gives the wrap-up. `no-oaat-header` also reads the question texts of such a turn.
- **Numbering.** `header-number` runs along the calls: the first call is the previous item (read from the earlier turns' reply or question texts, 0 for a first turn) plus 1; each later call is the previous call's item plus 1, or the same item again when the previous answer was free text ("Other": a question or a disagreement) or the previous call failed. After a chosen label or `Skip for now` the same item again fails.
- **Detours and turns.** `tool-detours` does not count `AskUserQuestion` calls, nor the **milestone bookkeeping** calls: a `Skill` call whose name ends in `milestones`, a `Bash` call that runs a script named `milestones.py` (a word of the command, by its base name), a `Read` of `.cache/milestones.json` (the skill requires the recording in the same turn; `ask_tool.is_bookkeeping`). `turns-bound` subtracts from `num_turns` the number of `AskUserQuestion` calls and of bookkeeping calls of the graded turn, because each call adds a model turn (call, `tool_result`, next message). Every other tool call and other turn still counts (three Reads of another file are still three detours). The exemption has no cap of its own.

`tests/test_tool_path.py` pins this: a good tool-path trace passes all 20 applicable checks (8 common, 4 outcome checks per call, 8 tool checks), and a good fallback trace passes the 19 checks of the text path (the 16 that ran before the tool path, `no-tool-search`, `tool-denied-fallback` and `tool-template-ends-turn`).

**`no-tool-search` guards the fallback.** It applies to every row on both paths. The skill says "do not search for it", and the measured fact is that in a headless run the search finds nothing, so a search is a wasted call on every real run. A real trace that searches for the tool fails this check even when its reply is perfect. The 44 recorded real traces in `artifacts/` (checked 2026-11-22; they are the pre-0.3.0 runs) and the committed `tests/fixtures/traces/real/` trace hold no such search and pass it (a test pins this for the committed ones). Each trial has three more checks than before the tool path (`no-tool-search`, `tool-denied-fallback`, `tool-template-ends-turn`), and the rows without an item have `tool-not-allowed`: the 11 prompts count 170 checks per full run (`captures/grade-after.txt`: outcome 48, process 55, style 37, efficiency 30) where the recorded run before 0.3.0 counted 132 (6 item and skip rows with 19, `stop` 12, `tangent` 13, `list` 11, the two `none` rows 10). `tool-early-end` and the seven tool checks run only on the tool path, so a headless run never counts them.

**Failing runs.** `tests/test_tool_path.py` holds two registries like those of the other harnesses. `MUTATIONS` has one invented failing run for each of the twelve new checks (`no-tool-search`, `tool-denied-fallback`, `tool-template-ends-turn`, `tool-not-allowed` and the eight tool checks, `tool-early-end` among them), each failing only its own check (the one for `tool-no-extra-text`, a whole template next to a call, also fails `tool-template-ends-turn`, and says so); `test_every_new_check_has_a_failing_run` compares it with `checks.GROUPS` against the fixed list of ids that existed before, so a new check without a failing run fails the suite. `CHANGED` has one failing run for each existing check whose behaviour changed (`header-number`, `header-total`, `no-other-items`, `no-preview` per call, `no-oaat-header` on question texts), and `test_every_changed_check_has_a_failing_run` forces it. `REFINED` holds one more failing run AND one passing run for each behaviour that the last round changed in a check that already had a failing run (a retry made only once, the flag before a call, a count line of 31 words against 30, milestone calls not counted as detours or turns, a label matched ignoring case); `test_every_refined_behaviour_has_a_failing_and_a_passing_run` forces both and compares the ids with a fixed list. The test classes after them hold more failing runs per check: a defect in the second call only, batching (two calls in one message, two calls without a `tool_result` between them), a header of 13 code points, a title in an option description, the count line printing the list, two lines between calls, a denied call with no text, a call on a `stop` row, a milestone walk of three items (Skill, Bash and Read calls of the milestones plugin), every envelope, and so on. The invented traces are built in code (`tests/helpers.py`: `make_walk_events`, `make_tool_events`, `GOOD_TOOL_INPUT`) and two small JSONL files are committed under `tests/fixtures/traces/tool/`, built by `tests/build_fixtures.py`: both are one-turn walks in which every call is answered before the next (`explicit-dollar`: a label, `Skip for now`, free text `stop`, the wrap-up; `followup-skip`: `Skip for now`, a free-text question, the same item again, a label, `stop`, the wrap-up). Grade them offline:

```
python3 python/run_evals.py --fixtures tests/fixtures/traces/tool --only explicit-dollar,followup-skip
```

The shared reader `evals/_common/python/agent_trace.py` was not changed. The helpers that find the last turn, the timeline of a turn, the calls with their results, the text between the calls and the searches (`split`, `timeline`, `ask_records`, `segments`, `unanswered_overlaps`, `searched_for_tool`) live in `python/ask_tool.py` of this harness, because no other harness needs them; if one does, they belong in `_common`.

**Manual interactive smoke test (by hand, about five minutes).** The only way to confirm the envelope and the behaviour, in one same-turn walk-through:

1. Make a throwaway directory and open an interactive Claude Code session in it with the plugin loaded: `claude --plugin-dir /path/to/ondrasek-skills/plugins/oaat`.
2. Ask: `Use the oaat skill and walk me through three pending items: pick a database (Postgres or SQLite), confirm a launch date, assign a security review. These are invented.`
3. Answer the first question by picking an option. Answer the second with `Skip for now`. Answer the third by typing `stop` into the free-text field ("Other").
4. Confirm, for every question: one question per call (a question dialog, never several at once); at most 4 options; the recommended option first with the label suffix `(Recommended)`; `Skip for now` last; the header at most 12 characters; the question starts with `Item N of M, type: Title.` and has one question mark. Confirm for the text around the calls: at most one plain line between two calls (and the one-line count before the first), nothing else printed (no list, no preview of the next item), and after `stop` the wrap-up with Decided, Parked (the skipped item) and Next action.
5. To record a trace for this harness, run the same prompt in a session that has the tool, if your version has one, with `--output-format stream-json --verbose`, and check the `tool_use` block and the `tool_result` block against the shape above. The wrapper sentence of the `tool_result` text was observed once (2026-11-22); recording it on another version, and the stream-json nesting around it, is what can turn the envelope from "UNCONFIRMED" into "confirmed".

## The prompt table (`evals/prompts.csv`)

Columns: `id`, `should_trigger`, `kind`, `expect`, `prompt`, `reply`. The OpenAI post uses three (`id`, `should_trigger`, `prompt`). The sample adds `kind` (what the prompt tests), `expect` (which checks apply to the reply) and `reply` (optional: the user's second message of a two-turn row).

| id | should_trigger | kind | expect | What the prompt is |
|---|---|---|---|---|
| `explicit-dollar` | true | explicit | item | "Use $oaat." and 5 pending items |
| `explicit-name` | true | explicit | item | "Please use the oaat skill" and 4 items |
| `implicit-walk` | true | implicit | item | "Walk me through what's pending, one at a time." (no skill name) |
| `implicit-overwhelmed` | true | implicit | item | "Can we go through them one by one?" |
| `contextual-review` | true | contextual | item | a design review ended, items wait, each blocks something (no "one at a time") |
| `followup-skip` | true | followup | skip | turn 1 starts the list; the reply is "skip, later." |
| `followup-stop` | true | followup | stop | turn 1 starts the list; the reply is "stop, enough for today." |
| `followup-tangent` | true | followup | tangent | turn 1 starts the list; the reply is a question about container runtimes |
| `negative-single` | false | negative | none | one question with one answer, no items |
| `negative-list` | false | negative | list | "Show me the full list … all at once." with 5 items |
| `negative-one-decision` | false | negative | none | one decision, two sentences wanted |

**A follow-up row runs two turns in one session.** The runner answers `prompt` first ("Use the oaat skill on the list below" and 5 items), so the agent loads the skill and shows item 1. It then resumes that session with `claude -p <reply> --resume <session id>`, as the user's second message. The trace is both streams one after the other, and the checks grade the second reply. The first reply is the history: `header-number` reads the item number from the agent's own first reply. The first design replayed the conversation as text in one prompt (`We are going through my items with the oaat skill. Your last message was: …`). In a real run the agent made 0 tool calls on 2 of 3 such rows, so those replies came from the model and the user-level `CLAUDE.md`, and the skill had no effect on them. A cost note: a two-turn row costs about twice a one-turn row, $0.17 in the first real run.

**The fixture block** has one fixed shape, so the checks read the item titles from the prompt itself:

```
Pending items:
- Pick the database: Postgres or SQLite for the first release.
- Confirm the launch date: Marketing needs a date by Friday.
```

**Explicit prompts name the skill in words.** A prompt that starts with `/oaat` is a different test: the headless documentation says that Claude Code expands `/skill-name` before the run, so the skill loads without a model decision. Expect no Skill tool call for such a prompt (an inference from that sentence; confirm with one trace). Use plain words to test the model's choice to call the skill.

## The checks (`python/checks.py`)

Each check is a pure function of a `Case` (the prompt row, the events of the trace, the reply text) and returns `(id, passed, note)`. The note holds the measured value.

| Id | Group | Applies to | Passes when |
|---|---|---|---|
| `skill-loaded` | process | all | `system/init` lists the skill |
| `skill-trigger` | process | all | a `Skill` tool call names the skill when `should_trigger` is true; none when false |
| `completed` | outcome | all | the result event is `success`, `is_error` false, reply not empty (on the tool path the answered call stands for the reply) |
| `tool-detours` | efficiency | all | at most 2 tool calls besides `Skill` and `AskUserQuestion` (the item presentations of the tool path are not detours; neither are the milestone bookkeeping calls: a `Bash` call running `milestones.py`, a `Read` of `.cache/milestones.json`) |
| `turns-bound` | efficiency | all | `num_turns` minus the `AskUserQuestion` calls and the milestone bookkeeping calls (a `Skill` call named `...milestones`, `Bash`, `Read` as above) of the graded turn is at most 4 |
| `one-header` | style | item, skip | exactly one `Item N of M` in the reply |
| `header-number` | outcome | item, skip | N is 1 on the first turn, and the quoted previous N plus 1 on a follow-up |
| `header-total` | outcome | item, skip | M equals the number of pending items in the prompt |
| `sections` | style | item, skip | What it is, Why it matters, Options, One question: all present, in order |
| `options-max-3` | style | item, skip | 1 to 3 option lines |
| `options-numbered` | style | item, skip | the option lines are numbered 1, 2, 3 in order (a letter or a bullet fails) |
| `recommended-marked` | style | item, skip | exactly one line between the Options label and the question contains "recommend"; it is a line of its own after the last option, never a list item and never inside the options |
| `one-question` | style | item, skip, tangent | exactly one `?` in the reply, and (item, skip) it sits in the One question section |
| `no-other-items` | outcome | item, skip | at most one pending title appears in the reply (on a skip, the title just parked does not count) |
| `no-preview` | outcome | item, skip | no `item N` besides the header (on a skip, the parked item's number does not count), no phrase such as "next up" or "coming up" |
| `length-bound` | efficiency | item, skip, stop, tangent | at most 12 non-empty lines and 180 words |
| `wrapup-headings` | outcome | stop | Decided, Parked, Next action, in that order |
| `tangent-flag` | outcome | tangent | "This may be a tangent from X. Continue or refocus?" |
| `all-items-listed` | outcome | list | every pending title appears in the reply |
| `no-oaat-header` | outcome | stop, tangent, list, none | no `Item N of M` in the reply and in the question texts of any `AskUserQuestion` call of the turn; the one exemption is the tangent flag itself, which may name the current item as X (`This may be a tangent from Item 2 of 4, the hosting choice. Continue or refocus?`): the exempt span is exactly the sentence that `tangent-flag` accepts, so a header between the start of the flag and a later `Continue or refocus?` still fails |
| `no-tool-search` | process | all (both paths) | no `ToolSearch` call whose input mentions `AskUserQuestion` (any spelling: `AskUserQuestion`, `ask user question`, ...) |
| `tool-denied-fallback` | process | all (both paths) | every `AskUserQuestion` call that got no successful `tool_result` (an error result, or none) has text printed after it, before the next call or the end of the turn; except a call that failed with an error and is immediately retried, once, for the same item |
| `tool-template-ends-turn` | process | all (both paths) | no `AskUserQuestion` call follows a text template item (an `Item N of M` header outside the tangent flag plus a template label) in the same turn |
| `tool-not-allowed` | outcome | stop, tangent, list, none | no `AskUserQuestion` call in the turn (denied or not) |
| `tool-early-end` | outcome | item, skip (tool path) | when the last answered call was answered with a chosen label or `Skip for now`, its `Item N of M` has N < M and nothing asked to stop, the turn ends with a further `AskUserQuestion` call (or a text template for a later item), not with a closing line or a wrap-up |

**The tool-path checks** apply instead of the text-template style checks when the graded turn of an `item` or `skip` row has an answered `AskUserQuestion` call (next section). They grade **every call of the turn on its own**: the check passes when all calls pass, and its note names each call. The `Case` properties `turn`, `asks` and `tool_path` and the helpers in `python/ask_tool.py` find the calls.

| Id | Group | Passes when (for every call) |
|---|---|---|
| `tool-one-call-one-question` | process | the call holds exactly one question, and the calls are sequential: a call gets its `tool_result` before the next call is made (two calls in one assistant message, or two calls without a `tool_result` between them, are batching) |
| `tool-header-short` | style | `header` is a non-empty string of at most 12 characters, counted as code points (`MAX_TOOL_HEADER`) |
| `tool-options-shape` | style | 2 to 4 options, each with a non-empty `label` and `description`; `multiSelect` is `false` or absent |
| `tool-recommended-first` | style | the first option's label ends with `(Recommended)` and no other label does (surrounding whitespace is ignored) |
| `tool-skip-last` | style | the last option's label is `Skip for now` (surrounding whitespace is ignored) |
| `tool-question-shape` | style | the question starts with `Item N of M, type: Title.` (type and title present) and has exactly one `?` |
| `tool-no-extra-text` | style | the text outside the calls is only: before the first call one line of at most 30 words (`MAX_COUNT_LINE_WORDS`, raised from 20: a natural count line runs to about 21 words), between two calls ONE short plain line of at most 30 words (`MAX_PLAIN_LINE_WORDS`), after the last call nothing, one plain line or the wrap-up (Decided, Parked, Next action, in order, within the length bounds). A plain line has no list mark, no `Item N of M` header, no template label, no look-ahead phrase, no question mark, no other item's number, and no pending title except the item of the call before or after it; the count line may name only the item of the first call, never the list. The tangent flag is the one sentence that may ask, and only as the closing text after the last call: before a call it fails. The text after a denied call that is not retried is the fallback and is left to `tool-denied-fallback`; the text after a failed call that is retried is a plain line between two calls |

On the tool path these outcome checks still run, per call, on the **question and its options** (the item is there, not in the reply): `header-number` (the chain described in the next section), `header-total`, `no-other-items` (at most one pending title in the question, the option labels and the option descriptions) and `no-preview` (no other item number and no look-ahead phrase there). `completed` also accepts a turn with no text at all when it holds an answered call. The text-template style checks (`one-header`, `sections`, `options-max-3`, `options-numbered`, `recommended-marked`, `one-question`, `length-bound`) do not run on that path.

The bounds (`MAX_REPLY_LINES`, `MAX_REPLY_WORDS`, `MAX_OPTIONS`, `MAX_OTHER_TOOL_CALLS`, `MAX_TURNS`) are constants at the top of `checks.py`. The skill asks for "about 8 lines", and the bound of 12 lines leaves room for the intro line and a confirmation. Change a bound when your skill needs another one, and re-run the tests.

**What the deterministic checks can see:** structure, counts and verbatim text. `no-other-items` finds a pending title that the reply quotes word for word, and `no-preview` finds a fixed list of look-ahead phrases. A reply that paraphrases the next item passes both. The rubric pass exists for this gap.

## The rubric pass (`python/judge.py`, `python/rubric.schema.json`)

The judge call is a parameter: `--judge-cmd` (default `claude -p {prompt}`). The runner builds the grading prompt (criteria, the user's message, the reply, the schema), gives it to the command and reads one JSON object from its output. The object must match `rubric.schema.json`:

```json
{"overall_pass": true, "score": 90, "checks": [{"id": "one-item", "pass": true, "notes": "…"}]}
```

- `judge.validate` is a small JSON Schema validator for exactly the keywords that the schema uses (`type`, `properties`, `required`, `additionalProperties`, `items`, `minItems`, `minimum`, `maximum`). It refuses any other keyword, so a schema edit cannot silently weaken the check.
- The `checks` ids must equal the criteria ids for the prompt (`criteria_for(expect)` in `judge.py`). A reply with other ids is invalid.
- A trial passes when every deterministic check passes **and** the judge says `overall_pass`. An invalid judge reply fails the trial and the failure line says why.
- The judge reads the criteria in `judge.py`, which restate the skill's rules in this sample's own words. With `--skill-file` the judge also reads the real skill text.
- `python3 python/judge.py followup-skip tests/fixtures/traces/after/followup-skip.jsonl` prints the grading prompt without a model call.
- The tests use `tests/stub_judge.py` (modes: pass, fail, fenced, garbage, badschema, wrongids, crash).

The judge is a model, so its verdict is a second noisy measurement. Keep the deterministic checks as the gate and read the judge's notes as a second opinion.

## Trials and noise

`--trials 3` runs each prompt three times and the table prints `2/3` in the `trials` column. The prompt line passes only when every trial passes (the summary says so: `prompts passed 9/11 (every trial)`), and the summary also prints the share of trials that passed. A prompt with `2/3` is a flaky behaviour of the skill. A single run can pass by luck. Run 3 to 5 trials before you decide that an edit helped.

`report.py` compares the **share** of passed trials per prompt, so `1/2` and `2/4` are `same`.

## Fields of the stream that the harness reads

The harness reads these fields of `claude -p --output-format stream-json --verbose` and no others (`../_common/python/agent_trace.py`, shared by every harness under `evals/`). The sources are the Claude Code documentation pages read on 2026-11-04.

| Field | Used for | Source |
|---|---|---|
| each stdout line is one JSON object with a `type` | the trace is JSONL | [headless](https://code.claude.com/docs/en/headless#stream-responses): "Each line is a JSON object representing an event"; `--verbose` is required with `stream-json` in the documented commands |
| `type: "system"`, `subtype: "init"`, `skills` (array of strings), `tools`, `model`, `session_id` | `skill-loaded` | [Agent SDK TypeScript reference, `SDKSystemMessage`](https://code.claude.com/docs/en/agent-sdk/typescript); [headless, "Read session metadata"](https://code.claude.com/docs/en/headless#read-session-metadata) |
| `type: "assistant"`, `message.content[]` blocks `{"type": "text", "text"}` and `{"type": "tool_use", "id", "name", "input"}` | the reply text and every tool call | [`SDKAssistantMessage`](https://code.claude.com/docs/en/agent-sdk/typescript) (`message: BetaMessage`); [agent loop, "Handle messages"](https://code.claude.com/docs/en/agent-sdk/agent-loop#handle-messages) (`block.type === "tool_use"`, `block.name`) |
| `parent_tool_use_id` (`null` for the main conversation) | ignore subagent messages | [headless, "Follow subagent messages"](https://code.claude.com/docs/en/headless#follow-subagent-messages) |
| the tool named `Skill` | the process check | [tools reference](https://code.claude.com/docs/en/tools-reference) (the `Skill` row; "a skill … runs through the existing `Skill` tool") |
| `input.skill` of a `Skill` call (`"oaat"` or `"plugin:skill"`) | which skill was called | **Confirmed in one real run** on Claude Code 2.1.318 (2026-11-04): `{"skill": "oaat:oaat"}`. The documentation pages above name the tool and leave its input fields unlisted. Plugin skills are named `plugin:skill` ([skills page](https://code.claude.com/docs/en/skills): "namespaced as `/plugin-name:skill-name`"). Open `artifacts/<id>.jsonl` after your first run to confirm the field on your version |
| `type: "result"`, `subtype` (`"success"` or `error_*`), `is_error`, `result` (text, `success` only), `num_turns`, `total_cost_usd` | `completed`, `turns-bound`, the cost line, the reply text | [`SDKResultMessage`](https://code.claude.com/docs/en/agent-sdk/typescript); [agent loop, "Handle the result"](https://code.claude.com/docs/en/agent-sdk/agent-loop#handle-the-result) |

Two flags of the default agent command come from the same documentation:

- `--allowedTools Skill`: the tools reference marks `Skill` as "Permission required: Yes", and the headless page says that in a Manual-mode `claude -p` run a call that needs approval is denied. The flag allows the skill call (confirmed in the real run). The flag takes a list, so the default command puts `{prompt}` before it.
- The default command runs without `--bare`: the headless page says that bare mode gives Claude no list of the available skills, so the model could not choose a skill on its own. The implicit and contextual prompts need that choice.

## Honest limits

- **One trace is one sample.** Use `--trials` before you compare two skill texts.
- **The fixtures `after/` and `before/` are invented.** They prove that each check reads the stream shape and fails where it should; they say nothing about how `oaat` behaves. `real/` holds one recording of one prompt: it says what that run did and nothing about the other ten prompts.
- **A check of verbatim text misses a paraphrase.** See "What the deterministic checks can see".
- **`header-total` assumes M is the number of pending items.** A skill version that counts only open items would fail this check on a follow-up; adjust the check to the skill's rule.
- **The tool path is graded offline only, on a partly assumed event shape.** The tool is not available in headless runs, and no real trace is stored in this repository. One interactive walk (2026-11-28) was observed and graded by hand from its session transcript: it showed the input shape, the same-turn behaviour and both answer wrappers. That is one walk on one version with one kind of item (three decisions); a denied or failed call, a retry, a tangent and `Chat about this` were not exercised. The stream-json nesting is assumed, and the tool fixtures are invented.
- **Known limits of the tool checks.** (1) The tangent flag names the current item as X without a period: a flag whose X holds one (`... from Item 1 of 5 (the database, e.g. Postgres). Continue ...`) is not recognised (`tangent-flag` fails on it; a test pins this, N1c). (2) An error result cannot be told apart from a denial, so a denied call called again for the same item is accepted like a retry after an input error. (3) A template item is recognised by its `Item N of M` header plus a template label; a mangled item with no label is not seen by `tool-template-ends-turn`. (4) `tool-early-end` accepts a template for a later item with no call before it. (5) The milestone exemption has no cap: a run that makes many `milestones.py` or `.cache/milestones.json` calls is not penalised for them. (6) The command is matched by the base name of a word (`milestones.py`), so `echo milestones.py` would count.
- **The `input.skill` field is verified once**, on one version (see the table above). The documentation does not list it.
- **The bounds are guesses.** `MAX_REPLY_WORDS` started at 150 and was raised to 180 on 2026-11-07, after a 33-run template run put two correct replies at 154 and 170 words. `MAX_REPLY_WORDS` (first value 150) and `MAX_REPLY_LINES` (12) came from the skill's "about 8 lines" and from that reply (143 words and 9 lines without the closing block). Run 3 to 5 trials of every prompt, then set the bounds from the replies that you accept.
- **The judge is a model.** Its verdict can differ between runs.
- **A two-turn row depends on `--resume`.** One real run (2026-11-04) resumed the session and the skill text stayed in context. The runner needs the session id from turn 1; without it the row fails with a note.

## Exercise: turn a manual fix into an eval

Step 1 (a miss): run the skill yourself in a normal session on a list of your own items. Find one behaviour that you did not like: the reply named the next item, the wrap-up forgot the parked items, the recommendation had no reason.

Step 2 (name the behaviour): write one sentence that says what a correct reply does, for example "the wrap-up lists every skipped item under Parked".

Step 3 (a prompt): add a row to `evals/prompts.csv` with the pending items and a follow-up that triggers the behaviour. Pick `kind` and `expect` from the table above, or add a new `expect` value.

Step 4 (a check): add a function to `python/checks.py` that measures the sentence (a count, a position or a phrase). Return `(id, passed, note)`, put the id in `GROUPS` and in `BY_EXPECT`.

Step 5 (a test, red): write two tests in `tests/test_checks.py`: a reply that passes and a reply that fails. Run `make test` and watch the failing test fail for the right reason.

Step 6 (a trace): build an invented `before` trace with the defect and an `after` trace without it in `tests/build_fixtures.py`, run `python3 tests/build_fixtures.py` and `make grade-fixtures`.

Step 7 (the real run): run `make eval ARGS="--only <your-id> --trials 3"` before and after you fix the skill text, and compare with `make compare`.

The post of OpenAI says the same: "add rows whenever a manual test reveals a miss". The eval set grows from your own corrections.

## Files

| Path | What it is |
|---|---|
| `evals/prompts.csv` | the 11 test prompts: `id`, `should_trigger`, `kind`, `expect`, `prompt`, `reply` (three rows have a second turn) |
| `python/run_evals.py` | the runner: prompt rows to agent runs to saved traces to checks (and the judge), the table, the summary, the exit code |
| `python/checks.py` | the deterministic checks and the four groups (the tool-path checks included) |
| `python/ask_tool.py` | helpers for the tool path: the last turn of a trace, its timeline, the `AskUserQuestion` calls with their results, the text between the calls, the `ToolSearch` calls |
| `../_common/python/agent_trace.py`, `runner_cmd.py` | the stream-json reader (the fields in the table above) and the command helper, shared: `evals/_common` |
| `python/cases.py`, `judge.py`, `rubric.schema.json`, `report.py`, `demo.py`, `measure.py` | CSV loader, rubric judge and schema, before/after report, offline demo, timing |
| `tests/` | one test file per module (269 tests), `test_skill.py` (the live plugin is the subject), `test_fixtures.py` (traces and captures equal their sources), `test_tool_path.py` (the tool path: selection, `MUTATIONS`, variants), stub agent and judge |
| `tests/fixtures/traces/after/`, `before/` | the INVENTED traces, 11 per set; `tests/fixtures/README.md` explains the marks of invention |
| `tests/fixtures/traces/tool/` | two INVENTED one-turn walks of the tool path, every call answered by a `tool_result` before the next; the `tool_result` text uses the wrapper observed once (UNCONFIRMED across versions); the input shape is documented |
| `tests/fixtures/traces/real/` | one REAL trace (`explicit-name`), trimmed by `tests/trim_real_trace.py` |
| `captures/` | the recorded output of `make grade-fixtures` |
| `Makefile`, `python/Makefile` | the entry points |

## Provenance

| Source | What it gives |
|---|---|
| The PA015 sample `05-oaat-skill-evals` (Ondrej Krajicek, course PA015, Masaryk University), commit `b1791795` | the whole harness, the prompts, the fixtures and the recorded runs, copied here without the container files, the bundled plugin copies and the hash-pinning tests |
| This repository, `evals/_common/python/` | `agent_trace.py` (the stream-json reader) and `runner_cmd.py` (the command runner) moved out of `python/` into the module shared by every harness under `evals/`; `python/run_evals.py`, `demo.py`, `judge.py`, `measure.py` and `tests/helpers.py` put it on `sys.path`, and `tests/test_trace.py` fails if a local copy comes back in `python/` (`evals/_common/tests/test_layout.py` scans the whole harness, `tests/` included). This directory does not run without that sibling. Since the move the cost of a trial is the `total_cost_usd` of the last result event, not the sum |
| OpenAI, "Testing Agent Skills Systematically with Evals", developers.openai.com/blog/eval-skills, read 2026-11-04 (the page shows no date or byline) | the four success groups, the prompt CSV with `should_trigger`, the JSONL trace per prompt, deterministic checks over the trace, the rubric schema |
| Mullen and Gunderman, "The Anatomy of Harness Engineering", Google for Developers Blog, 2026-09-09, read 2026-11-04 | the behavioral eval (assert on a tool call and observable behavior, not on prose), aggregate pass rates over batch runs |
| Claude Code documentation, code.claude.com/docs, read 2026-11-04 | the stream-json shape (table above) |

The traces in `tests/fixtures/traces/after/` and `before/`, and the captures built from them, are invented. `tests/fixtures/traces/real/explicit-name.jsonl` and `captures/grade-real.txt` come from one real run in the sample.
