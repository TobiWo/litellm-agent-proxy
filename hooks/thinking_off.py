"""Translate ``thinking: disabled`` into what Claude 5.5 models accept.

Claude Code turns thinking off with ``{"type": "disabled"}``, e.g. for the auto
mode classifier, which it sends to ``claude-sonnet-5``. Copilot no longer
serves that model, so ``litellm-config.yaml`` aliases it to Haiku 5.5. The 5.5
models reject ``disabled`` with a 400. Opus and Sonnet want
``{"type": "between_tools"}`` instead; Haiku rejects that too and only works
without a ``thinking`` field, so it is dropped there. Claude Code reports a
failed check as the classifier being "temporarily unavailable" and does not run
the action.

The classifier also gets no thinking at all: Claude Code sometimes sends it
``thinking: adaptive`` or ``output_config.effort``, which would make Haiku think
on a call that only needs a ~10-token verdict. Those fields are dropped by
alias (``CLASSIFIER_ALIAS``) before LiteLLM sees the request. That is the only
place the two Haiku aliases can differ: LiteLLM keys the thinking flags by the
backend model, so ``model_info`` on one alias applies to both.

Every Claude deployment in ``model_list`` is a 5.5 model, so the rewrite is
unconditional. If you add a Claude model that still takes ``disabled``,
restrict it to the 5.5 deployments.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any, cast

from litellm.integrations.custom_logger import CustomLogger

THINKING_KEY = "thinking"
DISABLED = {"type": "disabled"}
THINKING_OFF = {"type": "between_tools"}
NO_THINKING_FAMILY = "haiku"
CLASSIFIER_ALIAS = "claude-sonnet-5"
OUTPUT_CONFIG_KEY = "output_config"
EFFORT_KEY = "effort"


class ThinkingOffHandler(CustomLogger):
    """Fit thinking params to what each Claude deployment accepts."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> None:
        """Strip all thinking from classifier requests before LiteLLM maps them."""
        if data.get("model") != CLASSIFIER_ALIAS:
            return
        data.pop(THINKING_KEY, None)
        output_config = data.get(OUTPUT_CONFIG_KEY)
        if isinstance(output_config, dict):
            config = cast(dict[str, Any], output_config)
            config.pop(EFFORT_KEY, None)
            if not config:
                del data[OUTPUT_CONFIG_KEY]

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Rewrite a disabled-thinking request just before the upstream call."""
        if kwargs.get(THINKING_KEY) != DISABLED:
            return None
        if NO_THINKING_FAMILY in str(kwargs.get("model", "")):
            del kwargs[THINKING_KEY]
        else:
            kwargs[THINKING_KEY] = dict(THINKING_OFF)
        return kwargs


handler = ThinkingOffHandler()
