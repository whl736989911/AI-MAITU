"""Preserve turn scope across HITL resumes on harness-agent 1.0.14."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from harness_agent import HarnessAgentManager
from harness_agent.llm.session_header import session_header_scope
from harness_agent.observability.logging import logging_scope

_CONTEXT_TTL_SECONDS = 30 * 60
_MAX_PAUSED_CONTEXTS = 1024


class HitlResumeContexts:
    """Keep only paused turns; graph state itself remains in the checkpointer."""

    def __init__(self) -> None:
        self._contexts: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    def remember(self, agent_id: str, thread_id: str, config: dict[str, Any]) -> None:
        now = time.monotonic()
        self._prune(now)
        self._contexts.pop((agent_id, thread_id), None)
        while len(self._contexts) >= _MAX_PAUSED_CONTEXTS:
            self._contexts.pop(next(iter(self._contexts)))
        self._contexts[agent_id, thread_id] = (now, config)

    def take(self, agent_id: str, thread_id: str) -> dict[str, Any] | None:
        self._prune(time.monotonic())
        found = self._contexts.pop((agent_id, thread_id), None)
        return found[1] if found is not None else None

    def clear_agent(self, agent_id: str) -> None:
        for key in list(self._contexts):
            if key[0] == agent_id:
                del self._contexts[key]

    def _prune(self, now: float) -> None:
        for key, (created, _) in list(self._contexts.items()):
            if now - created >= _CONTEXT_TTL_SECONDS:
                del self._contexts[key]


async def resume_hitl_with_config(
    manager: HarnessAgentManager,
    agent_id: str,
    thread_id: str,
    decisions: list[dict[str, Any]],
    config: dict[str, Any],
) -> AsyncIterator[Any]:
    """Use native resume scopes without its thread-id-only config replacement.

    The pinned runtime's public ``resume_hitl`` discards configurable.user,
    conversation mode, feature ACL context and the steering turn id. Keep its
    invocation/cancellation/session scopes and call the same protocol with the
    original turn config instead. No global runtime monkey patch is installed.
    """
    entry = manager.get_agent(agent_id)
    agent = entry.agent
    protocol = agent._get_protocol(None)
    resume_stream = getattr(protocol, "resume_stream", None)
    if resume_stream is None:
        raise ValueError(f"Protocol {protocol.name!r} does not support HITL resume")
    resumed_config = {
        **config,
        "configurable": {**config.get("configurable", {}), "thread_id": thread_id},
    }
    async with manager._cancellable_stream(agent_id, thread_id) as cancelled, agent._invocation():
        with session_header_scope(thread_id), logging_scope(agent_id):
            async for chunk in resume_stream(thread_id, decisions, resumed_config):
                if cancelled.is_set():
                    break
                yield chunk
