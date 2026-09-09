#!/usr/bin/env bash
# Start the reduced native LiteLLM-only runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

usage() {
    printf '%s\n' "Usage: $(basename "$0")" >&2
    printf '%s\n' "Start LiteLLM natively without PostgreSQL or Docker Compose services." >&2
}

case "${1:-}" in
    '')
        ;;
    -h|--help)
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
    printf '%s\n' "Error: this command does not accept arguments." >&2
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

if [ ! -f "$COPILOT_TOKEN_FILE" ]; then
    printf '%s\n' "No GitHub Copilot authentication found." >&2
    printf '%s\n' "Run ./scripts/authenticate.sh first." >&2
    exit 1
fi

printf 'Starting native LiteLLM proxy on port %s …\n' "$LITELLM_PORT"
printf '%s\n' "This fallback starts LiteLLM only; PostgreSQL and other Compose services are not included."
printf '\n%s\n' "WARNING: the PII guardrail requires the Docker Compose stack."
printf '%s\n' "  PRESIDIO_ANALYZER_API_BASE / PRESIDIO_ANONYMIZER_API_BASE point at Compose"
printf '%s\n' "  service DNS names that do not resolve here, and litellm_config.yaml sets"
printf '%s\n' "  default_on: true — so every request will fail until you either set"
printf '%s\n\n' "  default_on: false in litellm_config.yaml or make Presidio reachable on localhost."

# The subshell discards fd 3 on exit, so there is nothing to close here.
if (exec 3<>/dev/tcp/127.0.0.1/"$LITELLM_PORT") 2>/dev/null; then
    printf 'WARNING: port %s is already in use.\n' "$LITELLM_PORT" >&2
    printf '%s\n' "  LiteLLM binds a random port instead of failing when the port is 4000," >&2
    printf '%s\n' "  so it may appear to start while being unreachable. Claude Code is" >&2
    printf '%s\n\n' "  configured for $LITELLM_PORT and will not connect. Run 'docker compose down' first." >&2
fi

run_litellm
