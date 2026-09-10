#!/usr/bin/env bash
# Regenerate codex/codex-model-catalog.json for the installed Codex version.
#
# Fetches the GitHub Copilot catalog (for real token limits), reads the models
# Codex bundles (for the current schema), and rebuilds the vendored catalog
# from the intersection with the Responses-API aliases in the LiteLLM config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

usage() {
    printf '%s\n' "Usage: $(basename "$0") [--output PATH] [--yes]"
    printf '%s\n' "Regenerate the Codex model catalog for the installed Codex version."
    printf '%s\n' ""
    printf '%s\n' "  --output PATH  Write there instead of the vendored catalog (for review)."
    printf '%s\n' "  --yes          Skip the confirmation prompt."
}

OUTPUT=""
ASSUME_YES=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            if [ "$#" -lt 2 ] || [ -z "$2" ]; then
                printf '%s\n' "Error: --output requires a path." >&2
                exit 1
            fi
            OUTPUT="$2"
            shift 2
            ;;
        --output=*)
            OUTPUT="${1#--output=}"
            shift
            ;;
        --yes)
            ASSUME_YES="--yes"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf "Error: unknown argument '%s'\n" "$1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! check_python; then
    printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
    exit 1
fi
if ! check_codex; then
    printf 'Error: %s\n' "$CHECK_MESSAGE" >&2
    exit 1
fi
if [ ! -f "$UPDATE_CATALOG_SCRIPT" ]; then
    printf 'Error: %s is missing.\n' "$UPDATE_CATALOG_SCRIPT" >&2
    exit 1
fi
if [ ! -x "$FETCH_MODELS_SCRIPT" ]; then
    printf 'Error: %s is missing or not executable.\n' "$FETCH_MODELS_SCRIPT" >&2
    exit 1
fi

COPILOT_MODELS=""
cleanup() {
    if [ -n "$COPILOT_MODELS" ] && [ -f "$COPILOT_MODELS" ]; then
        rm -f "$COPILOT_MODELS"
    fi
}
on_signal() {
    trap - EXIT INT TERM
    cleanup
    exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

COPILOT_MODELS="$(mktemp "${TMPDIR:-/tmp}/copilot-models.XXXXXX")"

printf '%s\n' "Fetching the GitHub Copilot model catalog …" >&2
if ! "$FETCH_MODELS_SCRIPT" --output "$COPILOT_MODELS"; then
    printf '%s\n' "Error: could not fetch the GitHub Copilot model catalog." >&2
    exit 1
fi

set -- --litellm-config "$LITELLM_CONFIG_FILE" \
       --copilot-models "$COPILOT_MODELS" \
       --catalog "$CODEX_CATALOG_FILE"
if [ -n "$OUTPUT" ]; then
    set -- "$@" --output "$OUTPUT"
fi
if [ -n "$ASSUME_YES" ]; then
    set -- "$@" "$ASSUME_YES"
fi

"$PYTHON_BIN" "$UPDATE_CATALOG_SCRIPT" "$@"
