# ondrasek-skills

Personal collection of AI agent skills, packaged as a [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

## Install

```
/plugin marketplace add ondrasek/ondrasek-skills
/plugin install <plugin>@ondrasek-skills
```

On Claude Code 2.1.275+ this works in one step, e.g. `/plugin install oaat --marketplace ondrasek/ondrasek-skills` or `/plugin install sitrep --marketplace ondrasek/ondrasek-skills`

## Layout

```
.claude-plugin/marketplace.json     # marketplace catalog (lists every plugin)
plugins/<plugin>/
  .claude-plugin/plugin.json        # plugin manifest
  skills/<skill>/SKILL.md           # one directory per skill
```

Each plugin groups related skills. Add `agents/`, `commands/`, `hooks/` or `.mcp.json` inside a plugin when needed.

## Adding a plugin

1. Copy an existing plugin (e.g. `plugins/oaat`) to `plugins/<name>` and edit `plugin.json`.
2. Add skills under `plugins/<name>/skills/<skill>/SKILL.md`.
3. Register the plugin in `.claude-plugin/marketplace.json`.
4. Run `claude plugin validate .`

## Optional: let `sitrep` open the report

Opening apps is blocked inside the Claude Code sandbox, so `sitrep` prints the path and a `! open "<path>/index.html"` line to run yourself. To let it open the report directly, add this to your own `~/.claude/settings.json` (nothing in this repo applies it):

```json
{
  "sandbox": { "excludedCommands": ["open */sitrep.*/index.html"] },
  "permissions": { "allow": ["Bash(open */sitrep.*/index.html)"] }
}
```

- `sandbox.excludedCommands` runs matching commands outside the sandbox; the allow rule skips the prompt.
- The pattern is deliberately narrow. `open *` would run anything starting with `open` with full access.
- Patterns match the command text, so this is not airtight: extra arguments can still match the wildcard.
- On Linux use `xdg-open` instead of `open`.

## Optional: show the milestones in the statusline

A plugin cannot register a statusline, so the `milestones` plugin's `/milestones` skill works on its own and the statusline is a setting you add yourself. Add this to your `~/.claude/settings.json` (nothing in this repo applies it):

```json
{
  "statusLine": {
    "type": "command",
    "command": "sh \"$(ls -d ~/.claude/plugins/cache/ondrasek-skills/milestones/*/skills/milestones/scripts/statusline | sort -V | tail -1)\"",
    "padding": 0
  }
}
```

- The installed plugin path contains the version (`.../milestones/0.1.0/...`), so the command picks the highest installed version and survives updates.
- It prints, for example, `To release 1.0 ██▌░░░ 1/4 · ▸ #2 1h12m · next: Storage layer · ⚑ you · ⛔1`, then context, credit spend, time, plan limits and model. The credit part (`cost`) shows the estimated session cost only for API-key / console-credit users, `spend $used / $limit` behind a gateway spend limit, and nothing for subscribers (their plan limits are the `5h` / `7d` part). A project without `.cache/milestones.json` shows no milestone segment.
- Settings: `MILESTONES_STATUSLINE_PARTS` (comma list of `milestones,timing,ctx,cost,time,limits,model`), `MILESTONES_STATUSLINE_BASE` (a shell command whose output comes first), `NO_COLOR` or `MILESTONES_STATUSLINE_COLOR=0` (no colour).
- `.cache/` should be in your gitignore; the tracker is per checkout.

## The stale-tracker reminder

The `milestones` plugin registers a `Stop` hook (`hooks/milestones-reminder.sh`, needs `jq` and `git`). After a turn it nudges the agent to update the tracker when all of these hold: a milestone is `doing`, the tracker file is at least 30 minutes old (`MILESTONES_STALE_MINUTES`), and the repository changed after it (uncommitted paths outside `.cache/`, or a newer commit). It nudges at most once per window. It does nothing in a project without a tracker. If you had the same hook in `~/.claude/settings.json`, remove that entry: the plugin one replaces it.

## The git hook plugins

Two small plugins, each with one hook, independent of each other. Both need `jq`; `auto-commit` also needs `git` (and uses `gitleaks` when installed).

- **`auto-commit`**: a `Stop` hook. After each turn it checks the repository (untracked, unstaged and staged changes, unpushed commits, remote commits not merged, `.env` files, secrets that `gitleaks` finds in unpushed commits). If the repository is not saved and in sync, it blocks the stop once and tells the agent to snapshot the work in a Conventional Commit and sync with origin: merge only, no rebase, stash or squash, no force push, no `--no-verify`, no `.env` files. `.cache/` is ignored. **This makes the agent commit and push without asking, in every project where the plugin is enabled**; enable it per project if that is too much.
- **`block-force-push`**: a `PreToolUse` hook on `Bash`. It denies `git push --force`, `-f` (also inside option clusters such as `-fu`) and `--force-with-lease`, and tells the agent to ask you first. It matches the command text, so it is a safety net, not a security boundary: a force push hidden behind an alias, a script or a variable passes, and a command that merely contains such text (for example inside a heredoc) is blocked.

If you have the same hooks in `~/.claude/settings.json` or `~/.claude/hooks/`, remove them after installing the plugins, otherwise `auto-commit` reports twice.

## Tests and evals

```
python3 -m unittest discover -s tests/milestones -v   # milestones plugin
python3 -m unittest discover -s tests/auto-commit -v   # auto-commit plugin (slow: about 80 s)
python3 -m unittest discover -s tests/block-force-push -v
make -C evals/oaat test                                # oaat eval harness, offline, no model call
make -C evals/oaat eval ARGS="--only explicit-name --trials 1"   # a REAL run: one model call on your account
```

`evals/oaat/` grades the live `plugins/oaat` skill (deterministic checks on the reply and the tool calls, optional rubric judge); see its [README](evals/oaat/README.md).

## How the plugins work together

- `milestones` writes `.cache/milestones.json` in a project. Nothing else writes it.
- `oaat` reads it: a milestone that is `blocked` or `needs` the user becomes one of the items, and an answer is recorded back through the `milestones` skill.
- `sitrep` reads it: such milestones become cards, and a progress strip shows done/total and the current milestone. Answers pasted back with **Copy JSON** update the milestone through the `milestones` skill.
- Each integration is optional: without the tracker file, or without the `milestones` plugin, `oaat` and `sitrep` behave as before.

## Plugins

| Plugin | Description |
| --- | --- |
| `oaat` | Walks through pending decisions, questions and risks strictly one at a time (ADHD-friendly), in a fixed reply template; picks up milestones that wait on you. Invoke with `/oaat` or `/oaat:oaat`. |
| `sitrep` | Builds a temporary, self-contained HTML situation report of everything open (Focus / Matrix / Map / All tabs). Add comments per item in the page, then **Copy JSON** and paste it back into Claude. Invoke with `/sitrep` or `/sitrep:sitrep`. Shows milestone progress when a tracker exists. Output goes to a temp dir and is deleted on request. |
| `milestones` | A per-project milestone tracker (`.cache/milestones.json`): `/milestones` shows a table with a progress bar, durations and who must act; the agent keeps it current. Optional colour statusline segment (see above) and a stale-tracker reminder hook. Invoke with `/milestones` or `/milestones:milestones`. |
| `auto-commit` | Stop hook: after each turn, makes the agent snapshot uncommitted work in a Conventional Commit and keep the branch in sync with origin (merge only; no `.env` files or secrets). It pushes without asking: see "The git hook plugins". |
| `block-force-push` | PreToolUse hook: denies force pushes (`--force`, `-f`, `--force-with-lease`) and tells the agent to ask you first. A text match, not a security boundary. |
