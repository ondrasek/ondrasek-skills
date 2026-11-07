#!/usr/bin/env bash
# Auto-commit hook (Stop) of the `auto-commit` plugin: keep the repository saved and in sync with origin.
#
# DECLARATIVE: the hook only reports the state it finds (untracked files,
# unstaged changes, staged-but-uncommitted changes, unpushed commits, remote
# changes not merged, .env files) plus the goal and a few guardrails. HOW Claude
# gets there (which git commands, in which order) is Claude's job.
#
# Priority: HISTORY over tidiness — we always want a revert point, so work in
# progress is snapshotted immediately (a broken WIP commit is fine; a missing
# one is not).
#
# Guardrails, enforced as far as code can:
#   - never rebase / stash / squash / rewrite history; merge only
#   - never force-push (the `block-force-push` plugin also blocks it)
#   - never bypass git hooks (no --no-verify): "they are there for a reason"
#   - .cache/ is always ignored: never reported (keep it in your global gitignore)
#   - never push credentials: when `gitleaks` is installed, unpushed commits are scanned
#     with it (content based, redacted output). Without gitleaks the scan is skipped;
#     a git pre-commit hook running gitleaks refuses a commit up front.
#   - never add/commit/push .env files (.env.example is fine). Detected with
#     `git ls-files` (tracked, or untracked-and-not-ignored) and in unpushed
#     commits; the message then forbids pushing.
#
# Concurrency: read-only, git runs with --no-optional-locks, so parallel
# sessions never contend on .git/index.lock; writes are serialised by git
# itself. No hook-level lock: it would only cover the detection and could
# cause a missed snapshot.
#
# Exit 0 = clean, nothing to do. Exit 2 = blocking; the report is on stderr.

# Once per turn, never loop: when Claude is already continuing because of a
# Stop hook (stop_hook_active), let the turn end.
_hook_input=$(cat 2>/dev/null || true)
[ "$(printf '%s' "$_hook_input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0

# All git calls here are read-only: never take optional locks.
git() { command git --no-optional-locks "$@"; }

# --- Guard: not inside a git repo → exit silently ---
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# Guard against re-entry from a nested hook process
[ -n "$CLAUDE_HOOK_RUNNING" ] && exit 0
export CLAUDE_HOOK_RUNNING=1

# In a worktree .git is a file (gitdir: pointer): stay there — CLAUDE_PROJECT_DIR
# may point to the main repo and we must not auto-commit the wrong branch.
_toplevel=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$_toplevel" ] && [ -f "$_toplevel/.git" ]; then
    cd "$_toplevel"
else
    cd "${CLAUDE_PROJECT_DIR:-.}"
fi

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
HAS_REMOTE=false
if [ -n "$BRANCH" ] && git rev-parse "origin/$BRANCH" &>/dev/null; then
    HAS_REMOTE=true
fi

# ─── Detect state (read-only) ───────────────────────────────────────────────
# .cache (tool caches, milestone tracker) is ALWAYS ignored here, whatever a repo's
# .gitignore says.
CACHE_RE='(^|[ /"])\.cache(/|"?$)'
# --untracked-files=all lists files individually (a collapsed 'sub/' would hide sub/.cache/ from the filter)
porcelain=$(git status --porcelain --untracked-files=all 2>/dev/null | grep -Ev "$CACHE_RE")
untracked=$(printf '%s\n' "$porcelain" | grep '^??' | cut -c4-)
unstaged=$(printf '%s\n' "$porcelain" | grep -E '^.[^ ?]' | cut -c4-)
staged=$(printf '%s\n' "$porcelain" | grep -E '^[^ ?]' | cut -c4-)

ENV_RE='(^|/)\.env(\.[^/]*)?$'
ENV_OK_RE='(^|/)\.env\.example$'   # explicitly allowed
# .env files that are tracked OR would be picked up by `git add -A`
env_files=$(git ls-files --cached --others --exclude-standard 2>/dev/null | grep -E "$ENV_RE" | grep -Ev "$ENV_OK_RE" | sort -u)

behind_count=0
ahead_count=0
env_unpushed=""
leaks_unpushed=""
if $HAS_REMOTE; then
    behind_count=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)
    ahead_count=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)
    [ "$ahead_count" -gt 0 ] && env_unpushed=$(git log "origin/$BRANCH..HEAD" --name-only --format= 2>/dev/null | grep -E "$ENV_RE" | grep -Ev "$ENV_OK_RE" | sort -u)
    # Credentials inside unpushed commits (content-based, only when gitleaks is installed).
    # Output is redacted.
    if [ "$ahead_count" -gt 0 ] && command -v gitleaks >/dev/null 2>&1; then
        leaks_unpushed=$(gitleaks git --log-opts="origin/$BRANCH..HEAD" --no-banner --redact -l error -f json -r - . 2>/dev/null \
            | jq -r '.[]? | "\(.RuleID) in \(.File):\(.StartLine) (commit \(.Commit[0:8]))"' 2>/dev/null | sort -u)
    fi
fi

# Clean, in sync, no .env problem → nothing to do.
[ -z "$untracked$unstaged$staged$env_files" ] && [ "$behind_count" -eq 0 ] && [ "$ahead_count" -eq 0 ] && exit 0

# ─── Report the state ───────────────────────────────────────────────────────
facts=""
# add_fact "headline" ["newline-separated items"]  (items capped at 10)
add_fact() {
    local total
    facts="${facts}- $1"$'\n'
    [ -z "$2" ] && return 0
    total=$(printf '%s\n' "$2" | wc -l | tr -d ' ')
    facts="${facts}$(printf '%s\n' "$2" | head -10 | sed 's/^/    /')"$'\n'
    [ "$total" -gt 10 ] && facts="${facts}    ... and $((total - 10)) more"$'\n'
    return 0
}

[ -n "$untracked" ] && add_fact "untracked files (not gitignored):" "$untracked"
[ -n "$unstaged" ]  && add_fact "unstaged changes:" "$unstaged"
[ -n "$staged" ]    && add_fact "staged but uncommitted changes:" "$staged"
[ "$ahead_count" -gt 0 ]  && add_fact "$ahead_count commit(s) not pushed to origin/$BRANCH"
[ "$behind_count" -gt 0 ] && add_fact "$behind_count commit(s) on origin/$BRANCH not merged locally"
[ -n "$env_files" ]    && add_fact ".env file(s) tracked or not gitignored:" "$env_files"
[ -n "$env_unpushed" ] && add_fact "unpushed commit(s) already contain .env file(s):" "$env_unpushed"
[ -n "$leaks_unpushed" ] && add_fact "gitleaks: potential secret(s) in unpushed commits (redacted):" "$leaks_unpushed"
$HAS_REMOTE || add_fact "no remote branch origin/${BRANCH:-<detached HEAD>}: nothing to push"

if $HAS_REMOTE; then
    goal="Goal: working tree clean and $BRANCH in sync with origin/$BRANCH."
else
    goal="Goal: working tree clean, everything committed locally (no remote branch exists: do not push or create one)."
fi

guardrails="- Integrate remote changes by merge only and resolve conflicts locally. Never rebase, stash, squash or otherwise rewrite history.
- Never force-push, in any form. If a push is rejected, merge and retry; if it is still rejected, stop and tell the user.
- Never add, commit or push .env files (.env.example is fine): gitignore or untrack them.
- Never bypass git hooks (no --no-verify): they are there for a reason. If a hook rejects the commit, fix what it reports and commit again; if you can't, stop and tell the user."
[ -n "$env_files$env_unpushed$leaks_unpushed" ] && guardrails="$guardrails
- Do not push while a .env file or a potential secret is involved (see above); don't rewrite history to fix it, tell the user which commits/files are affected."
guardrails="$guardrails
- On an 'index.lock' error another git process is running: wait a few seconds and retry; remove the lock only if no git process is running."

{
    echo ""
    echo "AUTO-COMMIT HOOK: the repository is not saved and in sync."
    echo ""
    echo "State:"
    printf '%s' "$facts"
    echo ""
    echo "$goal Version history is the top priority: snapshot work in progress now, without asking and without waiting for the task to finish. A broken or half-done WIP commit is fine (no tests/linters first). Conventional Commits message, Co-Authored-By trailer."
    echo ""
    echo "Guardrails:"
    echo "$guardrails"
} >&2
exit 2
