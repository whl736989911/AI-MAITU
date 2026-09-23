"""The turn path supplies a feature's workflow and locale to its model calls.

The workflow is read asynchronously here; a feature with no workflow still
passes its locale so the model can explain configuration in the user's language.
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
    CONFIGURABLE_WORKFLOW_KEY,
    FEATURE_RUN_META_KEY,
    WorkflowRunContext,
    save_workflow,
)
from octop.infra.agents.middleware.feature_workflow import CONFIGURABLE_FEATURE_LOCALE_KEY
from octop.infra.gateway.process import processor as processor_module
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher


def _definition() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "active",
        "inputs": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "title": {"zh": "客户名称", "en": "Customer"}}
            },
        },
        "steps": [{"id": "draft", "name": "起草", "prompt": "写"}],
    }


def _processor(
    monkeypatch: Any,
    row: Any,
    workspace: BackendWorkspace | None,
    overlay_repo: Any | None = None,
    run_repo: Any | None = None,
) -> GlobalProcessor:
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
    return GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(return_value=row)),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        usage_repo=None,
        gateway=None,
        feature_overlay_repo=overlay_repo,
        feature_run_repo=run_repo,
    )


def _msg(metadata: dict[str, Any] | None = None) -> InboundMessage:
    return InboundMessage(
        channel_id="ws",
        channel_type="dashboard",
        tenant_id="feat-quote-helper",
        channel_subject=ChannelSubject(subject_id="1"),
        content=[TextContent(text="▶ 运行")],
        metadata=metadata or {},
    )


async def _workspace_with_workflow(root: Path) -> BackendWorkspace:
    workspace = BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)
    await save_workflow(workspace, _definition())
    return workspace


@pytest.mark.asyncio
async def test_a_feature_turn_carries_its_workflow_and_the_submitted_values(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    processor = _processor(monkeypatch, row, workspace)
    run = {"inputs": {"customer_name": "ACME"}, "attachments": ["inbound/a.pdf"]}

    request = await processor._build_dashboard_request(
        _msg({FEATURE_RUN_META_KEY: run}),
        agent_id="feat-quote-helper",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta={FEATURE_RUN_META_KEY: run},
    )

    context = request["configurable"][CONFIGURABLE_WORKFLOW_KEY]
    assert isinstance(context, WorkflowRunContext)
    assert context.definition["steps"][0]["id"] == "draft"
    assert context.name == "报价助手"
    assert context.values == {"customer_name": "ACME"}
    assert context.attachments == ("inbound/a.pdf",)
    # The submitted values stay on the message too, so the card can be shown again.
    assert request["messages"][0].additional_kwargs[FEATURE_RUN_META_KEY] == run


@pytest.mark.asyncio
async def test_a_draft_runs_for_its_author_but_not_for_a_shared_caller(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    await save_workflow(workspace, {**_definition(), "status": "draft"})
    row = SimpleNamespace(kind="feature", name="报价助手", user_id=7)
    run_repo = MagicMock()
    processor = _processor(monkeypatch, row, workspace, run_repo=run_repo)
    submitted = {"inputs": {"customer_name": "ACME"}}

    caller = await processor._feature_workflow_context(
        agent_id="feat-quote-helper",
        user_id=42,
        locale="en",
        run_payload=submitted,
        thread_id="thread",
    )
    assert caller is None
    run_repo.record.assert_not_called()

    author = await processor._feature_workflow_context(
        agent_id="feat-quote-helper",
        user_id=7,
        locale="en",
        run_payload=submitted,
        thread_id="thread",
    )
    assert author is not None
    assert author.definition["status"] == "draft"


@pytest.mark.asyncio
async def test_the_callers_own_overlay_rides_above_the_definition(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Read by (feature, user): the text is the caller's, not the feature's."""
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    overlay_repo = MagicMock()
    overlay_repo.content = MagicMock(return_value="我们的报价含税，不要写不含税价。")
    processor = _processor(monkeypatch, row, workspace, overlay_repo)

    request = await processor._build_dashboard_request(
        _msg(),
        agent_id="feat-quote-helper",
        user_id=42,
        session_key="sk",
        thread_id="thr",
        meta={},
    )

    context = request["configurable"][CONFIGURABLE_WORKFLOW_KEY]
    assert context.overlay == "我们的报价含税，不要写不含税价。"
    overlay_repo.content.assert_called_once_with(feature_id="quote-helper", user_id=42)


@pytest.mark.asyncio
async def test_an_overlay_store_that_cannot_answer_adds_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    overlay_repo = MagicMock()
    overlay_repo.content = MagicMock(side_effect=RuntimeError("db gone"))
    processor = _processor(monkeypatch, row, workspace, overlay_repo)

    request = await processor._build_dashboard_request(
        _msg(),
        agent_id="feat-quote-helper",
        user_id=42,
        session_key="sk",
        thread_id="thr",
        meta={},
    )

    # The run still carries what the platform *could* read: the definition stands.
    context = request["configurable"][CONFIGURABLE_WORKFLOW_KEY]
    assert context.overlay == ""
    assert context.definition["steps"][0]["id"] == "draft"


@pytest.mark.asyncio
async def test_a_submitted_run_is_recorded_with_what_it_was_given(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The evidence layer: the values the card sent, against the definition in force."""
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    run_repo = MagicMock()
    processor = _processor(monkeypatch, row, workspace, run_repo=run_repo)
    run = {"inputs": {"customer_name": "ACME"}, "attachments": ["inbound/a.pdf"]}

    await processor._build_dashboard_request(
        _msg({FEATURE_RUN_META_KEY: run}),
        agent_id="feat-quote-helper",
        user_id=42,
        session_key="sk",
        thread_id="thr-1",
        meta={FEATURE_RUN_META_KEY: run},
    )

    run_repo.record.assert_called_once()
    kwargs = run_repo.record.call_args.kwargs
    assert kwargs["feature_id"] == "quote-helper"
    assert kwargs["agent_id"] == "feat-quote-helper"
    assert kwargs["user_id"] == 42
    assert kwargs["thread_id"] == "thr-1"
    assert kwargs["inputs"] == {"customer_name": "ACME"}
    assert kwargs["definition"]["steps"][0]["id"] == "draft"


@pytest.mark.asyncio
async def test_an_ordinary_turn_is_not_a_run(tmp_path: Path, monkeypatch: Any) -> None:
    """Only a submitted card is a run; talking to the feature is not."""
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    run_repo = MagicMock()
    processor = _processor(monkeypatch, row, workspace, run_repo=run_repo)

    await processor._build_dashboard_request(
        _msg(),
        agent_id="feat-quote-helper",
        user_id=42,
        session_key="sk",
        thread_id="thr-1",
        meta={},
    )

    run_repo.record.assert_not_called()


@pytest.mark.asyncio
async def test_a_run_store_that_cannot_write_does_not_stop_the_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    run_repo = MagicMock()
    run_repo.record = MagicMock(side_effect=RuntimeError("db gone"))
    processor = _processor(monkeypatch, row, workspace, run_repo=run_repo)
    run = {"inputs": {"customer_name": "ACME"}}

    request = await processor._build_dashboard_request(
        _msg({FEATURE_RUN_META_KEY: run}),
        agent_id="feat-quote-helper",
        user_id=42,
        session_key="sk",
        thread_id="thr-1",
        meta={FEATURE_RUN_META_KEY: run},
    )

    context = request["configurable"][CONFIGURABLE_WORKFLOW_KEY]
    assert context.values == {"customer_name": "ACME"}


@pytest.mark.asyncio
async def test_an_expert_turn_carries_no_workflow_even_if_it_has_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = await _workspace_with_workflow(root)
    row = SimpleNamespace(kind="agent", name="我的专家", default_model=None)
    processor = _processor(monkeypatch, row, workspace)

    request = await processor._build_dashboard_request(
        _msg(),
        agent_id="agent-1",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta={},
    )

    assert CONFIGURABLE_WORKFLOW_KEY not in (request.get("configurable") or {})


@pytest.mark.asyncio
async def test_a_feature_without_a_workflow_still_stamps_its_locale(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    workspace = BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)
    row = SimpleNamespace(kind="feature", name="报价助手", default_model=None)
    processor = _processor(monkeypatch, row, workspace)
    monkeypatch.setattr(processor_module, "resolve_user_locale", lambda **_kwargs: "zh")

    request = await processor._build_dashboard_request(
        _msg(),
        agent_id="feat-quote-helper",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta={},
    )

    assert CONFIGURABLE_WORKFLOW_KEY not in request["configurable"]
    assert request["configurable"][CONFIGURABLE_FEATURE_LOCALE_KEY] == "zh"
