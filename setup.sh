#!/usr/bin/env bash
# One-command setup: verify prerequisites, create .env, authenticate with
# GitHub Copilot, and configure the selected coding agent to use the local proxy.
#
# Written for Bash 3.2 (macOS default) as well as Linux and WSL2: no
# associative arrays, no `readlink -f`/`realpath`, no `sed -i`, no other
# GNU-only or Bash-4+ constructs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/scripts/common.sh"

# ------------------------------------------------------------------------
# Restrained terminal UI
#
# Colors only when stdout is an interactive terminal and the environment
# does not opt out (NO_COLOR, TERM=dumb). No spinners, no cursor
# repositioning, no external UI dependency: this must stay robust on
# macOS Bash 3.2, Linux, WSL2, CI logs, and screen readers.
# ------------------------------------------------------------------------

USE_COLOR=0
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
	USE_COLOR=1
fi

if [ "$USE_COLOR" -eq 1 ]; then
	BOLD="$(printf '\033[1m')"
	DIM="$(printf '\033[2m')"
	RED="$(printf '\033[31m')"
	GREEN="$(printf '\033[32m')"
	YELLOW="$(printf '\033[33m')"
	RESET="$(printf '\033[0m')"
else
	BOLD=""
	DIM=""
	RED=""
	GREEN=""
	YELLOW=""
	RESET=""
fi

heading() {
	printf '%s\n' "${BOLD}LiteLLM Agent Proxy — Setup${RESET}"
}

step() {
	# $1: step label
	printf '\n%s\n' "${BOLD}==> $1${RESET}"
}

info() {
	# $1: primary message
	printf '%s\n' "  $1"
}

detail() {
	# $1: secondary/explanatory message, printed dim
	printf '%s\n' "  ${DIM}$1${RESET}"
}

ok() {
	printf '%s\n' "  ${GREEN}[ok]${RESET} $1"
}

warn() {
	printf '%s\n' "  ${YELLOW}[warn]${RESET} $1"
}

fail_line() {
	printf '%s\n' "  ${RED}[fail]${RESET} $1" >&2
}

usage() {
	printf '%s\n' "Usage: $(basename "$0") [--agent claude|codex|both]"
	printf '%s\n' "Configure this machine to route a coding agent through the local LiteLLM proxy."
	printf '%s\n' ""
	printf '%s\n' "  --agent AGENT  Skip the interactive prompt and configure AGENT."
}

# ------------------------------------------------------------------------
# 0. Agent selection — decides which prerequisites are required, so it runs
#    before the prerequisite gate.
# ------------------------------------------------------------------------

AGENT=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--agent)
		if [ "$#" -lt 2 ]; then
			printf '%s\n' "Error: --agent requires a value." >&2
			usage >&2
			exit 1
		fi
		AGENT="$2"
		shift 2
		;;
	--agent=*)
		AGENT="${1#--agent=}"
		shift
		;;
	-h | --help)
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

heading

if [ -n "$AGENT" ]; then
	case "$AGENT" in
	claude | codex | both) ;;
	*)
		printf '%s\n' "Error: --agent must be one of: claude, codex, both" >&2
		exit 1
		;;
	esac
else
	step "Choosing an agent"
	info "Which agent should this proxy be configured for?"
	printf '\n'
	printf '  %s\n' "1) Claude Code"
	printf '  %s\n' "2) Codex"
	printf '  %s\n' "3) Both"
	printf '\n'
	printf '%s' "  Selection [1/2/3]: "
	REPLY=""
	read -r REPLY || true
	case "$REPLY" in
	1) AGENT="claude" ;;
	2) AGENT="codex" ;;
	3) AGENT="both" ;;
	*)
		printf '\n'
		fail_line "Invalid selection. Re-run and choose 1, 2, or 3."
		exit 1
		;;
	esac
fi

SETUP_CLAUDE=0
SETUP_CODEX=0
case "$AGENT" in
claude) SETUP_CLAUDE=1 ;;
codex) SETUP_CODEX=1 ;;
both)
	SETUP_CLAUDE=1
	SETUP_CODEX=1
	;;
esac

# ------------------------------------------------------------------------
# 1. Prerequisite checks — aggregate every problem into one actionable
#    failure instead of stopping at the first missing tool.
# ------------------------------------------------------------------------

step "Checking prerequisites"

PREREQ_ERRORS=""

add_error() {
	# $1: message to append to the aggregated error list
	if [ -z "$PREREQ_ERRORS" ]; then
		PREREQ_ERRORS="$1"
	else
		PREREQ_ERRORS="$PREREQ_ERRORS
$1"
	fi
}

if check_uv; then
	ok "$CHECK_MESSAGE"
else
	fail_line "$CHECK_MESSAGE"
	add_error "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
fi

if check_python; then
	ok "$CHECK_MESSAGE"
else
	fail_line "$CHECK_MESSAGE"
	add_error "$CHECK_MESSAGE. Set PYTHON_BIN to override (Using venv or conda for virtual environments is recommended)."
fi

DOCKER_COMPOSE_AVAILABLE=0
if check_docker_compose; then
	DOCKER_COMPOSE_AVAILABLE=1
	ok "$CHECK_MESSAGE"
else
	warn "$CHECK_MESSAGE"
	detail "Setup can continue with the reduced native LiteLLM-only fallback."
fi

if [ "$SETUP_CLAUDE" -eq 1 ]; then
	if check_claude; then
		ok "$CHECK_MESSAGE"
	else
		fail_line "$CHECK_MESSAGE"
		add_error "Install Claude Code: https://docs.claude.com/en/docs/claude-code/setup"
	fi
fi

if [ "$SETUP_CODEX" -eq 1 ]; then
	if check_codex; then
		ok "$CHECK_MESSAGE"
	else
		fail_line "$CHECK_MESSAGE"
		add_error "Install Codex CLI: https://developers.openai.com/codex"
	fi
fi

if [ -n "$PREREQ_ERRORS" ]; then
	printf '\n%s\n' "${BOLD}${RED}Setup cannot continue — missing or invalid prerequisites:${RESET}"
	printf '%s\n' "$PREREQ_ERRORS" | while IFS= read -r line; do
		printf '  - %s\n' "$line"
	done
	printf '\n%s\n' "Note: a GitHub Copilot subscription (personal or enterprise) is also required for authentication, but this cannot be checked locally."
	exit 1
fi

detail ""
detail "A GitHub Copilot subscription (personal or enterprise) is required for authentication; this cannot be checked locally."

# ------------------------------------------------------------------------
# 2. Create .env from the template, never overwriting an existing file.
# ------------------------------------------------------------------------

step "Configuring .env"

if [ -f "$ENV_FILE" ]; then
	ok ".env already exists — leaving it untouched"
else
	if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
		fail_line ".env.example not found at $ENV_EXAMPLE_FILE"
		exit 1
	fi
	cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
	ok "Created .env from .env.example"
	detail "Edit .env now if you want to change the port, master key, or credentials."
fi

if ! load_env; then
	fail_line "$CHECK_MESSAGE"
	exit 1
fi

if ! validate_proxy_env; then
	fail_line "$CHECK_MESSAGE"
	exit 1
fi

ok "LITELLM_PORT=$LITELLM_PORT"
ok "LITELLM_MASTER_KEY is set"

# ------------------------------------------------------------------------
# 3. GitHub Copilot authentication (idempotent).
# ------------------------------------------------------------------------

step "GitHub Copilot authentication"

if [ -f "$COPILOT_TOKEN_FILE" ]; then
	ok "Authentication already present at $COPILOT_TOKEN_FILE"
else
	info "No GitHub Copilot token found."
	detail "This uses GitHub's OAuth device flow: the proxy will print a URL and a"
	detail "one-time code. Open the URL, enter the code, and approve access."
	detail "The token is then stored locally and refreshes on its own."
	printf '\n'
	if [ ! -x "$AUTH_SCRIPT" ]; then
		fail_line "$AUTH_SCRIPT is missing or not executable"
		exit 1
	fi
	"$AUTH_SCRIPT"
	if [ ! -f "$COPILOT_TOKEN_FILE" ]; then
		fail_line "Authentication did not complete (token not found at $COPILOT_TOKEN_FILE)"
		exit 1
	fi
	ok "GitHub Copilot authentication complete"
fi

# ------------------------------------------------------------------------
# 4. Agent configuration. Each helper owns its own file format, shows the
#    pending change, and asks for confirmation before writing.
# ------------------------------------------------------------------------

PROXY_BASE_URL="http://localhost:$LITELLM_PORT"
CODEX_CONFIGURED=0

if [ "$SETUP_CLAUDE" -eq 1 ]; then
	step "Configuring Claude Code settings"

	detail "Claude Code needs the base URL and token to reach the local proxy."
	detail "Without these settings, the proxy workflow will not work."
	printf '\n'
	detail "Two telemetry opt-outs are set alongside them:"
	detail "  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 — no non-essential traffic"
	detail "  DISABLE_GROWTHBOOK=1                       — no feature-flag/analytics calls"
	printf '\n'

	if [ ! -f "$CONFIGURE_CLAUDE_SCRIPT" ]; then
		fail_line "$CONFIGURE_CLAUDE_SCRIPT is missing"
		exit 1
	fi

	if ! "$PYTHON_BIN" "$CONFIGURE_CLAUDE_SCRIPT" \
		--settings "$CLAUDE_SETTINGS_FILE" \
		--base-url "$PROXY_BASE_URL" \
		--token "$LITELLM_MASTER_KEY"; then
		warn "Claude Code settings were not altered."
		detail "The proxy workflow will not work unless these settings are already configured."
	fi
fi

if [ "$SETUP_CODEX" -eq 1 ]; then
	step "Configuring Codex"

	detail "Codex reaches the proxy through an OpenAI-compatible provider entry."
	detail "Only Responses-API models are usable (GPT models)."
	detail "Code mode (the JS exec tool) is disabled: GitHub Copilot's /responses"
	detail "backend does not answer it correctly, which aborts every command."
	printf '\n'

	if [ ! -f "$CONFIGURE_CODEX_SCRIPT" ]; then
		fail_line "$CONFIGURE_CODEX_SCRIPT is missing"
		exit 1
	fi
	if [ ! -f "$CODEX_CATALOG_FILE" ]; then
		fail_line "Model catalog not found at $CODEX_CATALOG_FILE"
		exit 1
	fi

	if "$PYTHON_BIN" "$CONFIGURE_CODEX_SCRIPT" \
		--config "$CODEX_CONFIG_FILE" \
		--base-url "$PROXY_BASE_URL/v1" \
		--catalog "$CODEX_CATALOG_FILE" \
		--litellm-config "$LITELLM_CONFIG_FILE"; then
		CODEX_CONFIGURED=1
	else
		warn "Codex settings were not altered."
		detail "The proxy workflow will not work unless these settings are already configured."
	fi
fi

# ------------------------------------------------------------------------
# 5. Shell export for Codex.
#
# The line dereferences .env at shell startup rather than embedding the key,
# so the credential stays in exactly one place and rotating it needs no
# re-run. Only ever appended: existing lines are reported, never edited.
# ------------------------------------------------------------------------

# The written line and the matched line must be byte-identical, so both come
# from here. If they ever drift, every run reports a mismatch and appends again.
API_KEY_EXPORT_LINE="export $API_KEY_VAR=\"\$(grep -m1 '^LITELLM_MASTER_KEY=' '$ENV_FILE' | cut -d= -f2-)\""
API_KEY_GUARD_LINE="[ -n \"\${$API_KEY_VAR:-}\" ] || printf '%s\\n' \"warning: $API_KEY_VAR is empty; is '$ENV_FILE' still there?\" >&2"

write_api_key_export() {
	# $1: rc file to append to
	{
		printf '\n%s\n' "# LiteLLM agent proxy — reads the master key from the repo's .env"
		printf '%s\n' "$API_KEY_EXPORT_LINE"
		printf '%s\n' "$API_KEY_GUARD_LINE"
	} >>"$1"
	ok "Appended the $API_KEY_VAR export to $1"
}

append_api_key_export() {
	# $1: rc file to append to
	rc_file="$1"

	# Already exactly right — including pointing at *this* repo's .env.
	if [ -e "$rc_file" ] && grep -qF -- "$API_KEY_EXPORT_LINE" "$rc_file" 2>/dev/null; then
		ok "$rc_file already exports $API_KEY_VAR from this repo's .env"
		return 0
	fi

	# An active export exists but differs: a hardcoded key, or a line pointing at
	# a different checkout. The latter silently exports an empty value, so it
	# must not pass unnoticed.
	if [ -e "$rc_file" ] &&
		grep -qE "^[[:space:]]*export[[:space:]]+$API_KEY_VAR=" "$rc_file" 2>/dev/null; then
		warn "$rc_file already sets $API_KEY_VAR, but not from this repo's .env:"
		printf '\n'
		# Mask anything that looks like an inline credential before echoing it.
		grep -nE "^[[:space:]]*export[[:space:]]+$API_KEY_VAR=" "$rc_file" |
			sed -E 's/(sk-|gh[pousr]_)[A-Za-z0-9_-]*/\1<masked>/g' |
			while IFS= read -r offending; do
				printf '  %s\n' "$offending"
			done
		printf '\n'
		detail "Appending the correct line below it would make the new one win, but"
		detail "the old entry stays and should be deleted by hand."
		printf '\n'
		printf '%s' "  Append the correct export anyway? [y/N] "
		OVERWRITE_REPLY=""
		read -r OVERWRITE_REPLY || true
		case "$OVERWRITE_REPLY" in
		[yY] | [yY][eE][sS])
			write_api_key_export "$rc_file"
			;;
		*)
			warn "Left $rc_file unchanged. Codex will use the existing value."
			;;
		esac
		return 0
	fi

	write_api_key_export "$rc_file"
}

if [ "$SETUP_CODEX" -eq 1 ] && [ "$CODEX_CONFIGURED" -eq 1 ]; then
	step "Exporting $API_KEY_VAR for Codex"

	info "Codex reads the proxy key from the $API_KEY_VAR environment variable."
	detail "The export reads it from .env at shell startup, so the secret stays in"
	detail "one file and rotating it needs no re-run of this script. If the export"
	detail "is already in place, nothing is changed."
	printf '\n'

	BASHRC="$HOME/.bashrc"
	ZSHRC="$HOME/.zshrc"
	DEFAULT_RC="1"
	case "${SHELL:-}" in
	*zsh) DEFAULT_RC="2" ;;
	esac

	if [ ! -e "$BASHRC" ] && [ ! -e "$ZSHRC" ]; then
		warn "Neither ~/.bashrc nor ~/.zshrc exists; add this line by hand:"
		printf '\n'
		printf '  %s\n' "$API_KEY_EXPORT_LINE"
		printf '\n'
		detail "For fish, use: set -gx $API_KEY_VAR (grep -m1 '^LITELLM_MASTER_KEY=' '$ENV_FILE' | cut -d= -f2-)"
	else
		info "Where should the export be added?"
		printf '\n'
		printf '  %s\n' "1) ~/.bashrc"
		printf '  %s\n' "2) ~/.zshrc"
		printf '  %s\n' "3) both"
		printf '  %s\n' "4) skip"
		printf '\n'
		printf '%s' "  Selection [$DEFAULT_RC]: "
		RC_REPLY=""
		read -r RC_REPLY || true
		[ -z "$RC_REPLY" ] && RC_REPLY="$DEFAULT_RC"
		case "$RC_REPLY" in
		1) append_api_key_export "$BASHRC" ;;
		2) append_api_key_export "$ZSHRC" ;;
		3)
			append_api_key_export "$BASHRC"
			append_api_key_export "$ZSHRC"
			;;
		*)
			warn "Skipped. Codex will not authenticate until $API_KEY_VAR is exported."
			;;
		esac
	fi
fi

# ------------------------------------------------------------------------
# 6. List available models.
# ------------------------------------------------------------------------

step "Available models"

if [ -f "$LITELLM_CONFIG_FILE" ]; then
	detail "Defined in litellm-config.yaml — use with --model exactly as shown:"
	printf '\n'
	while IFS= read -r model_name; do
		printf '  - %s\n' "$model_name"
	done <<EOF
$(grep -E '^[[:space:]]*-[[:space:]]*model_name:' "$LITELLM_CONFIG_FILE" | sed -E 's/^[[:space:]]*-[[:space:]]*model_name:[[:space:]]*//; s/\*+$//')
EOF
	if [ "$SETUP_CODEX" -eq 1 ]; then
		printf '\n'
		detail "Codex can only use the Responses-API models (the gpt-* entries above)."
	fi
else
	warn "litellm-config.yaml not found; skipping model list"
fi

# ------------------------------------------------------------------------
# 7. Final summary.
# ------------------------------------------------------------------------

printf '\n%s\n' "${BOLD}Setup complete${RESET}"
printf '%s\n' "----------------------------------------"

printf '\n'
printf '  %s\n' "${BOLD}Edit .env now if you want to change the port, master key, or credentials.${RESET}"
printf '\n'

if [ "$DOCKER_COMPOSE_AVAILABLE" -eq 1 ]; then
	info "Start the proxy (recommended, full stack):"
	printf '  %s\n' "${BOLD}docker compose up -d${RESET}"
	detail "Includes PostgreSQL, persistence, restart policy, virtual keys, and spend tracking."
	printf '\n'
	info "Native fallback (LiteLLM only):"
	printf '  %s\n' "${BOLD}./scripts/start-proxy.sh${RESET}"
else
	warn "The recommended Docker Compose full-stack runtime is unavailable."
	info "Start the reduced native fallback (LiteLLM only):"
	printf '  %s\n' "${BOLD}./scripts/start-proxy.sh${RESET}"
fi

if [ "$SETUP_CLAUDE" -eq 1 ]; then
	printf '\n'
	info "Start Claude Code:"
	printf '  %s\n' "${BOLD}claude --model claude-sonnet-5${RESET}"
fi

if [ "$SETUP_CODEX" -eq 1 ]; then
	printf '\n'
	info "Start Codex:"
	printf '  %s\n' "${BOLD}codex${RESET}"
	detail "Open a new shell first, so the $API_KEY_VAR export takes effect."
fi

printf '\n'
detail "The proxy must stay running while you use an agent."
