"""Fixture repositories for the evals, built in code.

They are generated at run time, never stored as files, because a committed CLAUDE.md or AGENTS.md inside this repository
would be loaded by Claude Code when anyone works here, and audited by the skill when it audits this repository.

    defects  a 229-line CLAUDE.md with planted defects and a divergent AGENTS.md (the CLAUDE.md imports it)
    clean    a short, correct CLAUDE.md whose every claim holds in the repo
    bare     a repo with no instruction file at all (for prompts that must not trigger the skill)

Planted defects in `defects` (the checks look for each by name):
    dead import       CLAUDE.md line 5 imports @docs/missing-guide.md; docs/ does not exist
    stale command     CLAUDE.md line 3 demands `npm run typecheck`; package.json has no such script
    emphasis          the same line shouts IMPORTANT / ALWAYS / NEVER EVER
    derivable, stale  an "Architecture" section naming src/legacy/engine.ts, which does not exist
    filler            214 identical generic rules, so the file is far over 200 lines
    contradiction     "Use tabs" and "Use 2-space" in CLAUDE.md, "Use 4-space" in AGENTS.md
"""

PACKAGE = '{"name": "demo", "scripts": {"test": "vitest", "lint": "eslint ."}}\n'
INDEX = "export const a = 1;\n"


def _defects():
    head = [
        "# Demo project",
        "",
        "IMPORTANT: ALWAYS run `npm run typecheck` before committing. NEVER EVER skip this!!!",
        "",
        "See @docs/missing-guide.md for details and @AGENTS.md too.",
        "",
        "## Architecture",
        "The src/ directory contains index.ts. The file src/legacy/engine.ts handles the engine.",
        "Dependencies: vitest, eslint, typescript.",
        "",
    ]
    filler = [f"- Filler rule {i}: write clean, readable code and be careful." for i in range(1, 215)]
    tail = ["", "Never edit src/gen/client.ts, it is generated.", "Write good code.", "Use tabs for indentation.", "Use 2-space indentation."]
    agents = "# Agents\n- Tests: `npm test`. Lint: `npm run lint`.\n- Use 4-space indentation.\n- Deploys go through the `release` branch only.\n"
    return {"CLAUDE.md": "\n".join(head + filler + tail) + "\n", "AGENTS.md": agents, "package.json": PACKAGE, "src/index.ts": INDEX}


def _clean():
    claude = ("# Demo project\n\n## Commands\n- Test: `npm test`; one file: `npx vitest run src/index.test.ts`\n- Lint: `npm run lint`\n\n"
              "## Gotchas\n- `src/gen/` is produced by `npm run codegen`; never edit it by hand.\n")
    package = '{"name": "demo", "scripts": {"test": "vitest", "lint": "eslint .", "codegen": "node scripts/codegen.js"}}\n'
    return {"CLAUDE.md": claude, "package.json": package, "src/index.ts": INDEX, "src/index.test.ts": "import { a } from './index';\n",
            "src/gen/client.ts": "// generated\n", "scripts/codegen.js": "console.log('codegen');\n"}


def _bare():
    return {"package.json": PACKAGE, "src/index.ts": INDEX}


FIXTURES = {"defects": _defects, "clean": _clean, "bare": _bare}


def build(name):
    """{relative path: text} of fixture `name`; raises KeyError for an unknown name."""
    return FIXTURES[name]()
