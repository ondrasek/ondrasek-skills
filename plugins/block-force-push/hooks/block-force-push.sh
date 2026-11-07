#!/usr/bin/env bash
# PreToolUse hook of the `block-force-push` plugin: block `git push --force` / `-f` / `--force-with-lease`.
# Reads the tool-call JSON on stdin, exits 2 (with stderr message) to deny.

set -u

cmd=$(jq -r '.tool_input.command // ""')

# Token-boundary class: characters that are NOT part of a normal shell token.
# Allows `;`, `)`, `&`, `|`, `<`, `>`, whitespace, etc. to terminate a match.
B='[^[:alnum:]_./=-]'

# Fast exit if this isn't a git push at all.
if ! grep -qE "(^|$B)git[[:space:]]+push($B|\$)" <<<"$cmd"; then
  exit 0
fi

# Match: --force, --force-with-lease[=...], or any short-option cluster containing f (-f, -fu, -uf).
if grep -qE "(^|$B)(--force(-with-lease)?(=[^[:space:]]*)?|-[a-zA-Z]*f[a-zA-Z]*)($B|\$)" <<<"$cmd"; then
  echo "Blocked: git force push detected (--force / -f / --force-with-lease). Force pushing rewrites remote history; ask the user before retrying without the force flag." >&2
  exit 2
fi

exit 0
