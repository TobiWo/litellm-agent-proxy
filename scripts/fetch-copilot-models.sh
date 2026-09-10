#!/usr/bin/env bash
# Fetch the model catalog available to the authenticated Copilot subscription.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

usage() {
	printf '%s\n' "Usage: $(basename "$0") [--output PATH]" >&2
	printf '%s\n' "Fetch the GitHub Copilot model catalog." >&2
	printf '%s\n' "" >&2
	printf '%s\n' "  --output PATH  Write the response to PATH instead of stdout." >&2
}

OUTPUT_FILE=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--output)
		if [ "$#" -lt 2 ] || [ -z "$2" ]; then
			printf '%s\n' "Error: --output requires a path." >&2
			usage
			exit 1
		fi
		case "$2" in
		-*)
			printf '%s\n' "Error: --output requires a path, not another option." >&2
			usage
			exit 1
			;;
		esac
		OUTPUT_FILE="$2"
		shift 2
		;;
	--output=*)
		OUTPUT_FILE="${1#--output=}"
		if [ -z "$OUTPUT_FILE" ]; then
			printf '%s\n' "Error: --output requires a path." >&2
			usage
			exit 1
		fi
		shift
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
	-* | *)
		printf "Error: unknown argument '%s'\n" "$1" >&2
		usage
		exit 1
		;;
	esac
done

if ! command -v curl >/dev/null 2>&1; then
	printf '%s\n' "Error: 'curl' is not available in PATH." >&2
	exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
	printf '%s\n' "Error: 'jq' is not available in PATH." >&2
	printf '%s\n' "Install jq to read the Copilot authentication file." >&2
	exit 1
fi
if [ ! -x "$AUTH_SCRIPT" ]; then
	printf 'Error: %s is missing or not executable.\n' "$AUTH_SCRIPT" >&2
	exit 1
fi

RESPONSE_FILE=""
cleanup() {
	if [ -n "$RESPONSE_FILE" ] && [ -f "$RESPONSE_FILE" ]; then
		rm -f "$RESPONSE_FILE"
	fi
}
on_signal() {
	trap - EXIT INT TERM
	cleanup
	exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

if [ -n "$OUTPUT_FILE" ]; then
	if [ -d "$OUTPUT_FILE" ]; then
		printf 'Error: output path is a directory: %s\n' "$OUTPUT_FILE" >&2
		exit 1
	fi
	OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"
	if [ ! -d "$OUTPUT_DIR" ]; then
		printf 'Error: output directory does not exist: %s\n' "$OUTPUT_DIR" >&2
		exit 1
	fi
	RESPONSE_FILE="$(mktemp "$OUTPUT_DIR/.fetch-copilot-models.XXXXXX")"
else
	RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/fetch-copilot-models.XXXXXX")"
fi

ensure_authentication() {
	if [ ! -f "$COPILOT_TOKEN_FILE" ]; then
		printf '%s\n' "No GitHub Copilot API token found; starting authentication …" >&2
		"$AUTH_SCRIPT" >&2
	fi
	if [ ! -f "$COPILOT_TOKEN_FILE" ]; then
		printf 'Error: authentication did not create %s.\n' "$COPILOT_TOKEN_FILE" >&2
		return 1
	fi
}

load_credentials() {
	if ! COPILOT_TOKEN="$(jq -er '.token | select(type == "string" and length > 0)' "$COPILOT_TOKEN_FILE" 2>/dev/null)"; then
		printf 'Error: %s is malformed or has no usable token.\n' "$COPILOT_TOKEN_FILE" >&2
		return 1
	fi
	if ! COPILOT_API_ENDPOINT="$(jq -er '.endpoints.api | select(type == "string" and length > 0)' "$COPILOT_TOKEN_FILE" 2>/dev/null)"; then
		printf 'Error: %s has no usable Copilot API endpoint.\n' "$COPILOT_TOKEN_FILE" >&2
		return 1
	fi
	COPILOT_API_ENDPOINT="${COPILOT_API_ENDPOINT%/}"
}

fetch_models() {
	if ! HTTP_STATUS="$(
		printf 'Authorization: Bearer %s\n' "$COPILOT_TOKEN" |
			curl \
				--disable \
				--silent \
				--show-error \
				--output "$RESPONSE_FILE" \
				--write-out '%{http_code}' \
				--header @- \
				--header 'Copilot-Integration-Id: vscode-chat' \
				--header 'Editor-Version: vscode/1.85.1' \
				"$COPILOT_API_ENDPOINT/models"
	)"; then
		printf '%s\n' "Error: failed to reach the GitHub Copilot model catalog." >&2
		return 1
	fi
	return 0
}

ensure_authentication
load_credentials

REFRESHED=0
while :; do
	fetch_models || exit 1
	case "$HTTP_STATUS" in
	2??)
		break
		;;
	401)
		if [ "$REFRESHED" -eq 1 ]; then
			printf '%s\n' "Error: GitHub Copilot rejected the refreshed API token (HTTP 401)." >&2
			exit 1
		fi
		printf '%s\n' "GitHub Copilot API token was rejected; refreshing authentication once …" >&2
		"$AUTH_SCRIPT" --refresh >&2
		load_credentials
		REFRESHED=1
		;;
	*)
		printf 'Error: GitHub Copilot model catalog returned HTTP %s.\n' "$HTTP_STATUS" >&2
		exit 1
		;;
	esac
done

if [ -n "$OUTPUT_FILE" ]; then
	mv -f "$RESPONSE_FILE" "$OUTPUT_FILE"
	RESPONSE_FILE=""
	printf 'Wrote GitHub Copilot model catalog to %s\n' "$OUTPUT_FILE" >&2
else
	cat "$RESPONSE_FILE"
fi
