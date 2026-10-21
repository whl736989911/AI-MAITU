from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Interrupt

from octop.api.routers.chat.history import _pending_hitl_for_dashboard
from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator


@pytest.mark.asyncio
async def test_history_reconstructs_and_reuses_current_native_interrupt_record() -> None:
    coordinator = HitlChannelCoordinator()
    interrupt = Interrupt(
        {
            "value": {
                "action_requests": [{"name": "execute", "args": {"command": "echo safe"}}],
                "review_configs": [
                    {"action_name": "execute", "allowed_decisions": ["approve", "reject"]}
                ],
            }
        },
        id="native-interrupt-1",
    )
    graph = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=(interrupt,)))
    )
    agent = SimpleNamespace(graph=graph)
    registry = MagicMock()
    registry.get_agent.return_value = agent
    server = SimpleNamespace(
        app_runtime=SimpleNamespace(
            gateway=SimpleNamespace(processor=SimpleNamespace(hitl_coordinator=coordinator)),
            agent_registry=registry,
        )
    )
    context = {
        "server": server,
        "thread_id": "thread-restore",
        "agent_id": "agent-1",
        "user_id": 7,
        "session_key": "agent-1:dashboard:7:dm",
        "channel_type": "dashboard",
    }

    first = await _pending_hitl_for_dashboard(**context)
    repeated = await _pending_hitl_for_dashboard(**context)

    assert first is not None and repeated is not None
    assert first["pending_id"] == repeated["pending_id"]
    assert first["action_requests"] == [{"name": "execute", "args": {"command": "echo safe"}}]
    assert first["review_configs"] == [
        {"action_name": "execute", "allowed_decisions": ["approve", "reject"]}
    ]

    coordinator.store.expire_pending_for_thread("thread-restore", agent_id="agent-1", user_id=7)
    graph.aget_state.return_value = SimpleNamespace(
        interrupts=(
            Interrupt(
                {"action_requests": [{"name": "execute", "args": {"command": "echo new"}}]},
                id="native-interrupt-2",
            ),
        )
    )
    replacement = await _pending_hitl_for_dashboard(**context)
    assert replacement is not None
    assert replacement["pending_id"] != first["pending_id"]
    assert coordinator.store.get(first["pending_id"]).status == "expired"  # type: ignore[union-attr]
