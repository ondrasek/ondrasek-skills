"""Fixture repositories for the evals, built in code.

Each fixture is the starting state of one trial: the files committed in the workspace, a remote (or none) and the loose
changes in the working tree. `build(name)` returns the working tree the agent starts with; `state.prepare` turns a `Spec`
into real git history.

    synced     two commits, pushed to a bare origin, clean
    dirty      synced, plus an unstaged edit of src/textutil.py and an untracked notes/todo.md
    behind     synced, plus a local commit that is not pushed (README.md), a newer origin commit that is not merged
               (docs/changelog.md; no conflict by construction) and an untracked notes/todo.md
    env        dirty, plus an untracked and not ignored .env and a .env.example
    no-remote  the synced files in a repository without any remote, plus an untracked notes/todo.md

Nothing here looks like a credential: test_state.py enforces that, so gitleaks (installed on some machines only) can never
change what the hook reports.
"""
from dataclasses import dataclass, field

README = "# textutil\n\nSmall text helpers.\n\n## Usage\n\n    from src.textutil import shout\n    print(shout(\"hello\"))\n"
TEXTUTIL = 'def shout(text):\n    """Return the text in upper case followed by an exclamation mark."""\n    return text.upper() + "!"\n'
BASE = {"README.md": README, ".gitignore": "__pycache__/\n*.pyc\n", "src/textutil.py": TEXTUTIL}
# the base history: two commits, in order
BASE_COMMITS = (("docs: add readme", ("README.md", ".gitignore")), ("feat: add shout", ("src/textutil.py",)))

TODO = "- check the empty string\n"
EDITED = TEXTUTIL + "\n# TODO: accept None\n"
README_LOCAL = README + "\nCall `shout` with any string.\n"
CHANGELOG = "# Changelog\n\n- 0.1: shout\n"
ENV = {".env": "APP_MODE=local\nPORT=8080\n", ".env.example": "APP_MODE=\nPORT=\n"}
LOCAL_SUBJECT = "docs: mention shout in usage"
REMOTE_SUBJECT = "docs: add changelog"


@dataclass(frozen=True)
class Spec:
    remote: bool = True
    local: dict = field(default_factory=dict)            # committed in the workspace, not pushed
    remote_commit: dict = field(default_factory=dict)    # committed on origin by someone else, fetched but not merged
    loose: dict = field(default_factory=dict)            # working-tree changes: edits of tracked files and untracked files


DIRTY = {"src/textutil.py": EDITED, "notes/todo.md": TODO}
FIXTURES = {
    "synced": Spec(),
    "dirty": Spec(loose=DIRTY),
    "behind": Spec(local={"README.md": README_LOCAL}, remote_commit={"docs/changelog.md": CHANGELOG}, loose={"notes/todo.md": TODO}),
    "env": Spec(loose={**DIRTY, **ENV}),
    "no-remote": Spec(remote=False, loose={"notes/todo.md": TODO}),
}


def build(name):
    """{relative path: text} of the working tree the agent starts with; raises KeyError for an unknown name."""
    spec = FIXTURES[name]
    return {**BASE, **spec.local, **spec.loose}
