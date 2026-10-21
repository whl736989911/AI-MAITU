"""Inject authenticated dashboard steering only at model-call boundaries."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from octop.infra.gateway.steering import SteeringItem, TurnSteeringRegistry

logger = logging.getLogger(__name__)

STEERING_TURN_ID_KEY = "octop_steering_turn_id"
_registry = TurnSteeringRegistry()


def turn_steering_registry() -> TurnSteeringRegistry:
    """Return the process-wide queue shared by WS routes and agent middleware."""
    return _registry


def _configurable(runtime: Any) -> dict[str, Any]:
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        try:
            from langgraph.config import get_config  # noqa: PLC0415

            config = get_config()
        except (RuntimeError, LookupError):
            config = {}
    configurable = config.get("configurable") if isinstance(config, dict) else None
    return configurable if isinstance(configurable, dict) else {}


def _safe_turn_ids(runtime: Any) -> tuple[str, str] | None:
    configurable = _configurable(runtime)
    thread_id = configurable.get("thread_id")
    turn_id = configurable.get(STEERING_TURN_ID_KEY)
    if not isinstance(thread_id, str) or not thread_id.strip():
        return None
    if not isinstance(turn_id, str) or not turn_id.strip():
        return None
    return thread_id.strip(), turn_id.strip()


class TurnSteeringMiddleware(AgentMiddleware[Any, Any]):
    """Inject queued steering as a new user message before a safe model call.

    It never edits existing messages (in particular tool call/result pairs),
    and never takes effect in a turn without a server-stamped turn id.
    """

    @property
    def name(self) -> str:
        return "OctopTurnSteeringMiddleware"

    def __init__(self, registry: TurnSteeringRegistry | None = None) -> None:
        super().__init__()
        self._registry = registry or turn_steering_registry()

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        ids = _safe_turn_ids(runtime)
        if ids is None:
            return None
        try:
            item = await self._registry.take_at_boundary(ids[0], ids[1])
        except Exception:
            logger.exception("failed to read dashboard steering at model boundary")
            return None
        if item is None:
            return None
        return {"messages": [_steering_message(item)]}


def _steering_message(item: SteeringItem) -> HumanMessage:
    # A unique id prevents state reducers/replayed snapshots from duplicating it.
    return HumanMessage(
        id=f"octop-steer-{item.client_turn_id}",
        content=(
            "[Dashboard steering instruction — incorporate this direction into "
            f"the current turn without discarding prior context]\n{item.text}"
        ),
    )


__all__ = ["STEERING_TURN_ID_KEY", "TurnSteeringMiddleware", "turn_steering_registry"]
