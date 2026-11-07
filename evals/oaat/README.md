# Evals of the `oaat` plugin

How do you know that `oaat` still works after you edit `plugins/oaat/skills/oaat/SKILL.md`? A reread of the text cannot tell you; an **eval** can: run the agent on fixed prompts and grade what it did. This directory is a small, complete eval harness for the live plugin in `plugins/oaat`. It runs offline on invented traces (free, no model call), and on your own agent when you ask.

It was developed as a teaching sample for the course PA015 (Masaryk University) and replicated here, pointed at the live plugin instead of a bundled copy. The record of the first real runs (sections "The first real run" to "Isolating the run") is from that sample, where the plugin was bundled as `skill/`.

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

- **`captures/grade-after.txt`**: the table of the run with the edited skill. All 11 prompts pass, every check of every group (`checks passed by group: outcome 43/43, process 22/22, style 31/31, efficiency 30/30`), `pass rate 100%`, exit code 0.
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

**The three follow-up rows as two turns** (`--label followup-two-turn`, $0.51). The skill loaded on all three (`process` 6/6). Findings of the replies:

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

The original skill described the reply as a list of five parts, and real replies then differed from run to run: lettered or numbered options, the recommendation inside an option or in a bullet after the list, `Question:` for `One question:`. On 2026-11-05 the author set one structure: options always numbered, the recommendation on a line of its own after the options, never a bullet. The checks `options-numbered` and `recommended-marked` test it, and `plugins/oaat` (version 0.2.0) now carries the template in section 3 and the wrap-up. **No real run has graded the template yet.** Run `make eval ARGS="--trials 3 --label template"` (about 33 model calls, see the cost table) and compare it with a run of the previous text from git history (`git show <commit>:plugins/oaat/skills/oaat/SKILL.md`, then `--skill-path` to a copy of the plugin).

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
| `completed` | outcome | all | the result event is `success`, `is_error` false, reply not empty |
| `tool-detours` | efficiency | all | at most 2 tool calls besides `Skill` |
| `turns-bound` | efficiency | all | `num_turns` at most 4 |
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
| `length-bound` | efficiency | item, skip, stop, tangent | at most 12 non-empty lines and 150 words |
| `wrapup-headings` | outcome | stop | Decided, Parked, Next action, in that order |
| `tangent-flag` | outcome | tangent | "This may be a tangent from X. Continue or refocus?" |
| `all-items-listed` | outcome | list | every pending title appears in the reply |
| `no-oaat-header` | outcome | stop, tangent, list, none | no `Item N of M` in the reply |

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

The harness reads these fields of `claude -p --output-format stream-json --verbose` and no others (`python/agent_trace.py`). The sources are the Claude Code documentation pages read on 2026-11-04.

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
- **The `input.skill` field is verified once**, on one version (see the table above). The documentation does not list it.
- **The bounds are guesses from one real reply.** `MAX_REPLY_WORDS` (150) and `MAX_REPLY_LINES` (12) came from the skill's "about 8 lines" and from that reply (143 words and 9 lines without the closing block). Run 3 to 5 trials of every prompt, then set the bounds from the replies that you accept.
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
| `python/checks.py` | the deterministic checks and the four groups |
| `python/agent_trace.py` | the stream-json reader (the fields in the table above) |
| `python/cases.py`, `judge.py`, `rubric.schema.json`, `report.py`, `runner_cmd.py`, `demo.py`, `measure.py` | CSV loader, rubric judge and schema, before/after report, command helper, offline demo, timing |
| `tests/` | one test file per module, `test_skill.py` (the live plugin is the subject), `test_fixtures.py` (traces and captures equal their sources), stub agent and judge |
| `tests/fixtures/traces/after/`, `before/` | the INVENTED traces, 11 per set; `tests/fixtures/README.md` explains the marks of invention |
| `tests/fixtures/traces/real/` | one REAL trace (`explicit-name`), trimmed by `tests/trim_real_trace.py` |
| `captures/` | the recorded output of `make grade-fixtures` |
| `Makefile`, `python/Makefile` | the entry points |

## Provenance

| Source | What it gives |
|---|---|
| The PA015 sample `05-oaat-skill-evals` (Ondrej Krajicek, course PA015, Masaryk University), commit `b1791795` | the whole harness, the prompts, the fixtures and the recorded runs, copied here without the container files, the bundled plugin copies and the hash-pinning tests |
| OpenAI, "Testing Agent Skills Systematically with Evals", developers.openai.com/blog/eval-skills, read 2026-11-04 (the page shows no date or byline) | the four success groups, the prompt CSV with `should_trigger`, the JSONL trace per prompt, deterministic checks over the trace, the rubric schema |
| Mullen and Gunderman, "The Anatomy of Harness Engineering", Google for Developers Blog, 2026-09-09, read 2026-11-04 | the behavioral eval (assert on a tool call and observable behavior, not on prose), aggregate pass rates over batch runs |
| Claude Code documentation, code.claude.com/docs, read 2026-11-04 | the stream-json shape (table above) |

The traces in `tests/fixtures/traces/after/` and `before/`, and the captures built from them, are invented. `tests/fixtures/traces/real/explicit-name.jsonl` and `captures/grade-real.txt` come from one real run in the sample.
