"""Channel-type permission at runtime (design §2.3/§4.5).

``channel_<kind>`` is not only a gate on the routes that configure a channel:
撤销通道类型权限后，已有通道停止收发消息. These tests drive the three runtime places
that have to hold that line — what may be registered, what may keep receiving,
and what may still be sent — against a real database, so permission resolution
(role ∪ department ∪ grant − deny) is the production one.

The channel manager is faked with the behaviour the gateway depends on — a
channel is live between ``add_channel`` and ``remove_channel`` — because what is
under test is which of those two calls the gateway makes, and when.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from harness_gateway.channel import MessageProcessor
from harness_gateway.models import (
    ChannelSubject,
    InboundMessage,
    MessageEvent,
    MessageEventType,
    TextContent,
)

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.channels import ChannelRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.gateway import Gateway
from octop.infra.utils.paths import PathLayout

_AGENT = "agent1"
_CHANNEL = "ch-feishu"


class _FakeChannelManager:
    """Just the channel registry: live iff ``add_channel`` ran and no removal."""

    def __init__(self) -> None:
        self.live: dict[str, MagicMock] = {}
        self.added: list[str] = []
        self.removed: list[str] = []
        self.push_text = AsyncMock()

    async def add_channel(self, *_args: object, **kwargs: object) -> None:
        channel_id = str(kwargs["channel_id"])
        self.live[channel_id] = MagicMock()
        self.added.append(channel_id)

    async def remove_channel(self, channel_id: str) -> None:
        self.live.pop(channel_id, None)
        self.removed.append(channel_id)

    def get_channel(self, channel_id: str) -> MagicMock | None:
        return self.live.get(channel_id)


def _gateway(tmp_path: Path, *, owner_permissions: list[str]) -> tuple[Gateway, int]:
    """A gateway with one owner, one agent and one stored Feishu channel."""
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    services = build_shared_services(db=db, paths=PathLayout(tmp_path), config=OctopConfig())
    owner_id = UserRepo(db).create(
        username="owner",
        password_hash="h",
        role="user",
        permissions=owner_permissions,
    )
    AgentRepo(db).create(agent_id=_AGENT, user_id=owner_id, name="bot")
    ChannelRepo(db).create(
        channel_id=_CHANNEL,
        agent_id=_AGENT,
        user_id=owner_id,
        kind="feishu",
        name="feishu",
        config_json='{"app_id":"x","app_secret":"y"}',
    )
    gw = Gateway(agent_manager=MagicMock(), repos=services.repos)
    gw._channel_manager = _FakeChannelManager()
    gw._processor = MagicMock()
    return gw, owner_id


def _manager(gw: Gateway) -> _FakeChannelManager:
    manager = gw._channel_manager
    assert isinstance(manager, _FakeChannelManager)
    return manager


def _row(gw: Gateway):
    row = gw._repos.channel_repo.get(_CHANNEL)
    assert row is not None
    return row


def _inbound(text: str) -> InboundMessage:
    return InboundMessage(
        channel_id=_CHANNEL,
        channel_type="feishu",
        content=[TextContent(text=text)],
    )


@pytest.mark.asyncio
async def test_channel_without_its_type_key_never_starts(tmp_path: Path) -> None:
    """An account that lost ``channel_feishu`` does not get a live Feishu channel."""
    gw, _owner = _gateway(tmp_path, owner_permissions=["channels", "channel_wecom"])

    await gw._start_channel(_row(gw))

    assert _manager(gw).added == []
    status = gw.get_runtime_status(_CHANNEL)
    assert status is not None
    assert status.connected is False
    assert status.reason == "permission"
    rendered = gw.runtime_status_to_dict(_CHANNEL, locale="zh")
    assert rendered is not None
    assert rendered["error"]


@pytest.mark.asyncio
async def test_channel_with_its_type_key_starts(tmp_path: Path) -> None:
    gw, _owner = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])

    await gw._start_channel(_row(gw))

    assert _manager(gw).added == [_CHANNEL]
    status = gw.get_runtime_status(_CHANNEL)
    assert status is not None
    assert status.connected is True


@pytest.mark.asyncio
async def test_revoking_the_key_stops_a_running_channel_and_restoring_starts_it(
    tmp_path: Path,
) -> None:
    """The design §2.3 lifecycle: authorized → live, revoked → stopped, back → live."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])
    await gw._start_channel(_row(gw))
    assert _manager(gw).live

    gw._repos.user_repo.set_permissions(owner_id, ["channels"])
    await gw.reconcile_channel_permissions([owner_id])

    assert _manager(gw).live == {}
    status = gw.get_runtime_status(_CHANNEL)
    assert status is not None
    assert status.connected is False
    assert status.reason == "permission"

    gw._repos.user_repo.set_permissions(owner_id, ["channels", "channel_feishu"])
    await gw.reconcile_channel_permissions([owner_id])

    assert list(_manager(gw).live) == [_CHANNEL]
    status = gw.get_runtime_status(_CHANNEL)
    assert status is not None
    assert status.connected is True


@pytest.mark.asyncio
async def test_a_deny_outranks_the_grant_and_stops_the_channel(tmp_path: Path) -> None:
    """``deny`` is part of the effective set, so it stops a channel like a revoke."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])
    await gw._start_channel(_row(gw))
    gw._repos.user_repo.set_denied_permissions(owner_id, ["channel_feishu"])

    await gw.reconcile_channel_permissions([owner_id])

    assert _manager(gw).live == {}
    status = gw.get_runtime_status(_CHANNEL)
    assert status is not None
    assert status.reason == "permission"


@pytest.mark.asyncio
async def test_reconcile_leaves_untouched_accounts_alone(tmp_path: Path) -> None:
    """A reconcile re-derives the named accounts — it is not a reload."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])
    await gw._start_channel(_row(gw))

    await gw.reconcile_channel_permissions([owner_id + 100])

    assert _manager(gw).added == [_CHANNEL]
    assert _manager(gw).removed == []


@pytest.mark.asyncio
async def test_a_department_grant_keeps_the_channel_running(tmp_path: Path) -> None:
    """The unit leg counts: a type key granted by the department is a key held."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels"])
    gw._repos.org_unit_repo.create(
        key="root", label_zh="总部", label_en="HQ", parent_key=None, sort_order=0
    )
    gw._repos.user_repo.set_org_unit(owner_id, "root")
    gw._repos.org_unit_repo.set_grants("root", ["channel_feishu"])

    await gw._start_channel(_row(gw))

    assert _manager(gw).added == [_CHANNEL]

    gw._repos.org_unit_repo.set_grants("root", [])

    assert gw.channel_refusal(_row(gw)) is not None


@pytest.mark.asyncio
async def test_inbound_message_is_refused_after_the_key_is_revoked(tmp_path: Path) -> None:
    """§4.5: 通道收发消息前检查对应 ``channel_<kind>`` — on every message, not once."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])
    seen: list[str] = []

    async def inner(message: InboundMessage):
        seen.append(message.text)
        yield MessageEvent(type=MessageEventType.MESSAGE, content=[TextContent(text="pong")])

    processor: MessageProcessor = gw._gated_processor(_row(gw), inner)
    message = _inbound("ping")

    first = [event async for event in processor(message)]
    assert [event.type for event in first] == [MessageEventType.MESSAGE]

    gw._repos.user_repo.set_permissions(owner_id, ["channels"])
    second = [event async for event in processor(message)]

    assert seen == ["ping"]  # the turn never ran a second time
    assert [event.type for event in second] == [
        MessageEventType.ERROR,
        MessageEventType.COMPLETED,
    ]
    assert second[0].error


@pytest.mark.asyncio
async def test_inbound_message_of_a_deleted_owner_is_refused(tmp_path: Path) -> None:
    """A channel whose owner is gone has nobody to hold its type key."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])

    async def inner(_message: InboundMessage):
        yield MessageEvent(type=MessageEventType.MESSAGE, content=[TextContent(text="pong")])

    processor: MessageProcessor = gw._gated_processor(_row(gw), inner)
    gw._repos.user_repo.delete(owner_id)

    events = [event async for event in processor(_inbound("ping"))]

    assert [event.type for event in events] == [
        MessageEventType.ERROR,
        MessageEventType.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_proactive_send_is_refused_after_the_key_is_revoked(tmp_path: Path) -> None:
    """§4.5: 发送入口也必须拒绝 — the push path refuses too."""
    gw, owner_id = _gateway(tmp_path, owner_permissions=["channels", "channel_feishu"])
    subject = ChannelSubject(subject_id="ou_1", chat_type="dm", metadata={})

    await gw.push_text("feishu", _CHANNEL, subject, "hello")
    _manager(gw).push_text.assert_awaited_once()

    gw._repos.user_repo.set_permissions(owner_id, ["channels"])
    _manager(gw).push_text.reset_mock()

    with pytest.raises(OctopError) as exc:
        await gw.push_text("feishu", _CHANNEL, subject, "hello")

    assert exc.value.code is ErrorCode.FORBIDDEN
    _manager(gw).push_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_virtual_channel_without_a_row_is_not_type_gated(tmp_path: Path) -> None:
    """The dashboard / cli channels have no row and no kind — they push as before."""
    gw, _owner = _gateway(tmp_path, owner_permissions=[])
    subject = ChannelSubject(subject_id="dash", chat_type="dm", metadata={})

    await gw.push_text("dashboard", "octop-dashboard", subject, "hello")

    _manager(gw).push_text.assert_awaited_once()
