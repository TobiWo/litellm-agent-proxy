"""Send the one-hour prompt cache beta header to GitHub Copilot.

With ``promptCacheTtl: "1h"`` Claude Code marks blocks with
``"cache_control": {"type": "ephemeral", "ttl": "1h"}`` and sends the
``extended-cache-ttl-2025-04-11`` beta alongside. LiteLLM forwards the body
but not the client's ``anthropic-beta`` header to Copilot (see
``mid_conversation_effort.py``), so the ``ttl`` arrives without its beta. This
hook adds the beta whenever a request asks for a TTL.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from hooks.mid_conversation_effort import BETA_HEADER, EXTRA_HEADERS_KEY, with_beta

BETA = "extended-cache-ttl-2025-04-11"
CACHE_CONTROL_KEY = "cache_control"
TTL_KEY = "ttl"
# Request fields that can carry cache_control markers.
CACHEABLE_KEYS = ("messages", "system", "tools")


def has_cache_ttl(value: Any) -> bool:
    """Whether any ``cache_control`` nested in ``value`` sets a ``ttl``."""
    if isinstance(value, dict):
        control = value.get(CACHE_CONTROL_KEY)
        if isinstance(control, dict) and TTL_KEY in control:
            return True
        return any(has_cache_ttl(v) for v in value.values())
    if isinstance(value, list):
        return any(has_cache_ttl(v) for v in value)
    return False


class ExtendedCacheTtlHandler(CustomLogger):
    """Add the extended cache TTL beta to requests that need it."""

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Add the beta header just before the upstream call when it is needed."""
        if not any(has_cache_ttl(kwargs.get(key)) for key in CACHEABLE_KEYS):
            return None
        headers = dict(kwargs.get(EXTRA_HEADERS_KEY) or {})
        headers[BETA_HEADER] = with_beta(headers.get(BETA_HEADER, ""), BETA)
        kwargs[EXTRA_HEADERS_KEY] = headers
        return kwargs


handler = ExtendedCacheTtlHandler()
