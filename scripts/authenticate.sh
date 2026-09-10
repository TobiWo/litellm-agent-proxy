#!/usr/bin/env bash
# Perform GitHub Copilot OAuth device authentication through LiteLLM.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

usage() {
	printf '%s\n' "Usage: $(basename "$0") [--refresh]" >&2
	printf '%s\n' "Perform GitHub Copilot device authentication, then exit." >&2
	printf '%s\n' "" >&2
	printf '%s\n' "  --refresh  Refresh the cached Copilot API token. This may reuse the" >&2
	printf '%s\n' "             stored OAuth credential or show the browser device flow." >&2
}

REFRESH=0
case "${1:-}" in
'') ;;
--refresh)
	REFRESH=1
	;;
-h | --help)
	if [ "$#" -ne 1 ]; then
		printf '%s\n' "Error: help cannot be combined with other arguments." >&2
		usage
		exit 1
	fi
	usage
	exit 0
	;;
*)
	printf "Error: unknown argument '%s'\n" "$1" >&2
	usage
	exit 1
	;;
esac
if [ "$#" -gt 1 ]; then
	printf '%s\n' "Error: this command accepts at most one argument." >&2
	usage
	exit 1
fi

if ! load_env; then
	printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
	exit 1
fi
if ! check_uv; then
	printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
	printf '%s\n' "Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
	exit 1
fi
if ! check_python; then
	printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
	exit 1
fi
if ! validate_proxy_env; then
	printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
	exit 1
fi

if [ "$REFRESH" -eq 1 ]; then
	if [ -f "$COPILOT_TOKEN_FILE" ]; then
		printf '%s\n' "Refreshing the cached GitHub Copilot API token …"
		rm -f "$COPILOT_TOKEN_FILE"
	fi
elif [ -f "$COPILOT_TOKEN_FILE" ]; then
	printf 'GitHub Copilot authentication already present at %s\n' "$COPILOT_TOKEN_FILE"
	exit 0
fi

LITELLM_PID=""
CLEANED_UP=0

cleanup() {
	if [ "$CLEANED_UP" -eq 1 ]; then
		return
	fi
	CLEANED_UP=1
	if [ -n "$LITELLM_PID" ]; then
		kill "$LITELLM_PID" 2>/dev/null || true
		wait "$LITELLM_PID" 2>/dev/null || true
		LITELLM_PID=""
	fi
}

on_signal() {
	trap - EXIT INT TERM
	cleanup
	exit 130
}

trap cleanup EXIT
trap on_signal INT TERM

printf '%s\n\n' "Starting LiteLLM for GitHub Copilot authentication …"
(
	run_litellm
) &
LITELLM_PID=$!

printf '%s\n' "Waiting for GitHub Copilot authentication to complete …"
while [ ! -f "$COPILOT_TOKEN_FILE" ]; do
	if ! kill -0 "$LITELLM_PID" 2>/dev/null; then
		wait "$LITELLM_PID" 2>/dev/null || true
		LITELLM_PID=""
		printf '%s\n' "Error: LiteLLM exited before authentication completed." >&2
		exit 1
	fi
	sleep 1
done

printf '%s\n' "GitHub Copilot authentication complete."
cleanup
trap - EXIT INT TERM
