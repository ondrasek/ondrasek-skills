# ondrasek-skills

Claude Code plugin marketplace (`ondrasek-skills`) holding personal agent skills.

## Structure

- `.claude-plugin/marketplace.json` — catalog; every plugin must be listed here.
- `plugins/<name>/.claude-plugin/plugin.json` — plugin manifest.
- `plugins/<name>/skills/<skill>/SKILL.md` — skills; `description` frontmatter drives triggering.

## Rules

- After any change run `claude plugin validate . --strict`; fix all errors and warnings.
- Adding or removing a plugin: update `marketplace.json` and the plugin table in `README.md`.
- Keep `version` in `plugin.json` and `marketplace.json` in sync; bump on user-visible changes.
- Skill directory name must match the `name` in its `SKILL.md`.
