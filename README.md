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

## Plugins

| Plugin | Description |
| --- | --- |
| `oaat` | Walks through pending decisions, questions and risks strictly one at a time (ADHD-friendly). Invoke with `/oaat` or `/oaat:oaat`. |
| `sitrep` | Builds a temporary, self-contained HTML situation report of everything open (Focus / Matrix / Map / All tabs). Add comments per item in the page, then **Copy JSON** and paste it back into Claude. Invoke with `/sitrep` or `/sitrep:sitrep`. Output goes to a temp dir and is deleted on request. |
