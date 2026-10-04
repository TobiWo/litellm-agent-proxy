"""Translate ``thinking: disabled`` into what Claude 5.5 models accept.

Claude Code turns thinking off with ``{"type": "disabled"}``, e.g. for the auto
mode classifier, which it sends to ``claude-sonnet-5``. Copilot no longer
serves that model, so ``litellm-config.yaml`` aliases it to Sonnet 5.5. The 5.5
models reject ``disabled`` with a 400 and want ``{"type": "between_tools"}``
instead. Claude Code reports the failed check as the classifier being
"temporarily unavailable" and does not run the action.

Every Claude deployment in ``model_list`` is a 5.5 model, so the rewrite is
unconditional. If you add a Claude model that still takes ``disabled``,
restrict it to the 5.5 deployments.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger

THINKING_KEY = "thinking"
DISABLED = {"type": "disabled"}
THINKING_OFF = {"type": "between_tools"}


class ThinkingOffHandler(CustomLogger):
    """Swap ``thinking: disabled`` for ``between_tools`` before the upstream call."""

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Rewrite a disabled-thinking request just before the upstream call."""
        if kwargs.get(THINKING_KEY) != DISABLED:
            return None
        kwargs[THINKING_KEY] = dict(THINKING_OFF)
        return kwargs


handler = ThinkingOffHandler()
