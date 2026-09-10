# LiteLLM Agent Proxy

Routes coding agents through your GitHub Copilot subscription. [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) and [Codex CLI (or App)](https://developers.openai.com/codex) point at the local proxy as if it were their vendor's API; LiteLLM translates and forwards requests to GitHub Copilot.

## Acknowledgments

This is a fork of [National Bank Belgium's LiteLLM Claude Code Proxy](https://github.com/NationalBankBelgium/litellm-claude-code-proxy). It is based on [dsebastiens tutorial](https://www.dsebastien.net/claude-code-on-github-copilot-subscription/). I reworked the workflow and the documentation completely to make it completely seemless and user-friendly.

## License

The original upstream work remains under the [MIT License](LICENSE). All modifications and additions made in this fork are licensed under [PolyForm Noncommercial 1.0.0](LICENSE-MODIFICATIONS) — free for personal, educational, and other noncommercial use; commercial use of these changes requires permission from the author.

## Prerequisites

* A GitHub Copilot subscription (personal or enterprise)
* [uv](https://docs.astral.sh/uv/) installed
* Python 3.11–3.13 in PATH (3.14 is not supported — some dependency incompatibility).
* A virtual environment is recommended (conda, venv, etc.)
* Docker with the `docker compose` plugin (recommended; native fallback available)
* jq (required by the model catalog helper)
* At least one agent installed: [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) or [Codex CLI](https://developers.openai.com/codex)

## Setup

```bash
./setup.sh
```

The script asks which agent to configure (Claude Code, Codex, or both), checks the prerequisites for that choice, creates `.env`, performs GitHub Copilot authentication, writes the agent's configuration and might add env vars to your `.bashrc` or `.zshrc`. Every change is shown as a diff and confirmed before anything is written.

Non-interactive:

```bash
./setup.sh --agent claude
./setup.sh --agent codex
./setup.sh --agent both
```

### Migration from v0.1.0

New env vars were added with `v0.2.0`. If you are migrating from `v0.1.0` to `v0.2.0` or later, make sure to update your `.env` file accordingly.

## Running

Docker Compose is the recommended day-to-day runtime. It provides LiteLLM, PostgreSQL, persistence, restart policy, virtual keys, and spend tracking.

```bash
docker compose up -d
```

Starts the proxy on port `4000` (override with `LITELLM_PORT`) and PostgreSQL 18 with a persistent `pgdata` volume.

### Native fallback

If Docker/Compose is unavailable or unsuitable due to networking or corporate proxy behavior, start LiteLLM only:

```bash
./scripts/start-proxy.sh
```

### Stop / clean up

```bash
docker compose down        # stop containers (data preserved)
docker compose down -v     # stop containers and delete the database volume
```

**The proxy must stay running while you use an agent.**

## Agents

### Claude Code

Always pass `--model`, since the model picker in Claude Code's UI only reliably shows Haiku and Sonnet:

```bash
# general startup
claude --model claude-sonnet-5

# startup with increased context window
claude --model claude-sonnet-5[1m]
```

`setup.sh` also disables Claude Code's non-essential telemetry to Anthropic — routing through a local proxy while still reporting usage upstream would defeat the point. Both flags are shown in the diff before anything is written, and can be removed by hand later.

#### Override the context window

Set a global context-window override when Claude Code does not recognize your model's limit:

```bash
./scripts/set-max-context.sh 272000
./scripts/set-max-context.sh delete
./scripts/set-max-context.sh --help
```

Use a positive integer token count without suffixes or leading zeros.

For a shorter command, define this alias (replace the path with your checkout's absolute path):

```bash
alias ccmax='"/absolute/path/to/litellm-agent-proxy/scripts/set-max-context.sh"'
ccmax 272000
ccmax delete
```

#### GPT models via Claude Code

You can use GPT models in Claude Code but these are Response-API models (see [Models](#models)) which get translated on the fly through Claude Code's Anthropic-style `/v1/messages` endpoint. Expect the translation to be lossier than for native chat-completions models.

### Codex

```bash
# Start it as usual
codex
```

#### IMPORTANT

The following chapter is an important limitation. Although there is a separate chapter for `limitations` I put it here, since it is essential.

GitHub Copilot's `/responses` API endpoint does not answer tool calls of type `custom` correctly. Why does it matter / what you need to do?

1. `setup.sh` disables `[features.code_mode]` (Codex own JS `exec` tool) to avoid the generasl issue with custom tool calls. It will fall back to plain `shell` tools which might lead to issues on Windows.
2. Codex will not be able to use it's own `apply_patch` file editing tool via a custom tool call. Hence it is recommended to add an explicit instruction to codex to invoke it as a plain shell command (e.g. `Note for editing/writing file content: Use the workspace apply_patch binary directly since there is an issue with the underlying LLM API provider endpoint for the apply_patch custom tool call.`). It will also work without this instruction but it is faster since Codex will not fail in the first place.

#### Codex App

Note, you can also use the Codex App to interact with the proxy. It might be necessary to logout of an existing OpenAI session first. However, I experienced that after `./setup.sh` the app worked without further intervention since it correctly references the local litellm setup which does not need any login.

There are two things worth to mention:

1. If the app fails with e.g. `Invalid model name passed in model=gpt-5.6-luna` (dots) this might be due to the fact that the app is re-sending a model name it remembered from before, independent of `config.toml` and the catalog. You would see `custom` in the model picker. Simply switch to a concrete model from the list. Expect this after renaming an alias or regenerating the catalog ([see Model catalog](#model-catalog)).
1. The app's `Configuration` pane scopes Agent defaults to the open thread's/chat's project and will name a project-level `config.toml` even when none exists (if there is an empty `.codex` folder in the project); that is harmless. Project config layers on top of your user config, so a missing or empty one simply contributes nothing.

#### `LITELLM_API_KEY`

Codex reads the proxy api key from a shell variable (added by `setup.sh` to your `.bashrc` or `.zshrc`) which is referencing `LITELLM_MASTER_KEY` defined in your `.env` file. The secret therefore lives in exactly one file (`.env`, gitignored). Rotating it needs no re-run. Moving or renaming this repo breaks the path. You then need to update your shell configuration.

#### Model catalog

`codex-model-catalog.json` gives Codex metadata (context window, reasoning levels, system prompt) for the proxy's model aliases; without it every model falls back to generic metadata and Codex warns `Model metadata for ... not found`. The file replaces the bundled list entirely, so only the models in it appear in `/model`.

The catalog schema is tied to the Codex version. **Regenerate it after upgrading Codex**:

```bash
./scripts/update-codex-catalog.sh
```

The catalog holds the models that are both in your LiteLLM config and bundled with the installed Codex. Context window sizes come from GitHub Copilot's reported limits.

```bash
# Generate a new catalog and inspect it before applying
./scripts/update-codex-catalog.sh --output /tmp/new-catalog.json
codex -c 'model_catalog_json="/tmp/new-catalog.json' debug models

# Move to catalog location which is referenced in your config.toml (default: /absolute/path/to/THIS_REPO/codex/codex-model-catalog.json)
mv /tmp/new-catalog.json /absolute/path/to/codex-model-catalog.json
```

## Models

Your copilot licence might have more or different available models than listed in the respective config. Fetch the catalog for your subscription with:

```bash
./scripts/fetch-copilot-models.sh
```

Store the response in a file with:

```bash
./scripts/fetch-copilot-models.sh --output github-models.json
```

The helper uses the subscription-specific API endpoint and refreshes authentication once if the cached API token is rejected. It may show the browser device flow if no reusable authentication is available.

Each entry's `id` field is the exact string to use after `github_copilot/` in `litellm_params.model`. Each entry also lists `supported_endpoints` (see the next section, this determines whether you need extra config).

### Some models need extra config: `/responses` vs `/chat/completions`

GitHub Copilot models don't all speak the same wire API. Check the `supported_endpoints` field from the model catalog response above:

* Models with `/chat/completions` in `supported_endpoints` (all current Claude models, older GPT models) work with a plain entry.
* Models with only `/responses` in `supported_endpoints` (e.g. the `gpt-5.3-codex`, `gpt-5.5`, and `gpt-5.6-*` family) need `model_info.mode: responses`, otherwise you get a `400 ... is not accessible via the /chat/completions endpoint` error

## Guardrails

### PII guardrail

Every prompt is scanned by [Microsoft Presidio](https://github.com/microsoft/presidio) before it leaves your machine. Detected sensitive data is **masked**, not blocked — the value is replaced with a placeholder (`<EMAIL_ADDRESS>`) and the request continues, so your agent keeps working and the model never sees the real value.

Presidio runs as two sidecar containers (`presidio-analyzer`, `presidio-anonymizer`) started by `docker compose up -d`.

Masked entities (`litellm-config.yaml`, `guardrails:` block): `CREDIT_CARD`, `IBAN_CODE`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IP_ADDRESS`, `US_SSN`.

Verify it is live:

```bash
curl -s http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-5","max_tokens":100,
       "messages":[{"role":"user","content":"Repeat this back exactly: contact me at <EMAIL_ADDRESS>"}]}'
```

The reply must contain `<EMAIL_ADDRESS>`, not the real address.

**Disabling:** set `default_on: false` in the `guardrails:` block. This flag is load-bearing — Claude Code speaks plain Anthropic Messages and cannot send a per-request `guardrails: [...]` array, so without `default_on: true` the guard loads but silently never runs.

**Language:** English only. Structured entities (email, IBAN, credit card, phone) are language-independent; only `PERSON`/`LOCATION` detection is language-bound. German NER would need a custom analyzer image with `de_core_news_md`.

**Native fallback caveat:** `PRESIDIO_*_API_BASE` in `.env` uses Docker service DNS names that do not resolve outside Compose. If you run `./scripts/start-proxy.sh`, publish the Presidio ports and override the endpoints to `http://localhost:<port>`, or set `default_on: false` to run without the guard.

### Secret guardrail

A second guardrail (`secret-filter` in `litellm-config.yaml`) masks credentials before the prompt leaves your machine, using LiteLLM's built-in `litellm_content_filter` with custom regex patterns:

| Pattern | Matches |
| --- | --- |
| `aws_key` | AWS access key IDs (`AKIA`/`ASIA`/`ABIA`/`ACCA` + 16 base32 chars) |
| `aws_secret` | `aws_secret_access_key = <40 chars>` — the **assignment**, not a bare key (see below) |
| `gh_token` | GitHub classic tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` + 36) |
| `gh_finegrained_pat` | GitHub fine-grained PATs (`github_pat_` + 82) |
| `gitlab_token` | GitLab tokens: `glpat-`, `gloas-`, `gldt-`, `glrt-`/`glrtr-`, `glcbt-`, `glptt-`, `glft-`, `glimt-`, `glagent-`, `glwt-`, `glsoat-`, `glffct-` |
| `gitlab_routable_token` | GitLab's newer routable format (`<prefix><base64>.<len><crc32>`) |
| `gitlab_runner_registration` | Legacy runner registration tokens (`GR1348941…`) |
| `azure_client_secret` | Azure AD/Entra client secrets (`…\dQ~…`) |
| `azure_storage_key` | `AccountKey=`/`SharedAccessKey=` + 86-char base64 (storage, Cosmos, Service Bus) |
| `gcp_api_key` | Google API keys (`AIza` + 35) |
| `gcp_oauth_secret` | Google OAuth client secrets (`GOCSPX-` + 28) |
| `private_key` | PEM blocks (`-----BEGIN … PRIVATE KEY-----` … `-----END`), incl. GCP service-account JSON |
| `slack_token` | Slack tokens (`xoxb-`, `xoxp-`, `xoxo-`, `xoxa-`, `xoxs-`) |

A match is replaced with `[<PATTERN_NAME>_REDACTED]` and the request continues, same as the PII guardrail. Add your own entries under `patterns:` — anything with a stable prefix is a good candidate; anything relying on entropy is not (see below).

Verify it is live:

```bash
curl -s http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-5","max_tokens":100,
       "messages":[{"role":"user","content":"Repeat this back exactly: key [AWS_KEY_REDACTED] and const total = calculateTotal(items);"}]}'
```

The reply must contain `[AWS_KEY_REDACTED]` **and** `const total = calculateTotal(items);` intact. The intact code is the real regression check.

**Why not LiteLLM's built-in `hide-secrets` guardrail:** it is deliberately not used, and should not be added. It wraps Yelp's `detect-secrets`, whose entropy plugins flag ordinary English words. Tested on `Creds [AWS_KEY_REDACTED]. Code: const total = calculateTotal(items);` it matched `Creds`, `const`, `total`, `items`, `Code`, `7E`, `ca`, and `ed` alongside the real key. LiteLLM then applies those matches through a sequential `str.replace` loop, so replacing `Code` first rewrote the string and the later AWS-key replacement **missed** — the credential reached the model unredacted while the surrounding prose was destroyed. Plain prose degrades the same way: `Refactor the cached loader and fix the deprecated retry logic in scheduler.py` came back as `[REDACTED] [REDACTED] ... [REDACTED].[REDACTED]`. `litellm_content_filter` masks by *span* instead, which is why code and prose survive it.

### Checking what got masked

Every request records which guardrails ran and what they caught, in the `metadata.guardrail_information` field of the `LiteLLM_SpendLogs` table. Two ways to read it.

**LiteLLM UI** — `http://localhost:4000/ui`, Logs view. Expand a request to see the guardrail entries.

**Directly in Postgres** — useful for "did I ever leak a secret?", which the UI answers slowly:

```bash
docker compose exec db psql -U "${POSTGRES_USER:-litellm}" -d "${POSTGRES_DB:-litellm}" -c "
SELECT s.\"startTime\",
       g->>'guardrail_name' AS guardrail,
       g->'masked_entity_count' AS masked
FROM \"LiteLLM_SpendLogs\" s,
     jsonb_array_elements(s.metadata<IP_ADDRESS>jsonb -> 'guardrail_information') g
WHERE jsonb_typeof(s.metadata<IP_ADDRESS>jsonb -> 'guardrail_information') = 'array'
  AND g->'masked_entity_count' <> '{}'::jsonb
ORDER BY s.\"startTime\" DESC LIMIT 20;"
```

A row from the secret filter looks like:

```json
{
  "guardrail_name": "secret-filter",
  "guardrail_status": "success",
  "detection_method": "regex",
  "patterns_checked": 4,
  "masked_entity_count": { "aws_key": 1 },
  "match_details": "REDACTED_BY_LITELM",
  "guardrail_response": "REDACTED_BY_LITELM"
}
```

**A nonzero `masked_entity_count` for `secret-filter` means a credential was caught before it left your machine** — the guardrail worked, but the secret was in a prompt, so treat it as a signal to check your workflow (and rotate the credential if you are unsure it was a test value).

**What you cannot get from this:** *which* credential matched. `match_details` and `guardrail_response` are stored as `REDACTED_BY_LITELM` — LiteLLM strips them unless `store_prompts_in_spend_logs: true` is set, and that flag writes the caught secret into the local database in plaintext, defeating the guardrail in order to observe it. It is deliberately left off. You get "an `aws_key` was masked at 14:32", not the key itself; identifying which one means looking at what you were doing at that time.

## Known limitations

### Shared

**Existing ENV variables:** Please be sure to not have any conflicting agent/vendor env variables active in your .bashrc or .zshrc.

**Multi LLM provider setup:** This setup is specifically for GitHub Copilot. If you want to use multiple LLM providers like e.g. additionally connecting to native Anthropic API or Amazon Bedrock hosted Claude instances you need further manual editing of the agent's configuration.

**Litellm Proxy:** The litellm proxy always needs to be active when you want to use an agent.

### Claude Code limitations

**Beta header rejections (`400`):** If Claude Code fails with `Unexpected value(s) for the anthropic-beta header` or `Extra inputs are not permitted`, set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` in your shell and restart Claude Code.

### Codex limitations

**Responses-API models only:** Codex cannot use the Claude or `kimi-k3` aliases — they do not support the Responses API. Setup rewrites an unusable `model` value and tells you.

**Catalog is version-tied:** the catalog schema follows the Codex release; regenerate it after upgrading Codex (see [Model catalog](#model-catalog)).

## References

* [Claude Code on GitHub Copilot Subscription](https://www.dsebastien.net/claude-code-on-github-copilot-subscription/)
* [LiteLLM GitHub Copilot Provider](https://docs.litellm.ai/docs/providers/github_copilot)
* [Use Claude Code with Non-Anthropic Models](https://docs.litellm.ai/docs/tutorials/claude_non_anthropic_models)
* [LiteLLM Proxy Config Settings](https://docs.litellm.ai/docs/proxy/config_settings)
