"""The turn path decides a feature's memory routing from stage and verified identity.

These tests drive ``GlobalProcessor._feature_memory_context`` (and one stamp
through a real dashboard request) so the stage × channel × author matrix stays
honest: capture rides a private namespace on active only, shared files unlock
for the verified draft author only, and IM/cron-shaped turns unlock nothing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace
from harness_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.infra.agents.feature_workflow import (
    WorkflowRunContext,
    save_workflow,
)
from octop.infra.agents.middleware.feature_memory import (
    CONFIGURABLE_FEATURE_MEMORY_KEY,
    FeatureMemoryTurnContext,
)
from octop.infra.gateway.process import processor as processor_module
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.threads import ThreadRegistry

AGENT = "feat-quote-helper"
OWNER_ID = 7
CALLER_ID = 42


def _row(kind: str = "feature", user_id: int | None = OWNER_ID) -> Any:
    return SimpleNamespace(kind=kind, name="报价助手", default_model=None, user_id=user_id)


def _processor(monkeypatch: Any, workspace: BackendWorkspace | None) -> GlobalProcessor:
    agent_manager = MagicMock()
    agent_manager.providers = MagicMock()
    agent_manager.providers.is_model_ref_usable = MagicMock(return_value=False)
    agent_manager.get_thread_model = MagicMock(return_value=None)
    monkeypatch.setattr(
        processor_module, "harness_workspace_for_agent", lambda _mgr, _aid: workspace
    )
    return GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=None,
        gateway=None,
    )


def _workflow(definition: dict[str, Any] | None) -> WorkflowRunContext | None:
    if definition is None:
        return None
    return WorkflowRunContext(definition=definition, name="报价助手")


def _ctx(processor: GlobalProcessor, **kwargs: Any) -> FeatureMemoryTurnContext | None:
    return processor._feature_memory_context(agent_id=AGENT, row=kwargs.pop("row"), **kwargs)


async def _workspace_with_status(root: Path, status: str) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    workspace = BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)
    await save_workflow(workspace, {"version": 1, "status": status})
    return workspace


@pytest.mark.asyncio
async def test_active_caller_gets_only_their_own_private_namespace(
    tmp_path: Path, monkeypatch: Any
) -> None:
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "active"))
    context = await _ctx(
        processor,
        row=_row(),
        user_id=CALLER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=_workflow({"status": "active"}),
    )
    assert context is not None
    assert context.private_namespace == f"agent_{AGENT}_user_{CALLER_ID}"
    assert context.capture_namespace() == f"agent_{AGENT}_user_{CALLER_ID}"
    assert context.shared_writable is False
    assert context.recall_namespaces() == (f"agent_{AGENT}", f"agent_{AGENT}_user_{CALLER_ID}")


@pytest.mark.asyncio
async def test_active_author_is_frozen_out_of_shared_too(tmp_path: Path, monkeypatch: Any) -> None:
    """Publishing freezes the corpus for its own author; they get a private one."""
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "active"))
    context = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=_workflow({"status": "active"}),
    )
    assert context is not None
    assert context.shared_writable is False
    assert context.private_namespace == f"agent_{AGENT}_user_{OWNER_ID}"


@pytest.mark.asyncio
async def test_draft_author_on_dashboard_unlocks_manual_training_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "draft"))
    context = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=_workflow({"status": "draft"}),
    )
    assert context is not None
    assert context.shared_writable is True
    # The unlock is for manual file edits — conversation still captures nowhere.
    assert context.capture_namespace() is None
    assert context.private_namespace is None


@pytest.mark.asyncio
async def test_im_turn_unlocks_nothing_even_for_the_author(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """IM identity falls back to the owner, so it is never a verified trainer."""
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "draft"))
    draft = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type="wechat",
        workflow_context=_workflow({"status": "draft"}),
    )
    assert draft is not None
    assert draft.shared_writable is False
    assert draft.private_namespace is None

    active = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type="wechat",
        workflow_context=_workflow({"status": "active"}),
    )
    assert active is not None
    assert active.private_namespace is None
    assert active.shared_writable is False
    assert active.capture_namespace() is None


@pytest.mark.asyncio
async def test_draft_caller_who_is_not_the_author_unlocks_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "draft"))
    context = await _ctx(
        processor,
        row=_row(),
        user_id=CALLER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=_workflow({"status": "draft"}),
    )
    assert context is not None
    assert context.shared_writable is False
    assert context.private_namespace is None


@pytest.mark.asyncio
async def test_without_workflow_context_the_stage_is_reread(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A draft shown without run context still freezes files for non-trainers."""
    workspace = await _workspace_with_status(tmp_path / "ws", "draft")
    processor = _processor(monkeypatch, workspace)
    author = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=None,
    )
    assert author is not None and author.shared_writable is True
    caller = await _ctx(
        processor,
        row=_row(),
        user_id=CALLER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=None,
    )
    assert caller is not None and caller.shared_writable is False


@pytest.mark.asyncio
async def test_an_unreadable_definition_unlocks_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """Fail closed: a broken workflow.json must not become a write window."""
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(root), virtual_mode=False),
        root,
    )
    await workspace.awrite_text(".octop/workflow.json", "{not json", force=True)
    processor = _processor(monkeypatch, workspace)
    context = await _ctx(
        processor,
        row=_row(),
        user_id=OWNER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=None,
    )
    assert context is not None
    assert context.stage is None
    assert context.shared_writable is False
    assert context.private_namespace is None


@pytest.mark.asyncio
async def test_an_expert_turn_carries_no_memory_routing(tmp_path: Path, monkeypatch: Any) -> None:
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "active"))
    context = await _ctx(
        processor,
        row=_row(kind="agent"),
        user_id=CALLER_ID,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=None,
    )
    assert context is None


@pytest.mark.asyncio
async def test_unverified_zero_id_unlocks_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    processor = _processor(monkeypatch, await _workspace_with_status(tmp_path / "ws", "active"))
    context = await _ctx(
        processor,
        row=_row(),
        user_id=0,
        channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
        workflow_context=_workflow({"status": "active"}),
    )
    assert context is not None
    assert context.private_namespace is None
    assert context.shared_writable is False


@pytest.mark.asyncio
async def test_the_routing_rides_the_dashboard_request_configurable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = await _workspace_with_status(tmp_path / "ws", "active")
    agent_manager = MagicMock()
    agent_manager.providers = MagicMock()
    agent_manager.providers.is_model_ref_usable = MagicMock(return_value=False)
    agent_manager.providers.resolve_explicit_default_model = MagicMock(return_value=None)
    agent_manager.providers.resolve_model_for_multimodal_turn = MagicMock(
        side_effect=lambda ref, **_k: ref
    )
    agent_manager.get_thread_model = MagicMock(return_value=None)
    agent_manager.default_mcp_servers = MagicMock(return_value=[])
    agent_manager.default_knowledge_base_ids = MagicMock(return_value=[])
    agent_manager.prepare_chat_mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(
        processor_module, "harness_workspace_for_agent", lambda _mgr, _aid: workspace
    )
    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(return_value=_row())),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=None,
        gateway=None,
    )
    request = await processor._build_dashboard_request(
        InboundMessage(
            channel_id="ws",
            channel_type=ThreadRegistry.CHANNEL_DASHBOARD,
            tenant_id=AGENT,
            channel_subject=ChannelSubject(subject_id=str(CALLER_ID)),
            content=[TextContent(text="hello")],
            metadata={},
        ),
        agent_id=AGENT,
        user_id=CALLER_ID,
        session_key="sk",
        thread_id="thr",
        meta={},
    )
    context = (request.get("configurable") or {}).get(CONFIGURABLE_FEATURE_MEMORY_KEY)
    assert isinstance(context, FeatureMemoryTurnContext)
    assert context.private_namespace == f"agent_{AGENT}_user_{CALLER_ID}"
