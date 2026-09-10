# shellcheck shell=bash
# shellcheck disable=SC2034
# Shared runtime helpers for setup, authentication, and native proxy startup.
# This file is sourced; callers own shell options, UI, and error handling.
#
# Sourced, never executed: no shebang, and no executable bit needed. The
# `shell=bash` directive above stands in for the shebang so shellcheck still
# knows the dialect (without it: SC2148, and bash-only constructs such as
# ${!key+x} below go unchecked).

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$COMMON_DIR/.." && pwd)"

ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE_FILE="$PROJECT_DIR/.env.example"
LITELLM_CONFIG_FILE="$PROJECT_DIR/litellm-config.yaml"
COPILOT_TOKEN_FILE="$HOME/.config/litellm/github_copilot/api-key.json"
CLAUDE_SETTINGS_DIR="$HOME/.claude"
CLAUDE_SETTINGS_FILE="${CLAUDE_SETTINGS_FILE:-$CLAUDE_SETTINGS_DIR/settings.json}"
CLAUDE_SETTINGS_DIR="$(dirname "$CLAUDE_SETTINGS_FILE")"
CODEX_CONFIG_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_CONFIG_FILE="$CODEX_CONFIG_DIR/config.toml"
CODEX_CATALOG_FILE="$PROJECT_DIR/codex/codex-model-catalog.json"
AUTH_SCRIPT="$PROJECT_DIR/scripts/authenticate.sh"
NATIVE_START_SCRIPT="$PROJECT_DIR/scripts/start-proxy.sh"
CONFIGURE_CLAUDE_SCRIPT="$PROJECT_DIR/scripts/configure-claude.py"
CONFIGURE_CODEX_SCRIPT="$PROJECT_DIR/scripts/configure-codex.py"
UPDATE_CATALOG_SCRIPT="$PROJECT_DIR/scripts/update-codex-catalog.py"
FETCH_MODELS_SCRIPT="$PROJECT_DIR/scripts/fetch-copilot-models.sh"

# Env var Codex reads the proxy key from; must match configure-codex.py.
API_KEY_VAR="LITELLM_API_KEY"

PYTHON_BIN="${PYTHON_BIN:-}"
PYTHON_VERSION_STRING=""
CHECK_MESSAGE=""

load_env() {
	if [ ! -f "$ENV_FILE" ]; then
		CHECK_MESSAGE="Environment file not found: $ENV_FILE"
		return 1
	fi

	while IFS= read -r line || [ -n "$line" ]; do
		line="${line%$'\r'}"
		case "$line" in
		'' | '#'*)
			continue
			;;
		*=*)
			key="${line%%=*}"
			value="${line#*=}"
			case "$key" in
			*[!A-Za-z0-9_]* | '')
				continue
				;;
			esac
			# An explicitly exported value wins over .env, matching how
			# docker compose already resolves these same variables.
			if [ -z "${!key+x}" ]; then
				export "$key=$value"
			fi
			;;
		esac
	done <"$ENV_FILE"
	CHECK_MESSAGE=""
	return 0
}

find_python() {
	if [ -n "${PYTHON_BIN:-}" ]; then
		if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
			PYTHON_BIN="$(command -v "$PYTHON_BIN")"
			return 0
		fi
		CHECK_MESSAGE="PYTHON_BIN='$PYTHON_BIN' is not executable"
		return 1
	fi

	if command -v python >/dev/null 2>&1; then
		PYTHON_BIN="$(command -v python)"
		return 0
	fi
	if command -v python3 >/dev/null 2>&1; then
		PYTHON_BIN="$(command -v python3)"
		return 0
	fi

	CHECK_MESSAGE="neither 'python' nor 'python3' is available in PATH"
	return 1
}

check_python() {
	if ! find_python; then
		return 1
	fi

	PYTHON_VERSION_STRING="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || {
		PYTHON_VERSION_STRING="unknown"
		CHECK_MESSAGE="$PYTHON_BIN could not be executed as Python"
		return 1
	}

	if "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) and sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1; then
		CHECK_MESSAGE="Python $PYTHON_VERSION_STRING found ($PYTHON_BIN)"
		return 0
	fi

	# The 3.11 floor is load-bearing twice over: LiteLLM needs it, and the
	# configure-*.py helpers rely on tomllib, which entered the stdlib in 3.11.
	# Do not relax it to an older release.
	CHECK_MESSAGE="Python 3.11-3.13 is required (found $PYTHON_VERSION_STRING at $PYTHON_BIN; overwrite with export PYTHON_BIN=/path/to/python3.11+)"
	return 1
}

check_uv() {
	if command -v uv >/dev/null 2>&1; then
		CHECK_MESSAGE="uv found ($(command -v uv))"
		return 0
	fi
	CHECK_MESSAGE="uv is not available in PATH"
	return 1
}

check_claude() {
	if command -v claude >/dev/null 2>&1; then
		CHECK_MESSAGE="claude found ($(command -v claude))"
		return 0
	fi
	CHECK_MESSAGE="claude is not available in PATH"
	return 1
}

check_codex() {
	if command -v codex >/dev/null 2>&1; then
		CHECK_MESSAGE="codex found ($(command -v codex))"
		return 0
	fi
	CHECK_MESSAGE="codex is not available in PATH"
	return 1
}

check_docker_compose() {
	if ! command -v docker >/dev/null 2>&1; then
		CHECK_MESSAGE="Docker is not available"
		return 1
	fi
	if ! docker compose version >/dev/null 2>&1; then
		CHECK_MESSAGE="the Docker Compose plugin is not working"
		return 1
	fi
	CHECK_MESSAGE="Docker Compose available (recommended full-stack runtime)"
	return 0
}

validate_proxy_env() {
	if [ -z "${LITELLM_PORT:-}" ]; then
		CHECK_MESSAGE="LITELLM_PORT is empty or unset (checked the environment, then $ENV_FILE)"
		return 1
	fi
	case "$LITELLM_PORT" in
	*[!0-9]*)
		CHECK_MESSAGE="LITELLM_PORT='$LITELLM_PORT' is not a valid port number"
		return 1
		;;
	esac
	if [ "$LITELLM_PORT" -lt 1 ] || [ "$LITELLM_PORT" -gt 65535 ]; then
		CHECK_MESSAGE="LITELLM_PORT='$LITELLM_PORT' is out of range (1-65535)"
		return 1
	fi
	if [ -z "${LITELLM_MASTER_KEY:-}" ]; then
		CHECK_MESSAGE="LITELLM_MASTER_KEY is empty or unset (checked the environment, then $ENV_FILE)"
		return 1
	fi
	CHECK_MESSAGE=""
	return 0
}

run_litellm() {
	exec env UV_NATIVE_TLS=true PYTHONNOUSERSITE=1 uv run \
		--isolated \
		--no-project \
		--python "$PYTHON_BIN" \
		--with "litellm[proxy]==1.99.0" \
		--with "aiodns>=4.0.4" \
		--with "pycares>=5.0.1" \
		--with "prisma" \
		litellm --config "$LITELLM_CONFIG_FILE" --port "$LITELLM_PORT"
}
