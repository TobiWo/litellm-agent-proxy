#!/usr/bin/env bash
# Set or remove Claude Code's global context-window override.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

usage() {
    printf 'Usage: %s <positive-token-count|delete|--help>\n' "$0"
}

if [ "$#" -ne 1 ]; then
    usage >&2
    exit 1
fi
case "$1" in
    -h|--help) usage; exit 0 ;;
    delete) ;;
    *)
        if [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
            printf 'Error: use a positive integer token count (e.g. 272000), without suffixes or leading zeros.\n' >&2
            exit 1
        fi
        ;;
esac

if ! command -v jq >/dev/null 2>&1; then
    printf 'Error: jq is required.\n' >&2
    exit 1
fi
if [ -L "$CLAUDE_SETTINGS_FILE" ]; then
    printf 'Error: refusing to replace symlink: %s\n' "$CLAUDE_SETTINGS_FILE" >&2
    exit 1
fi

input="$CLAUDE_SETTINGS_FILE"
new=false
if [ ! -e "$input" ]; then
    if [ "$1" = delete ]; then
        printf 'Context override is already absent.\n'
        exit 0
    fi
    mkdir -p "$CLAUDE_SETTINGS_DIR"
    input=/dev/null
    new=true
fi

tmp="$(mktemp "$CLAUDE_SETTINGS_DIR/.settings.json.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
chmod 600 "$tmp"
# ponytail: atomic replacement, not concurrent editing; add conflict detection if needed.
jq --slurp --arg value "$1" --argjson new "$new" '
    if $new then {}
    elif length != 1 then error("settings must contain exactly one JSON object")
    else .[0] end
    | if type != "object" then error("settings must be a JSON object")
      elif has("env") and (.env | type != "object") then error("env must be a JSON object")
      else . end
    | if $value == "delete" then
        if (.env // {} | has("CLAUDE_CODE_MAX_CONTEXT_TOKENS")) then
            del(.env.CLAUDE_CODE_MAX_CONTEXT_TOKENS)
        else empty end
      else .env.CLAUDE_CODE_MAX_CONTEXT_TOKENS = $value end
' "$input" > "$tmp"

if [ -s "$tmp" ]; then
    mv -f "$tmp" "$CLAUDE_SETTINGS_FILE"
fi
if [ "$1" = delete ]; then
    printf 'Removed context override. Restart Claude Code to clear the running value.\n'
else
    printf 'Set CLAUDE_CODE_MAX_CONTEXT_TOKENS=%s in %s\n' "$1" "$CLAUDE_SETTINGS_FILE"
fi
