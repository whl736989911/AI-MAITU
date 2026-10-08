from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octop.api.routers.chat import routes


@pytest.mark.asyncio
async def test_chat_welcome_exposes_only_global_hitl_enabled_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "assert_agent_access", lambda *_args: None)
    monkeypatch.setattr(routes, "default_welcome_payload", lambda _catalog: {})
    registry = MagicMock()
    registry.workspace_for_agent.return_value = None
    registry.get_row.return_value = None
    registry.security.load.return_value = SimpleNamespace(hitl=SimpleNamespace(enabled=False))
    server = SimpleNamespace(
        app_runtime=SimpleNamespace(agent_registry=registry),
        expert_catalog=object(),
    )

    result = await routes.get_chat_welcome("agent-1", user=object(), server=server)

    assert result["hitl_enabled"] is False
    assert result["task_examples"] is None
