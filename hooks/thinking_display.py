"""Translate ``thinking.display: highlights`` into a value Copilot accepts.

Claude Code 2.1.295 asks for ``{"type": "adaptive", "display": "highlights"}``
whenever it considers the provider first-party, which includes this proxy.
Only Anthropic-hosted sessions accept ``highlights``; Copilot answers 400
``thinking.adaptive.display: Input should be 'summarized', 'omitted'``. Claude
Code recovers by retrying with ``omitted``, but it does that once per process
and then shows no thinking text at all. ``summarized`` is the accepted value
closest to ``highlights``: Claude Code renders both as thinking text.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger

THINKING_KEY = "thinking"
DISPLAY_KEY = "display"
HIGHLIGHTS = "highlights"
SUMMARIZED = "summarized"


def requests_highlights(thinking: Any) -> bool:
    """Whether ``thinking`` asks for the first-party-only ``highlights`` display."""
    return isinstance(thinking, dict) and thinking.get(DISPLAY_KEY) == HIGHLIGHTS


class ThinkingDisplayHandler(CustomLogger):
    """Swap ``display: highlights`` for ``summarized`` before the upstream call."""

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Rewrite a highlights display just before the upstream call."""
        if not requests_highlights(kwargs.get(THINKING_KEY)):
            return None
        kwargs[THINKING_KEY] = {**kwargs[THINKING_KEY], DISPLAY_KEY: SUMMARIZED}
        return kwargs


handler = ThinkingDisplayHandler()
