---
name: sitrep
description: Use when the user wants a visual overview, big picture, map, dashboard or situation report of everything that is open (decisions, questions, risks, concerns, blockers, dependencies), or says "show me the situation", "what's the state of things", "visualize this", or pastes JSON feedback copied from a sitrep report. Builds a temporary HTML report instead of a text list.
---

# Sitrep

Show the whole situation at a glance for a visual thinker with ADHD. Output is a temporary, self-contained HTML page: a "start here" card and tabs (Focus with the top 3 items, Matrix, Map, All). Every card has a comment box and a Resolved checkbox, and a "Copy JSON" button exports the feedback for pasting back here. It is never committed and is deleted when no longer needed.

## 1. Collect silently

Gather every open item from the conversation, tool output, notes and files in scope: decisions, questions, clarifications, concerns, risks, blockers, unverified assumptions. Dedupe and merge overlaps. Note which items must be settled before others (`dependsOn`).

If the project has a milestone tracker (`.cache/milestones.json` in the project root; read it with Read, it is written by the `milestones` plugin), take it in too. It is optional: no file, no change.

- Every milestone that is not `done` and is `blocked`, or whose `needs` names the user (`you`), is an open item: type `blocker` for `blocked`, `decision` otherwise; `detail` from its `left` text; `status: "blocked"` for `blocked`. Give these high urgency (3) and use a stable id such as `ms-<id>`.
- Milestones that are only `todo` or `doing` and need nobody are not items. Put them in the progress strip instead: `milestones: { title, done, total, current, needsYou, blocked }` (see [schema.md](schema.md)). `current` is the name of the milestone in `doing`.
- A milestone item `dependsOn` nothing unless another open item must be settled first.

Rate each item **impact** and **urgency** from 1 (low) to 3 (high). Pick the single best next action for the "start here" card: blockers and items that unlock others first. A `blocked` milestone that waits for the user is a strong candidate.

Keep text short: titles of a few words, `detail` one or two sentences. If there are more than about 15 items, merge small ones or mark minor ones `parked`.

## 2. Build the report

1. Create the temp dir (also sweeps stale ones): `"${CLAUDE_SKILL_DIR}/scripts/report.sh" new`. It prints the directory path, referred to below as `DIR`. It contains `index.html`.
2. Write `DIR/data.js` defining `window.SITREP`. Format and a full example are in [schema.md](schema.md). Write data only; do not edit `index.html`.
3. Reuse the same `DIR` when regenerating in the same session; overwrite `data.js`. Do not write the report anywhere else, and never into the project, a repo or an artifact.

The template renders all text safely (no HTML interpretation), so item text may contain anything. Never put secrets or credentials in the data.

## 3. Open it

Try to open it: `open "DIR/index.html"` (macOS), `xdg-open` (Linux) or `start` (Windows). Opening apps is often blocked in a sandbox. If it fails, do not retry with the sandbox disabled. Instead print the absolute path and a ready-to-run line, and tell the user to run it with the `!` prefix:

```
! open "DIR/index.html"
```

Then give a 2 to 3 line spoken summary of the situation (counts, the "start here" item), and tell them they can write comments on items in the page and press **Copy JSON** to paste them back here. Do not paste the item list into chat.

## 4. Read pasted feedback

The user may paste JSON from the report's **Copy JSON** button: `{"sitrep": "...", "for_claude": "...", "items": [{"id", "n", "title", "comment", "resolved"}]}`. Only items they commented on or resolved are included. When you see it:

- Treat each `comment` as the user's answer or thoughts on the item with that `id`. Refer to items by number and title.
- Answer briefly per item: what you will do or have done. Ask a follow-up only where a comment is unclear, one question at a time.
- Apply decisions you can apply. Treat `resolved: true` as settled and do not reopen it. When a resolved or answered item has the id `ms-<n>`, update that milestone through the `milestones` skill (for example clear `needs`, or set it `doing`); if that plugin is not installed, say so in one line and leave the tracker alone.
- Then offer to regenerate the report: overwrite `DIR/data.js` in the same directory if it still exists, drop settled items, add new items the comments raised, and keep every surviving `id` unchanged. The page remembers comments by `id` in the browser, so stable ids keep unsent comments in place.

## 5. Clean up

When the user says they are done, or the session ends, delete the report: `"${CLAUDE_SKILL_DIR}/scripts/report.sh" clean DIR`. The script refuses to delete anything that is not a `sitrep.*` directory directly under the temp dir. If the user answers or resolves items, offer to regenerate the report rather than editing it by hand.
