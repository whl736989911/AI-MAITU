from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octop.api.routers.chat import routes
from octop.api.routers.chat.models import HitlResumeBody
from octop.infra.errors import ErrorCode, OctopError


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_id", [None, "old-card"])
async def test_dashboard_resume_rejects_missing_or_noncurrent_pending_id(
    pending_id: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "assert_agent_access", lambda *_args: None)
    store = MagicMock()
    store.get.return_value = None
    thread_registry = MagicMock()
    thread_registry.get_thread.return_value = None
    gateway = SimpleNamespace(
        processor=SimpleNamespace(hitl_coordinator=SimpleNamespace(store=store)),
        thread_registry=thread_registry,
    )
    server = SimpleNamespace(app_runtime=SimpleNamespace(gateway=gateway))
    body = HitlResumeBody(
        thread_id="thread-1",
        pending_id=pending_id,
        decisions=[{"type": "approve"}],
    )

    with pytest.raises(OctopError) as error:
        await routes.resume_hitl(
            "agent-1",
            body,
            MagicMock(),
            user=SimpleNamespace(id=7),
            server=server,
        )

    assert error.value.code is ErrorCode.HITL_APPROVAL_STALE
    assert error.value.status == 409


@pytest.mark.asyncio
async def test_dashboard_resume_rejects_record_from_another_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "assert_agent_access", lambda *_args: None)
    store = MagicMock()
    store.get.return_value = SimpleNamespace(
        pending_id="pending-old",
        status="pending",
        thread_id="other-thread",
        agent_id="agent-1",
        user_id=7,
        session_key="agent-1:dashboard:7:dm",
    )
    thread_registry = MagicMock()
    thread_registry.get_thread.return_value = SimpleNamespace(
        thread_id="thread-1",
        agent_id="agent-1",
        user_id=7,
        session_key="agent-1:dashboard:7:dm",
        channel_type="dashboard",
    )
    gateway = SimpleNamespace(
        processor=SimpleNamespace(hitl_coordinator=SimpleNamespace(store=store)),
        thread_registry=thread_registry,
    )
    server = SimpleNamespace(app_runtime=SimpleNamespace(gateway=gateway))
    body = HitlResumeBody(
        thread_id="thread-1",
        pending_id="pending-old",
        decisions=[{"type": "approve"}],
    )

    with pytest.raises(OctopError) as error:
        await routes.resume_hitl(
            "agent-1",
            body,
            MagicMock(),
            user=SimpleNamespace(id=7),
            server=server,
        )

    assert error.value.code is ErrorCode.HITL_APPROVAL_STALE
    assert error.value.status == 409
