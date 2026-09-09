# LiteLLM Claude Code Proxy

Routes Claude Code through your GitHub Copilot subscription. Claude Code points at the local proxy as if it were Anthropic's API; LiteLLM translates and forwards requests to GitHub Copilot.

## Acknowledgments

This is a fork of [National Bank Belgium's LiteLLM Claude Code Proxy](https://github.com/NationalBankBelgium/litellm-claude-code-proxy). It is based on [dsebastiens tutorial](https://www.dsebastien.net/claude-code-on-github-copilot-subscription/). I optimized the workflow and the documentation quite a bit and extended for further shortcomings.

## License

The original upstream work remains under the [MIT License](LICENSE). All modifications and additions made in this fork are licensed under [PolyForm Noncommercial 1.0.0](LICENSE-MODIFICATIONS) — free for personal, educational, and other noncommercial use; commercial use of these changes requires permission from the author.

## Prerequisites

* A GitHub Copilot subscription (personal or enterprise)
* [uv](https://docs.astral.sh/uv/) installed
* Python 3.11–3.13 in PATH (3.14 is not supported — some dependency incompatibility)
* A virtual environment is recommended (conda, venv, etc.)
* Docker with the `docker compose` plugin (recommended; native fallback available)
* [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) installed
* jq (required by the model catalog and context-window helpers below)

## Setup

```bash
./setup.sh
```

## Running with Docker Compose

Docker Compose is the recommended day-to-day runtime. It provides LiteLLM, PostgreSQL, persistence, restart policy, virtual keys, and spend tracking.

```bash
docker compose up -d
```

Starts the proxy on port `4000` (override with `LITELLM_PORT`) and PostgreSQL 18 with a persistent `pgdata` volume.

### Native fallback

If Docker/Compose is unavailable or unsuitable due to networking or corporate proxy behavior, start LiteLLM only:

```bash
./scripts/start_proxy.sh
```

Start Claude Code separately with either runtime:

```bash
claude --model claude-sonnet-5
```

### Stop / clean up

```bash
docker compose down        # stop containers (data preserved)
docker compose down -v     # stop containers and delete the database volume
```

## Choosing a model

It is recommended to always pass `--model` since the model picker in Claude Code's UI only reliably shows Haiku and Sonnet. For more information [see known limitations](#known-limitations).

```bash
claude --model <model_name>
```

### Override the context window

Set a global context-window override when Claude Code does not recognize your model's limit:

```bash
./scripts/set_max_context.sh 272000
./scripts/set_max_context.sh delete
./scripts/set_max_context.sh --help
```

The helper requires jq and changes only `env.CLAUDE_CODE_MAX_CONTEXT_TOKENS` in `~/.claude/settings.json`. Use a positive integer token count without suffixes or leading zeros. It preserves unrelated settings, rejects malformed JSON and symlink settings files, and writes atomically with owner-only permissions.

For a shorter command, define this alias (replace the path with your checkout's absolute path):

```bash
alias ccmax='"/absolute/path/to/litellm-claude-code-proxy-tobiwo/scripts/set_max_context.sh"'
ccmax 272000
ccmax delete
```

### Note on available models in litellm_config.yaml

Your copilot licence might have more or different available models than listed in the respective config. Fetch the catalog for your subscription with:

```bash
./scripts/fetch_copilot_models.sh
```

Store the response in a file with:

```bash
./scripts/fetch_copilot_models.sh --output github-models.json
```

The helper uses the subscription-specific API endpoint and refreshes authentication once if the cached API token is rejected. It may show the browser device flow if no reusable authentication is available.

Each entry's `id` field is the exact string to use after `github_copilot/` in `litellm_params.model`. Each entry also lists `supported_endpoints` (see the next section, this determines whether you need extra config).

### Some models need extra config: `/responses` vs `/chat/completions`

GitHub Copilot models don't all speak the same wire API. Check the `supported_endpoints` field from the model catalog response above:

* Models with `/chat/completions` in `supported_endpoints` (all current Claude models, most GPT models) work with a plain entry.
* Models with only `/responses` in `supported_endpoints` (e.g. the `gpt-5.3-codex`, `gpt-5.5`, and `gpt-5.6-*` family) need `model_info.mode: responses`, otherwise you get a `400 ... is not accessible via the /chat/completions endpoint` error:

  ```yaml
  - model_name: gpt-5-6-luna
    model_info:
      mode: responses
    litellm_params:
      model: github_copilot/gpt-5.6-luna
  ```

Since these Responses-API models get translated on the fly through Claude Code's Anthropic-style `/v1/messages` endpoint, expect the translation to be lossier than for native chat-completions models (e.g. streaming or tool-call edge cases).

## PII guardrail

Every prompt is scanned by [Microsoft Presidio](https://github.com/microsoft/presidio) before it leaves your machine. Detected sensitive data is **masked**, not blocked — the value is replaced with a placeholder (`<EMAIL_ADDRESS>`) and the request continues, so Claude Code keeps working and the model never sees the real value.

Presidio runs as two sidecar containers (`presidio-analyzer`, `presidio-anonymizer`) started by `docker compose up -d`.

Masked entities (`litellm_config.yaml`, `guardrails:` block): `CREDIT_CARD`, `IBAN_CODE`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IP_ADDRESS`, `US_SSN`.

Verify it is live:

```bash
curl -s http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-5","max_tokens":100,
       "messages":[{"role":"user","content":"Repeat this back exactly: contact me at max.mustermann@example.com"}]}'
```

The reply must contain `<EMAIL_ADDRESS>`, not the real address.

**Disabling:** set `default_on: false` in the `guardrails:` block. This flag is load-bearing — Claude Code speaks plain Anthropic Messages and cannot send a per-request `guardrails: [...]` array, so without `default_on: true` the guard loads but silently never runs.

**Language:** English only. Structured entities (email, IBAN, credit card, phone) are language-independent; only `PERSON`/`LOCATION` detection is language-bound. German NER would need a custom analyzer image with `de_core_news_md`.

**Native fallback caveat:** `PRESIDIO_*_API_BASE` in `.env` uses Docker service DNS names that do not resolve outside Compose. If you run `./scripts/start_proxy.sh`, publish the Presidio ports and override the endpoints to `http://localhost:<port>`, or set `default_on: false` to run without the guard.

## Secret guardrail

A second guardrail (`secret-filter` in `litellm_config.yaml`) masks credentials before the prompt leaves your machine, using LiteLLM's built-in `litellm_content_filter` with custom regex patterns:

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
       "messages":[{"role":"user","content":"Repeat this back exactly: key AKIAIOSFODNN7EXAMPLE and const total = calculateTotal(items);"}]}'
```

The reply must contain `[AWS_KEY_REDACTED]` **and** `const total = calculateTotal(items);` intact. The intact code is the real regression check.

**Why not LiteLLM's built-in `hide-secrets` guardrail:** it is deliberately not used, and should not be added. It wraps Yelp's `detect-secrets`, whose entropy plugins flag ordinary English words. Tested on `Creds AKIAIOSFODNN7EXAMPLE. Code: const total = calculateTotal(items);` it matched `Creds`, `const`, `total`, `items`, `Code`, `7E`, `ca`, and `ed` alongside the real key. LiteLLM then applies those matches through a sequential `str.replace` loop, so replacing `Code` first rewrote the string and the later AWS-key replacement **missed** — the credential reached the model unredacted while the surrounding prose was destroyed. Plain prose degrades the same way: `Refactor the cached loader and fix the deprecated retry logic in scheduler.py` came back as `[REDACTED] [REDACTED] ... [REDACTED].[REDACTED]`. `litellm_content_filter` masks by *span* instead, which is why code and prose survive it.

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
     jsonb_array_elements(s.metadata::jsonb -> 'guardrail_information') g
WHERE jsonb_typeof(s.metadata::jsonb -> 'guardrail_information') = 'array'
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

**Existing ENV variables:** Please be sure to not have any conflicting Claude Code / Anthropic env variables active in your .bashrc or .zshrc.

**Multi LLM provider setup:** This setup is specifically for GitHub Copilot. If you want to use multiple LLM providers like e.g. additionally connecting to native Anthropic API or Amazon Bedrock hosted Claude instances you need to do further manually editing of Claude Codes settings.json.

**Default model:** Claude Code defaults to `claude-opus-5` with 1M context. That variant is not in GitHub Copilot.

**Beta header rejections (`400`):** If Claude Code fails with `Unexpected value(s) for the anthropic-beta header` or `Extra inputs are not permitted`, set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` in your shell and restart Claude Code.

**Litellm Proxy**: The litellm proxy always needs to be active when you want to use Claude Code.

## References

* [Claude Code on GitHub Copilot Subscription](https://www.dsebastien.net/claude-code-on-github-copilot-subscription/)
* [LiteLLM GitHub Copilot Provider](https://docs.litellm.ai/docs/providers/github_copilot)
* [Use Claude Code with Non-Anthropic Models](https://docs.litellm.ai/docs/tutorials/claude_non_anthropic_models)
* [LiteLLM Proxy Config Settings](https://docs.litellm.ai/docs/proxy/config_settings)
