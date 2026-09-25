"""Keep Responses-API tool definitions intact through LiteLLM's guardrails.

LiteLLM's guardrail layer scans a /responses request's tools by converting them
to chat-completions format and back, then writes the result over
``data["tools"]``. That round trip is lossy:

* ``namespace`` tools (how Codex declares MCP servers) are flattened into
  plain functions named ``<namespace>__<name>``, so Codex rejects every MCP
  call as ``unsupported call``.
* ``custom`` tools (Codex's ``apply_patch`` and code-mode ``exec``) become
  ``function`` tools, so the model answers with ``function_call`` instead of
  ``custom_tool_call`` and Codex rejects the payload.

GitHub Copilot handles both tool types natively. This hook snapshots the tools
before the guardrails run and restores the snapshot just before the request is
sent upstream. Prompt text is still guardrailed; only the tool definitions
(which come from the agent, not the user) skip the rewrite.

Snapshots are looked up rather than popped so router retries restore them too.
Streaming calls have no reliable completion hook to clean up in, so the store
is bounded instead; an entry is only needed for its request's lifetime.

Loaded via ``litellm_settings.callbacks`` in ``litellm-config.yaml``.
"""

from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

MAX_SNAPSHOTS = 256
RESPONSES_CALL_TYPE = "aresponses"
CALL_ID_KEY = "litellm_call_id"
TOOLS_KEY = "tools"

Tools = list[dict[str, Any]]


class ToolSnapshotStore:
    """A bounded, insertion-ordered store of tool lists keyed by call id."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._snapshots: OrderedDict[str, Tools] = OrderedDict()

    def save(self, call_id: str, tools: Tools) -> None:
        """Store a deep copy of ``tools``, evicting the oldest entries past capacity."""
        self._snapshots[call_id] = copy.deepcopy(tools)
        while len(self._snapshots) > self._capacity:
            self._snapshots.popitem(last=False)

    def load(self, call_id: str | None) -> Tools | None:
        """Return a deep copy of the stored tools, or None when absent."""
        if not call_id or call_id not in self._snapshots:
            return None
        return copy.deepcopy(self._snapshots[call_id])


class ResponsesToolsHandler(CustomLogger):
    """Restore /responses tool definitions after the guardrails rewrote them."""

    def __init__(self) -> None:
        super().__init__()
        self._store = ToolSnapshotStore(MAX_SNAPSHOTS)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> None:
        """Snapshot the tools of a /responses request before guardrails run."""
        call_id = data.get(CALL_ID_KEY)
        tools = data.get(TOOLS_KEY)
        if call_type == RESPONSES_CALL_TYPE and call_id and tools:
            self._store.save(call_id, tools)

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any] | None:
        """Put the snapshotted tools back just before the upstream call."""
        tools = self._store.load(kwargs.get(CALL_ID_KEY))
        if tools is None:
            return None
        kwargs[TOOLS_KEY] = tools
        return kwargs


handler = ResponsesToolsHandler()
