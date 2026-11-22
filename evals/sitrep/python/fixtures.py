"""Fixture repositories for the evals, built in code (see state.prepare for the git side).

    open-items     a repo whose docs/open-questions.md holds five planted open items; the README points to it
    open-items-ms  open-items plus a milestone tracker, .cache/milestones.json, which the repo's own .gitignore ignores
    bare           a repo with no open items (for prompts that must not trigger the skill)

Planted items in open-items (PLANTED holds one pattern per item; the checks look for them by these words):
    db         decision  Postgres or SQLite for the sync service
    sso        decision  SSO vendor
    licence    risk      licence audit of bundled fonts
    retention  question  data retention period
    migration  blocker   schema migration cannot start until the database is chosen  (the planted dependency: it depends on db)

Tracker in open-items-ms: 1 Schema design done, 2 Storage layer doing, 3 Release sign-off blocked, needs you, left "signing key
from legal", 4 Docs todo. SKILL.md step 1 turns milestone 3 into the item ms-3 and the others into the progress strip.
"""
import json

PLANTED = {
    "db": r"postgres|sqlite",
    "sso": r"\bsso\b",
    "licence": r"licen[cs]e",
    "retention": r"retention",
    "migration": r"migration",
}

TEXTUTIL = '''def shout(text):
    return text.upper() + "!"
'''
README = "# Sync service\n\nSmall text utilities for the sync service. Open decisions and risks are tracked in docs/open-questions.md.\n"
OPEN_QUESTIONS = """# Open questions

1. **Decision: Postgres or SQLite for the sync service?** Postgres scales and allows concurrent writers but needs operations; SQLite is simple but has a single writer. Nobody has decided.
2. **Decision: which SSO vendor?** Two vendors are shortlisted. The pilot customer wants SSO before the first release.
3. **Risk: licence audit of bundled fonts.** Three bundled fonts have no licence file. A customer audit could force their removal.
4. **Question: what is the data retention period?** Legal has not answered. It shapes the deletion job.
5. **Blocker: the schema migration cannot start until the database is chosen.** The migration scripts differ between Postgres and SQLite.
"""
TRACKER = {
    "title": "To release 1.0",
    "items": [
        {"id": 1, "name": "Schema design", "state": "done"},
        {"id": 2, "name": "Storage layer", "state": "doing", "left": "write path"},
        {"id": 3, "name": "Release sign-off", "state": "blocked", "needs": "you", "left": "signing key from legal"},
        {"id": 4, "name": "Docs", "state": "todo"},
    ],
}


def _bare():
    return {"README.md": "# Sync service\n\nSmall text utilities for the sync service.\n", "src/textutil.py": TEXTUTIL}


def _open_items():
    return {"README.md": README, "src/textutil.py": TEXTUTIL, "docs/open-questions.md": OPEN_QUESTIONS}


def _open_items_ms():
    # the user's global gitignore does not apply in the throwaway repo, so the fixture ships its own line
    return {**_open_items(), ".gitignore": ".cache/\n", ".cache/milestones.json": json.dumps(TRACKER, indent=2) + "\n"}


FIXTURES = {"open-items": _open_items, "open-items-ms": _open_items_ms, "bare": _bare}


def build(name):
    """{relative path: text} of fixture `name`; raises KeyError for an unknown name."""
    return FIXTURES[name]()
