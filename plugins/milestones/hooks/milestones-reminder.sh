#!/usr/bin/env bash
# Milestones reminder (Stop): nudge Claude when the project's milestone tracker looks stale.
#
# Part of the `milestones` plugin (hooks/hooks.json registers it on the Stop event).
# DECLARATIVE: it reports what it found and the goal; Claude decides whether anything
# changed and how to update (the /milestones skill).
#
# Fires only when ALL hold (so chat-only turns and long milestones don't nag):
#   1. the project has .cache/milestones.json with at least one `doing` milestone
#   2. the tracker was last written >= STALE minutes ago (file mtime; milestones.py
#      replaces the file atomically on every write)
#   3. the repository changed AFTER that: uncommitted paths (outside .cache) or a commit
#      newer than the tracker
#   4. it has not reminded for this tracker in the last STALE minutes (marker file
#      .cache/.milestones-reminded; .cache is always gitignored)
#
# STALE = $MILESTONES_STALE_MINUTES (default 30). Once per turn: skipped when
# stop_hook_active. Read-only except for the marker. Exit 0 = nothing to say,
# exit 2 = blocking, report on stderr.

_in=$(cat 2>/dev/null || true)
[ "$(printf '%s' "$_in" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0

STALE_MIN=${MILESTONES_STALE_MINUTES:-30}
# The script of this plugin, found from this file (hooks/ sits next to skills/), so it works from
# the plugin cache, from a --plugin-dir checkout and in tests alike.
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || exit 0
SCRIPT="$HERE/../skills/milestones/scripts/milestones.py"
CACHE_RE='(^|[ /"])\.cache(/|"?$)'
[ -f "$SCRIPT" ] || exit 0

# Same project resolution as milestones.py: $CLAUDE_PROJECT_DIR, else the git toplevel.
root=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}
[ -n "$root" ] && cd "$root" 2>/dev/null || exit 0
file=${MILESTONES_FILE:-$root/.cache/milestones.json}
[ -f "$file" ] || exit 0

mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
fmt() { local s=$1; if [ "$s" -ge 3600 ]; then echo "$((s / 3600))h$(((s % 3600) / 60))m"; else echo "$((s / 60))m"; fi; }

data=$(python3 "$SCRIPT" show --json 2>/dev/null) || exit 0
doing=$(printf '%s' "$data" | jq -r '.items[]? | select(.state == "doing") | "#\(.id) \(.name) (doing for \((.duration_seconds // 0) / 60 | floor)m)"' 2>/dev/null)
[ -n "$doing" ] || exit 0

now=$(date +%s)
tracker_mtime=$(mtime "$file")
age=$((now - tracker_mtime))
[ "$age" -ge $((STALE_MIN * 60)) ] || exit 0

marker="$root/.cache/.milestones-reminded"
if [ -f "$marker" ]; then
    [ $((now - $(mtime "$marker"))) -ge $((STALE_MIN * 60)) ] || exit 0
fi

# Did the repository change since the tracker was last written?
activity=""
if git rev-parse --is-inside-work-tree &>/dev/null; then
    changed=$(git --no-optional-locks status --porcelain 2>/dev/null | grep -Ev "$CACHE_RE" | sed '/^$/d' | wc -l | tr -d ' ')
    last=$(git --no-optional-locks log -1 --format=%ct 2>/dev/null)
    [ "${changed:-0}" -gt 0 ] && activity="- $changed uncommitted path(s) in the repository"$'\n'
    [ "${last:-0}" -gt "$tracker_mtime" ] && activity="${activity}- last commit $(fmt $((now - last))) ago, after the tracker's last update"$'\n'
fi
[ -n "$activity" ] || exit 0

touch "$marker" 2>/dev/null

{
    echo ""
    echo "MILESTONES HOOK: the milestone tracker may be out of date."
    echo ""
    echo "State:"
    printf '%s\n' "$doing" | sed 's/^/- doing: /'
    echo "- tracker last updated $(fmt "$age") ago ($file)"
    printf '%s' "$activity"
    echo ""
    echo "Goal: the statusline reflects reality. If a milestone finished, got blocked, needs the user, or a new one started, update the tracker now with the milestones skill (/milestones). If nothing changed, do nothing: this reminder repeats at most every $STALE_MIN minutes."
    echo ""
    echo "Guardrails: update silently, no announcement. The tracker file is never added, force-added or committed (.cache is gitignored)."
} >&2
exit 2
