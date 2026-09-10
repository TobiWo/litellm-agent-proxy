#!/usr/bin/env bash
# Set or remove Claude Code's global context-window override.
#
# Thin wrapper: scripts/configure-claude.py owns every write to
# ~/.claude/settings.json, so the JSON handling lives in exactly one place.
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
-h | --help)
	usage
	exit 0
	;;
delete) ;;
*)
	if [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
		printf 'Error: use a positive integer token count (e.g. 272000), without suffixes or leading zeros.\n' >&2
		exit 1
	fi
	;;
esac

if ! check_python; then
	printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
	exit 1
fi
if [ ! -f "$CONFIGURE_CLAUDE_SCRIPT" ]; then
	printf 'Error: %s is missing.\n' "$CONFIGURE_CLAUDE_SCRIPT" >&2
	exit 1
fi

if [ "$1" = delete ]; then
	exec "$PYTHON_BIN" "$CONFIGURE_CLAUDE_SCRIPT" \
		--settings "$CLAUDE_SETTINGS_FILE" --delete-max-context
fi
exec "$PYTHON_BIN" "$CONFIGURE_CLAUDE_SCRIPT" \
	--settings "$CLAUDE_SETTINGS_FILE" --max-context "$1"
