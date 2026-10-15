"""Keep ``thinking.display`` to the values Copilot accepts.

Copilot accepts only ``summarized`` and ``omitted`` and answers anything else
with 400 ``thinking.adaptive.display: Input should be 'summarized', 'omitted'``.
Claude Code 2.1.295 considers this proxy first-party and sends two values that
only Anthropic-hosted sessions accept:

* ``highlights``, when the user picks that display. Claude Code recovers by
  retrying with ``omitted``, but it does that once per process and then shows
  no thinking text at all. ``summarized`` is the accepted value closest to
  ``highlights``: Claude Code renders both as thinking text.
* ``updates``, by default (``CLAUDE_CODE_THINKING_DISPLAY_UPDATES``), together
  with the ``thinking-display-updates-2026-08-18`` beta. It asks for connector
  text instead of thinking text, so ``summarized`` is not a match; the key is
  dropped instead, which is what Claude Code itself sends after the 400.

Any other unknown value is dropped too, so a new display mode cannot fail the
first request of every session.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

from typing import Any, TypeGuard, cast

from litellm.integrations.custom_logger import CustomLogger

THINKING_KEY = "thinking"
DISPLAY_KEY = "display"
HIGHLIGHTS = "highlights"
SUMMARIZED = "summarized"
ACCEPTED = {SUMMARIZED, "omitted"}


def needs_rewrite(thinking: object) -> TypeGuard[dict[str, Any]]:
    """Whether ``thinking`` carries a display Copilot rejects."""
    if not isinstance(thinking, dict):
        return False
    t = cast(dict[str, Any], thinking)
    return DISPLAY_KEY in t and t[DISPLAY_KEY] not in ACCEPTED


class ThinkingDisplayHandler(CustomLogger):
    """Map or drop a rejected ``thinking.display`` before the upstream call."""

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Rewrite a rejected display just before the upstream call."""
        thinking = kwargs.get(THINKING_KEY)
        if not needs_rewrite(thinking):
            return None
        thinking = dict(thinking)
        if thinking.pop(DISPLAY_KEY) == HIGHLIGHTS:
            thinking[DISPLAY_KEY] = SUMMARIZED
        kwargs[THINKING_KEY] = thinking
        return kwargs


handler = ThinkingDisplayHandler()
