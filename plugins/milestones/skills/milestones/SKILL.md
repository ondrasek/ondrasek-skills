---
name: milestones
description: Show the milestone tracker of this checkout — what has to be completed before the work can go on, as a table with a progress bar, durations and who must act — and keep it current. Use when the user types /milestones, asks "where are we", "what is left before …" or "show the progress bar", and whenever a tracked milestone starts, finishes, gets blocked or needs them.
allowed-tools: Read, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/milestones.py *)
---

# /milestones — the milestone tracker

The tracker is `.cache/milestones.json` in the project (per checkout; add `.cache/` to the
project's or your global gitignore; schema `${CLAUDE_SKILL_DIR}/schema/milestones.schema.json`).
The commands act on the project of the working directory (the nearest directory with `.git`,
or `$CLAUDE_PROJECT_DIR`); the statusline reads the tracker of the session's
`workspace.project_dir`, so a user-level statusline shows each project's own tracker and none
where a project has no file. The Claude Code statusline shows it on every refresh once the
user registers it (`scripts/statusline`; the recipe is in the marketplace README), for example
`To F2/F3 ██▌░░░ 1/4 · ▸ #2 1h12m · next: Job claims and handover · ⚑ you · ⛔1`.

## Show it

```sh
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" show
```

Present the table as it prints (#, state ✅ done · 🟡 doing · ⬜ todo · ⛔ blocked, milestone,
what is left, who must act, duration) and the bar line under it. Then say in one or two lines
what is next and what waits for the user (`needs`). `show --json` gives the same data with
`progress`, `elapsed_seconds` and each item's `duration_seconds`. With no file, `show` exits 1:
offer to start one (below; `add` and `title` also start one on a missing file, an empty
tracker shows no statusline segment).

## Keep it current (the agent's job)

Update the file at each milestone, in the same turn the work changes — never in a batch later:

```sh
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" set 2 --state doing            # records started
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" set 2 --state done --left ""    # records finished
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" set 4 --state blocked --needs you --left "turn and depth caps"
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" add --name "Factory page board" --left "board and cards"
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" remove 5
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" title "To F2/F3"
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" init      # an example file to edit; --force replaces one
python3 "${CLAUDE_SKILL_DIR}/scripts/milestones.py" init --empty --title "Thesis"   # a tracker with no items
```

- `started` is written when an item first goes to `doing`, `finished` when it goes to `done`
  (removed when it leaves `done`); the Duration column and the statusline's `▸ #N 1h12m` read
  them. `--left ""` and `--needs ""` clear the field.
- `needs: you` (or "author", or a name, form of address or email from the optional `authors/<email>.md` profiles) puts `⚑ you` on the statusline while the item is not done: set it only when
  the next step is the author's (a decision, a sign-off, a key).
- Every write validates against the schema and replaces the file atomically; a refused write
  (exit 2) names the rule and leaves the file as it was.

## The statusline

`scripts/statusline` prints this segment on its own (a base runs first only when
`$MILESTONES_STATUSLINE_BASE` names one, then ` │ `). Parts, set with
`MILESTONES_STATUSLINE_PARTS` (comma list): `milestones` (title, bar, count, next, `⚑ you`, `⛔N`),
`timing` (the current milestone's elapsed time), and the session usage Claude Code sends on
stdin — `ctx` (context window used %), `cost` (session cost, USD estimate), `time` (session
wall time), `limits` (5-hour and 7-day plan usage %, present for Pro/Max/Ultra subscribers),
`model` (the model's display name and the session's effort level from stdin, and the advisor
from `advisorModel` in the user, project and project-local settings files, e.g.
`Opus 5.5 · effort high · advisor Fable`; a value Claude Code does not expose prints nothing).
Unset, the parts are `milestones,timing` when a base statusline printed something (it
shows its own usage fields) and all of them otherwise.

The segment is coloured with ANSI codes: the bar and the current milestone yellow, `⚑ you`
bold yellow, `⛔N` bold red, the ctx and limit percentages green below 50 %, yellow below
80 %, red from 80 %, the model part cyan. `NO_COLOR` (set, non-empty) or
`MILESTONES_STATUSLINE_COLOR=0` turns colour off; `show` colours its table only on a terminal.
