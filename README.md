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
- It prints, for example, `To release 1.0 ██▌░░░ 1/4 · ▸ #2 1h12m · next: Storage layer · ⚑ you · ⛔1`, then context, cost, time, plan limits and model. A project without `.cache/milestones.json` shows no milestone segment.
- Settings: `MILESTONES_STATUSLINE_PARTS` (comma list of `milestones,timing,ctx,cost,time,limits,model`), `MILESTONES_STATUSLINE_BASE` (a shell command whose output comes first), `NO_COLOR` or `MILESTONES_STATUSLINE_COLOR=0` (no colour).
- `.cache/` should be in your gitignore; the tracker is per checkout.

## Tests and evals

```
python3 -m unittest discover -s tests/milestones -v   # milestones plugin
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
| `milestones` | A per-project milestone tracker (`.cache/milestones.json`): `/milestones` shows a table with a progress bar, durations and who must act; the agent keeps it current. Optional colour statusline segment (see above). Invoke with `/milestones` or `/milestones:milestones`. |
