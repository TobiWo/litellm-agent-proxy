# CLAUDE.md

Project docs live in [README.md](README.md) — setup, running, agent config, guardrails,
known limitations. Read it before making changes; do not duplicate its content here.

## Shell scripts (`setup.sh`, `scripts/*.sh`)

Must stay Bash 3.2 compatible (macOS default ships 3.2), in addition to Linux/WSL2.
No associative arrays, no `readlink -f` / `realpath`, no `sed -i`, no other GNU-only
or Bash-4+ constructs. `scripts/common.sh` is sourced by callers, not executed directly.

`common.sh` therefore has **no shebang and no executable bit** — it is a library, and
pre-commit flags the mismatch. It carries `# shellcheck shell=bash` instead: without
it shellcheck reports SC2148 and, not knowing the dialect, silently skips the
bash-only constructs in the file (`${!key+x}`, `${line%$'\r'}`). Do not "restore" the
shebang.

## Config helpers (`scripts/configure-*.py`)

`configure-claude.py` and `configure-codex.py` own all writes to `~/.claude/settings.json`
and `~/.codex/config.toml` respectively. They are exempt from the Bash 3.2 rule but bound
by the Python 3.11+ floor that `common.sh:check_python` enforces — `configure-codex.py`
needs `tomllib`, which entered the stdlib in 3.11. Do not relax that floor.

`configure-claude.py` is the single JSON writer in this repo; `set-max-context.sh` is a
thin wrapper around it. Do not reintroduce a second implementation (the `jq` version it
replaced drifted from the `setup.sh` one).

`configure-codex.py` parses TOML with `tomllib` to decide what to change, then applies
targeted line edits while tracking the current `[table]` header. This is deliberate:
the stdlib has no TOML writer, a `tomli_w` round-trip would destroy the user's comments
and ordering, and `sed` cannot tell a top-level `model_provider =` from one inside an
unrelated table. Every edit is re-parsed before it replaces the original — keep that
guard. Do not swap in a line-oriented or full-rewrite approach.

`update-codex-catalog.py` regenerates `codex/codex-model-catalog.json`. The bundled
catalog (`codex debug models --bundled`) is the single source of truth for which models
exist: an entry is emitted only when the model is both a `mode: responses` alias in
`litellm-config.yaml` and present in the bundle. Copilot supplies the limits —
`context_window` is `max_prompt_tokens` (the prompt budget), **not**
`max_context_window_tokens` (which includes output headroom). Do not "fix" that to the
larger number. A model Codex stops bundling is dropped and reported, by design.

`STRIPPED_FIELDS` also drops `tool_mode`. Codex's bundled catalog tags gpt-5.6-*/gpt-6
models `tool_mode: "code_mode_only"`, which force-selects Codex's JS "code mode" `exec`
tool — a Responses API `custom`-type tool. GitHub Copilot's `/responses` backend doesn't
implement `custom` tools correctly: it replies with a plain `function_call` instead of
`custom_tool_call`, and Codex's own tool router then rejects every call as an
"incompatible payload", so no command ever runs. This is a GitHub Copilot API bug, not a
LiteLLM one — LiteLLM's `github_copilot` Responses provider is a spec-compliant
passthrough and has no custom-tool bridging on that path (unlike the separate
chat-completions-bridge path, which does). Dropping `tool_mode` from the vendored catalog
lets Codex fall back to `[features.code_mode]` / the model's `shell_type`
(`unified_exec`), restoring the working `shell`/`local_shell` tool. Do not restore this
field, and don't "fix" it by changing `shell_type` instead — that alone doesn't help,
`tool_mode` is what pins `code_mode_only`.

## Guardrails (`litellm-config.yaml`)

`litellm-config.yaml` is yamllint-clean, so its values are double-quoted. Anything that
parses it textually — `configure-codex.py:responses_models()`, the model-list `sed` in
`setup.sh` — must accept **both** quoted and bare values. Quoting the file once already
broke Codex setup silently (`no models with 'mode: responses' found`) because the
patterns only matched bare values. Test any change to those patterns against both forms.

The `secret-filter` guardrail deliberately does NOT use LiteLLM's built-in
`hide-secrets` guardrail — see the README's "Secret guardrail" section for why
(entropy-based detection flags ordinary code/prose and its sequential `str.replace`
can let real secrets through while mangling surrounding text). Don't suggest
switching back to it. New secret patterns need a stable prefix/format; entropy-only
detection is not viable here.

After editing `litellm-config.yaml`, verify guardrails with the `curl` checks in the
README (PII guardrail and secret guardrail sections) — there is no automated test
suite in this repo.

## Version pins

`compose.yml` (Docker image tag) and `scripts/common.sh` (`run_litellm`, native
fallback) both pin the same LiteLLM version deliberately. If you bump one, bump the
other and check `common.sh`'s comment for why the native path needs an explicit pin
(a newer LiteLLM version previously broke native `uv run` resolution via a FastAPI
incompatibility that doesn't affect the prebuilt Docker image).

## Secrets

`.env` is a real, active credentials file (not a template) — never print, cat, or
commit its contents. Use `.env.example` as the structural reference instead.

The `LITELLM_API_KEY` export that `setup.sh` appends to a user's shell rc file
dereferences `.env` at shell startup instead of embedding the key. Keep it that way:
the credential must live in exactly one file.
