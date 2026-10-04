"""Send the per-message effort beta header to GitHub Copilot.

Claude Code changes effort mid-conversation by appending
``{"role": "system", "content": [], "output_config": {"effort": ...}}``.
Upstream only accepts that with the ``mid-conversation-output-config-2026-07-01``
beta, but LiteLLM forwards the client's ``anthropic-beta`` header only to the
anthropic/bedrock/vertex_ai providers. Copilot therefore gets the field without
the beta and answers 400 ``messages.N.output_config: Extra inputs are not
permitted``. This hook adds the beta whenever a request uses the feature.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger

BETA = "mid-conversation-output-config-2026-07-01"
BETA_HEADER = "anthropic-beta"
BETA_SEPARATOR = ","
EXTRA_HEADERS_KEY = "extra_headers"
MESSAGES_KEY = "messages"
OUTPUT_CONFIG_KEY = "output_config"
ROLE_KEY = "role"
SYSTEM_ROLE = "system"


def uses_per_message_effort(messages: Any) -> bool:
    """Whether any system message carries its own ``output_config``."""
    return any(
        isinstance(m, dict)
        and m.get(ROLE_KEY) == SYSTEM_ROLE
        and OUTPUT_CONFIG_KEY in m
        for m in messages or []
    )


def with_beta(beta_header: str) -> str:
    """Return the comma-separated ``beta_header`` with ``BETA`` included once."""
    betas = [b.strip() for b in beta_header.split(BETA_SEPARATOR) if b.strip()]
    if BETA not in betas:
        betas.append(BETA)
    return BETA_SEPARATOR.join(betas)


class MidConversationEffortHandler(CustomLogger):
    """Add the per-message effort beta to requests that need it."""

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Add the beta header just before the upstream call when it is needed."""
        if not uses_per_message_effort(kwargs.get(MESSAGES_KEY)):
            return None
        headers = dict(kwargs.get(EXTRA_HEADERS_KEY) or {})
        headers[BETA_HEADER] = with_beta(headers.get(BETA_HEADER, ""))
        kwargs[EXTRA_HEADERS_KEY] = headers
        return kwargs


handler = MidConversationEffortHandler()
