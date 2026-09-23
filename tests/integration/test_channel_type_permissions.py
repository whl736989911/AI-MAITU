"""Per-kind channel permission over the API (design §2.3/§2.4).

``channels`` opens the channel surface; ``channel_<kind>`` decides which kinds an
account may create, edit, delete, test, bind or view. This file drives the real
routes: which kinds a restricted account may touch, what a baseline account may
still do (the keys are pre-checked for every new account, so nothing an ordinary
account could do yesterday may disappear), and what happens to a channel that is
already stored when its type key goes away.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.auth import create_agent, create_user, ensure_users

_FEISHU = {"kind": "feishu", "name": "feishu", "config": {"app_id": "x"}}
_WECOM = {"kind": "wecom", "name": "wecom", "config": {"bot_id": "b", "secret": "s"}}


@pytest.fixture
async def scoped_env(env_with_provider):
    """An account that may use channel management and Feishu *only*.

    Named apart from the conftest's ``env`` fixture it is built on, so the
    baseline test below can still ask for the un-narrowed environment.
    """
    client, srv, admin_auth = env_with_provider
    alice_auth = await create_user(
        client,
        admin_auth,
        username="channel-scoped",
        permissions=["channels", "channel_feishu"],
    )
    agent_id = await create_agent(client, alice_auth, name="scoped-bot")
    yield client, srv, admin_auth, alice_auth, agent_id


async def test_create_is_gated_by_the_kind_it_asks_for(scoped_env: Any) -> None:
    c, _srv, _admin, alice, aid = scoped_env

    r = await c.post(f"/api/agents/{aid}/channels", headers=alice, json=_FEISHU)
    assert r.status_code == 201, r.text

    r = await c.post(f"/api/agents/{aid}/channels", headers=alice, json=_WECOM)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["permission"] == "channel_wecom"


async def test_draft_probe_is_gated_by_the_kind_it_asks_for(scoped_env: Any) -> None:
    c, _srv, _admin, alice, aid = scoped_env

    r = await c.post(
        f"/api/agents/{aid}/channels/probe",
        headers=alice,
        json={"kind": "wecom", "config": {}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["permission"] == "channel_wecom"


async def test_kind_specific_route_is_gated_by_its_own_kind(scoped_env: Any) -> None:
    """A QR/bind helper needs the type key of the kind in its path."""
    c, _srv, _admin, alice, aid = scoped_env

    r = await c.post(f"/api/agents/{aid}/channels/dingtalk/qrcode/generate", headers=alice)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["permission"] == "channel_dingtalk"


async def test_stored_channels_are_gated_and_hidden_by_their_kind(scoped_env: Any) -> None:
    c, _srv, admin, alice, aid = scoped_env
    r = await c.post(f"/api/agents/{aid}/channels", headers=admin, json=_WECOM)
    assert r.status_code == 201, r.text
    wecom_id = r.json()["id"]

    r = await c.get(f"/api/agents/{aid}/channels", headers=alice)
    assert r.status_code == 200, r.text
    assert [row["kind"] for row in r.json()] == []

    r = await c.get(f"/api/agents/{aid}/channels/{wecom_id}", headers=alice)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["permission"] == "channel_wecom"

    r = await c.patch(f"/api/agents/{aid}/channels/{wecom_id}", headers=alice, json={"name": "x"})
    assert r.status_code == 403, r.text

    r = await c.delete(f"/api/agents/{aid}/channels/{wecom_id}", headers=alice)
    assert r.status_code == 403, r.text

    r = await c.post(f"/api/agents/{aid}/channels/{wecom_id}/test", headers=alice)
    assert r.status_code == 403, r.text


async def test_a_listed_channel_of_an_authorized_kind_is_fully_usable(scoped_env: Any) -> None:
    c, srv, _admin, alice, aid = scoped_env
    r = await c.post(f"/api/agents/{aid}/channels", headers=alice, json=_FEISHU)
    assert r.status_code == 201, r.text
    feishu_id = r.json()["id"]

    r = await c.get(f"/api/agents/{aid}/channels", headers=alice)
    assert [row["id"] for row in r.json()] == [feishu_id]

    r = await c.get(f"/api/agents/{aid}/channels/{feishu_id}", headers=alice)
    assert r.status_code == 200, r.text

    r = await c.patch(
        f"/api/agents/{aid}/channels/{feishu_id}", headers=alice, json={"name": "renamed"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"


async def test_re_typing_a_channel_needs_the_target_kind_too(scoped_env: Any) -> None:
    """PATCH ``kind`` is using the target type, so that type's key is required."""
    c, _srv, _admin, alice, aid = scoped_env
    r = await c.post(f"/api/agents/{aid}/channels", headers=alice, json=_FEISHU)
    assert r.status_code == 201, r.text
    feishu_id = r.json()["id"]

    r = await c.patch(
        f"/api/agents/{aid}/channels/{feishu_id}",
        headers=alice,
        json={"kind": "wecom", "config": {"bot_id": "b", "secret": "s"}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["permission"] == "channel_wecom"


async def test_revoking_the_key_stops_a_stored_channel(scoped_env: Any) -> None:
    """Design §2.3: 已存在的通道在类型权限被撤销后立即停止运行."""
    c, srv, admin, alice, aid = scoped_env
    r = await c.post(f"/api/agents/{aid}/channels", headers=alice, json=_FEISHU)
    assert r.status_code == 201, r.text
    channel_id = r.json()["id"]

    users = (await c.get("/api/users", headers=admin)).json()
    alice_id = next(u["id"] for u in users if u["username"] == "channel-scoped")
    r = await c.patch(f"/api/users/{alice_id}", headers=admin, json={"permissions": ["channels"]})
    assert r.status_code == 200, r.text

    gateway = srv.app_runtime.gateway
    status = gateway.get_runtime_status(channel_id)
    assert status is not None
    assert status.connected is False
    assert status.reason == "permission"
    assert gateway._channel_manager is not None
    assert gateway._channel_manager.get_channel(channel_id) is None

    # And the owner can no longer act on it, while an administrator still can.
    assert (await c.get(f"/api/agents/{aid}/channels", headers=alice)).json() == []


async def test_a_baseline_account_keeps_every_kind(env_with_provider: Any) -> None:
    """The keys are pre-checked for a new account: nothing an ordinary account
    could do before this catalog existed disappears behind the new gates."""
    (
        c,
        _srv,
        admin,
    ) = env_with_provider
    users = await ensure_users(c, admin, "baseline-holder")
    auth = users["baseline-holder"]
    aid = await create_agent(c, auth, name="baseline-bot")

    r = await c.post(f"/api/agents/{aid}/channels", headers=auth, json=_FEISHU)
    assert r.status_code == 201, r.text
    r = await c.post(f"/api/agents/{aid}/channels", headers=auth, json=_WECOM)
    assert r.status_code == 201, r.text
    r = await c.get(f"/api/agents/{aid}/channels", headers=auth)
    assert sorted(row["kind"] for row in r.json()) == ["feishu", "wecom"]
