"""GlobalProcessor preserves per-turn resource settings and preparation failures."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from octop_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher


@pytest.mark.asyncio
async def test_dashboard_request_attaches_knowledge_base_ids_without_prepending() -> None:
    agent_manager = MagicMock()
    agent_manager.merge_turn_mcp_servers = MagicMock(return_value=None)
    agent_manager.get_row = MagicMock(return_value=None)
    agent_manager.default_mcp_servers = MagicMock(return_value=[])
    agent_manager.default_knowledge_base_ids = MagicMock(return_value=[])
    agent_manager.providers = MagicMock()
    agent_manager.providers.is_model_ref_usable = MagicMock(return_value=False)
    agent_manager.providers.resolve_explicit_default_model = MagicMock(return_value=None)
    agent_manager.providers.resolve_model_for_multimodal_turn = MagicMock(
        side_effect=lambda ref, **_k: ref
    )
    agent_manager.get_thread_model = MagicMock(return_value=None)

    base = SimpleNamespace(
        id="kb-1",
        owner_user_id=1,
        name="Refund policy",
        description="Retail refund rules",
        default_open=False,
        shared=False,
    )
    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(return_value=MagicMock(default_model=None))),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        knowledge_repo=MagicMock(list_visible=MagicMock(return_value=[base])),
        settings_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        usage_repo=None,
        gateway=None,
    )
    msg = InboundMessage(
        channel_id="ws",
        channel_type="dashboard",
        tenant_id="agent-1",
        channel_subject=ChannelSubject(subject_id="1"),
        content=[TextContent(text="question")],
        metadata={"knowledge_base_ids": ["kb-1"], "user_is_admin": False},
    )

    request = await processor._build_dashboard_request(
        msg,
        agent_id="agent-1",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta=msg.metadata or {},
    )

    content = request["messages"][0]["content"]
    assert content == "question"
    configurable = request.get("configurable") or {}
    assert configurable["knowledge_base_ids"] == ["kb-1"]
    assert configurable["knowledge_base_catalog"] == [
        {
            "id": "kb-1",
            "name": "Refund policy",
            "description": "Retail refund rules",
        }
    ]
    assert configurable["user_is_admin"] is False
    assert "locale" in configurable


@pytest.mark.asyncio
async def test_resolve_turn_mcp_servers_raises_when_prepare_fails() -> None:
    agent_manager = MagicMock()
    agent_manager.merge_turn_mcp_servers = MagicMock(return_value=["bad__1"])
    agent_manager.prepare_chat_mcp = AsyncMock(return_value=["bad__1"])
    agent_manager.default_mcp_servers = MagicMock(return_value=[])

    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        usage_repo=None,
        gateway=None,
    )

    with pytest.raises(OctopError) as ei:
        await processor._resolve_turn_mcp_servers(
            agent_id="a1",
            user_id=1,
            explicit=None,
        )
    assert ei.value.code == ErrorCode.CONNECTOR_MCP_LOAD_FAILED
