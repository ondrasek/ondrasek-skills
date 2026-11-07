# ondrasek-skills

Claude Code plugin marketplace (`ondrasek-skills`) holding personal agent skills.

## Structure

- `.claude-plugin/marketplace.json` — catalog; every plugin must be listed here.
- `plugins/<name>/.claude-plugin/plugin.json` — plugin manifest.
- `plugins/<name>/skills/<skill>/SKILL.md` — skills; `description` frontmatter drives triggering.

- `tests/milestones/` — unit tests of the `milestones` plugin (script, statusline, `hooks/` reminder).
- `tests/auto-commit/`, `tests/block-force-push/` — tests of the two git hook plugins (`plugins/<name>/hooks/`).
- `evals/oaat/` — eval harness for the live `plugins/oaat` skill (ported from the PA015 sample).

## Rules

- After any change run `claude plugin validate . --strict`; fix all errors and warnings.
- Adding or removing a plugin: update `marketplace.json` and the plugin table in `README.md`.
- Keep `version` in `plugin.json` and `marketplace.json` in sync; bump on user-visible changes.
- Skill directory name must match the `name` in its `SKILL.md`.
- Run `python3 -m unittest discover -s tests/milestones` and `make -C evals/oaat test` (offline) after touching `milestones` or `oaat`.
- Never run `make -C evals/oaat eval` unasked: it spends model calls on the user's account.
- `oaat` and `sitrep` read `.cache/milestones.json` and write it only through the `milestones` skill; never hardcode another plugin's path.
- A plugin cannot register a statusline: it is a user setting (recipe in `README.md`).
- Bash commands containing force-push text (even in a heredoc) are blocked by the user-level force-push hook: write such test data and docs with the Write or Edit tool.
