"""tests/unit/test_global_processor_team.py"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from octop_harness.teams.inbox import InboxMessage
from octop_harness.teams.processor import ReplyEvent

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher
from octop.infra.gateway.threads import ThreadRegistry


@pytest.fixture
def processor_env(tmp_path: Path) -> dict[str, object]:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    UserRepo(db).create(username="u", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="parent", user_id=1, name="Parent")
    AgentRepo(db).create(agent_id="child", user_id=1, name="Researcher")

    repos = MagicMock()
    repos.session_repo = SessionRepo(db)
    repos.thread_repo = ThreadRepo(db)
    repos.channel_repo = MagicMock()
    repos.audit_repo = MagicMock()
    repos.agent_repo = AgentRepo(db)
    repos.user_repo = UserRepo(db)
    repos.connector_repo = MagicMock()

    agent_manager = MagicMock()
    agent_manager.get_row.side_effect = lambda aid: AgentRepo(db).get(aid)

    from octop.infra.gateway.gateway import Gateway

    gw = Gateway(agent_manager=agent_manager, repos=repos)
    gw._channel_manager = MagicMock()

    parent_sk = ThreadRegistry.dashboard_key(agent_id="parent", user_id=1)
    gw.thread_registry._threads.insert(
        thread_id="thr_parent",
        agent_id="parent",
        user_id=1,
        channel_type="dashboard",
        session_key=parent_sk,
    )
    gw.thread_registry._sessions.upsert(
        session_key=parent_sk,
        agent_id="parent",
        user_id=1,
        channel_type="dashboard",
        chat_type="dm",
        thread_id="thr_parent",
    )

    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=gw.thread_registry,
        audit_repo=repos.audit_repo,
        agent_repo=repos.agent_repo,
        user_repo=repos.user_repo,
        connector_repo=repos.connector_repo,
        dispatcher=SlashDispatcher(),
        gateway=gw,
    )
    return {
        "processor": processor,
        "gateway": gw,
        "parent_sk": parent_sk,
        "repos": repos,
        "db": db,
    }


def test_compose_followup_uses_peer_display_name(processor_env: dict) -> None:
    processor = processor_env["processor"]
    msg = InboxMessage(
        id="job-1",
        target_agent_id="child",
        source_agent_id="parent",
        source_thread_id="thr_parent",
        message="survey market",
        user_id=1,
        original_user_prompt="market size?",
    )
    text = processor.compose_followup(msg, result_text="findings", error_text=None)
    assert "Researcher" in text


@pytest.mark.asyncio
async def test_on_reply_increments_unread_on_dashboard(processor_env: dict) -> None:
    processor = processor_env["processor"]
    parent_sk = processor_env["parent_sk"]

    await processor.on_reply(
        ReplyEvent(
            inbox_id="job-1",
            status="done",
            source_agent_id="parent",
            source_thread_id="thr_parent",
            target_agent_id="child",
            user_id=1,
            reply_text="final synthesized reply",
            metadata={"session_key": parent_sk},
        )
    )

    session = processor_env["gateway"].thread_registry.get_session(parent_sk)  # type: ignore[attr-defined]
    assert session is not None
    assert session.unread_count == 1


@pytest.mark.asyncio
async def test_prepare_peer_session_creates_callee_thread_without_rebind(
    processor_env: dict,
) -> None:
    from octop_harness.teams.util import PeerCall

    processor = processor_env["processor"]
    parent_sk = processor_env["parent_sk"]
    prepared = await processor.prepare_peer_session(
        PeerCall(
            from_agent_id="parent",
            to_agent_id="child",
            user_id=1,
            message="hello",
            source_thread_id="thr_parent",
            source_session_key=str(parent_sk),
        )
    )
    assert prepared is not None
    assert prepared.thread_id == "thr_parent~child"
    registry = processor_env["gateway"].thread_registry
    row = registry.get_thread("thr_parent~child")
    assert row is not None
    assert row.agent_id == "child"
    assert registry.get_bound_thread_id(str(parent_sk)) == "thr_parent"


@pytest.mark.asyncio
async def test_reply_for_different_pair_does_not_release_active_team_job(
    processor_env: dict,
) -> None:
    from octop_harness.teams.util import PeerCall

    from octop.infra.agents.teams.jobs import TeamJobTracker

    processor = processor_env["processor"]
    tracker = TeamJobTracker()
    processor._agent_manager._team_jobs = tracker  # type: ignore[attr-defined]
    tracker.begin("parent", "child", job_id="job-1")

    await processor.on_reply(
        ReplyEvent(
            inbox_id="job-1",
            status="done",
            source_agent_id="parent",
            source_thread_id="thr_parent",
            target_agent_id="different-child",
            user_id=1,
            reply_text="unrelated reply",
        )
    )

    assert tracker.is_busy("parent", "child")
    processor._agent_manager.get_row.side_effect = lambda _agent_id: None  # type: ignore[attr-defined]
    processor.teams._track_job(
        PeerCall(
            from_agent_id="parent",
            to_agent_id="child",
            user_id=1,
            message="work",
            source_thread_id="thr_parent",
            source_session_key=None,
            job_id="job-1",
        ),
        begin=False,
    )
    assert not tracker.is_busy("parent", "child")


@pytest.mark.asyncio
async def test_peer_dispatch_rechecks_current_member_access(
    processor_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from octop_harness.teams.util import PeerCall

    from octop.infra.agents.kinds import KIND_TEAM
    from octop.infra.agents.teams.jobs import TeamJobTracker
    from octop.infra.db.repos.resource_acl import ResourceAclRepo
    from octop.infra.sharing import AclEntry

    processor = processor_env["processor"]
    manager = processor._agent_manager
    repos = processor_env["repos"]
    owner = repos.user_repo.create(username="member-owner", password_hash="h", role="user")
    repos.agent_repo.create(agent_id="foreign-child", user_id=owner, name="Foreign researcher")
    acl = ResourceAclRepo(processor_env["db"])
    acl.upsert(AclEntry("agent", "foreign-child", owner, "public", None, owner))
    original_get_row = manager.get_row.side_effect
    parent_row = replace(original_get_row("parent"), kind=KIND_TEAM)
    manager.get_row.side_effect = lambda agent_id: (
        parent_row if agent_id == "parent" else original_get_row(agent_id)
    )
    manager._repos = repos
    manager.teams.visible_member_ids = AsyncMock(return_value=["foreign-child"])
    manager.teams.member_ids = AsyncMock(return_value=["foreign-child"])
    monkeypatch.setattr(processor.teams, "_dispatch_message", AsyncMock(return_value="work"))
    tracker = TeamJobTracker()
    manager._team_jobs = tracker
    call = PeerCall(
        from_agent_id="parent",
        to_agent_id="foreign-child",
        user_id=1,
        message="work",
        source_thread_id="thr_parent",
        source_session_key=str(processor_env["parent_sk"]),
        job_id="job-shared",
    )
    await processor.prepare_peer_session(call)
    assert tracker.is_busy("parent", "foreign-child")
    await processor.on_reply(
        ReplyEvent(
            inbox_id="job-shared",
            status="done",
            source_agent_id="parent",
            source_thread_id="thr_parent",
            target_agent_id="foreign-child",
            user_id=1,
            reply_text="completed",
        )
    )
    assert not tracker.is_busy("parent", "foreign-child")

    acl.upsert(AclEntry("agent", "foreign-child", owner, "private", None, owner))
    with pytest.raises(OctopError) as exc_info:
        await processor.prepare_peer_session(replace(call, job_id="job-revoked"))

    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert not tracker.is_busy("parent", "foreign-child")


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
@pytest.mark.asyncio
async def test_failed_peer_session_preparation_never_leaves_job_busy(
    processor_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from octop_harness.teams.util import PeerCall

    from octop.infra.agents.kinds import KIND_TEAM
    from octop.infra.agents.teams.jobs import TeamJobTracker

    processor = processor_env["processor"]
    manager = processor._agent_manager
    original_get_row = manager.get_row.side_effect
    parent_row = replace(original_get_row("parent"), kind=KIND_TEAM)
    manager.get_row.side_effect = lambda agent_id: (
        parent_row if agent_id == "parent" else original_get_row(agent_id)
    )
    manager._repos = MagicMock()
    manager._repos.agent_repo = MagicMock()
    manager._repos.agent_repo.list_visible.return_value = [parent_row, original_get_row("child")]
    manager.teams.visible_member_ids = AsyncMock(return_value=["child"])
    manager.teams.member_ids = AsyncMock(return_value=["child"])
    tracker = TeamJobTracker()
    manager._team_jobs = tracker

    def fail_ensure_thread(**_kwargs: object) -> None:
        raise RuntimeError("thread storage unavailable")

    monkeypatch.setattr(processor.teams._thread_registry, "ensure_thread", fail_ensure_thread)
    with pytest.raises(RuntimeError, match="thread storage unavailable"):
        await processor.prepare_peer_session(
            PeerCall(
                from_agent_id="parent",
                to_agent_id="child",
                user_id=1,
                message="work",
                source_thread_id="thr_parent",
                source_session_key=str(processor_env["parent_sk"]),
                job_id="job-preparation-failure",
            )
        )

    assert tracker.is_busy("parent", "child")
    await processor.on_reply(
        ReplyEvent(
            inbox_id="job-preparation-failure",
            status=terminal_status,
            source_agent_id="parent",
            source_thread_id="thr_parent",
            target_agent_id="child",
            user_id=1,
            error_text="thread storage unavailable",
        )
    )
    assert not tracker.is_busy("parent", "child")


def test_resolve_harness_model_rejects_multimodal_turn_without_configured_model() -> None:
    processor = GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=MagicMock(),
        gateway=MagicMock(),
    )
    processor._agent_manager.get_thread_model.return_value = None
    processor._agent_repo.get.return_value = MagicMock(default_model=None)
    processor._agent_manager.get_config.return_value = {}
    processor._agent_manager.providers.resolve_explicit_default_model.return_value = None
    processor._agent_manager.providers.resolve_model_for_multimodal_turn.return_value = None

    with pytest.raises(OctopError) as exc_info:
        processor._resolve_harness_model(
            "a1",
            "t1",
            None,
            needs_multimodal=True,
        )
    assert exc_info.value.code is ErrorCode.AGENT_FAILED


def test_resolve_harness_model_uses_agent_default_and_upgrades_vision() -> None:
    processor = GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=MagicMock(),
        gateway=MagicMock(),
    )
    processor._agent_manager.get_thread_model.return_value = None
    processor._agent_repo.get.return_value = MagicMock(default_model="p/text-only")
    processor._agent_manager.get_config.return_value = {}
    processor._agent_manager.providers.resolve_explicit_default_model.return_value = "p/text-only"
    processor._agent_manager.providers.resolve_model_for_multimodal_turn.return_value = "p/vision"

    resolved = processor._resolve_harness_model(
        "a1",
        "t1",
        None,
        needs_multimodal=True,
    )
    assert resolved == "p/vision"
    processor._agent_manager.providers.resolve_model_for_multimodal_turn.assert_called_once_with(
        "p/text-only",
        needs_multimodal=True,
    )


def test_resolve_harness_model_dashboard_override_wins() -> None:
    processor = GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=MagicMock(),
        gateway=MagicMock(),
    )
    processor._agent_manager.get_thread_model.return_value = None
    processor._agent_repo.get.return_value = MagicMock(default_model="p/default")
    processor._agent_manager.providers.is_model_ref_usable.return_value = True

    resolved = processor._resolve_harness_model(
        "a1",
        "t1",
        {"model": "p/picked"},
        needs_multimodal=False,
    )
    assert resolved == "p/picked"


def test_resolve_harness_model_drops_unusable_dashboard_override() -> None:
    """Stale composer/expert refs must not bypass catalog usability checks."""
    processor = GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=MagicMock(),
        usage_repo=MagicMock(),
        gateway=MagicMock(),
    )
    processor._agent_manager.get_thread_model.return_value = None
    processor._agent_repo.get.return_value = MagicMock(default_model="p/deleted")
    processor._agent_manager.get_config.return_value = {}
    processor._agent_manager.providers.is_model_ref_usable.return_value = False
    processor._agent_manager.providers.resolve_explicit_default_model.return_value = None

    assert (
        processor._resolve_harness_model(
            "a1",
            "t1",
            {"model": "p/deleted"},
            needs_multimodal=False,
        )
        is None
    )
    processor._agent_manager.providers.resolve_model_for_multimodal_turn.assert_not_called()


def test_composer_model_precedes_sticky_and_legacy_slash_model() -> None:
    assert (
        GlobalProcessor._model_ref_from_meta(
            "p/slash",
            {"model": "p/composer"},
            "p/sticky",
        )
        == "p/composer"
    )
    assert GlobalProcessor._model_ref_from_meta("p/slash", None, "p/sticky") == "p/sticky"
