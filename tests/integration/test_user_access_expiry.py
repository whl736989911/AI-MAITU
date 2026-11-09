"""Account validity gates credentials and outstanding sessions independently of disabling."""

from __future__ import annotations

import time

import pytest

from tests.support.auth import create_agent, create_user, ensure_test_org_unit, resolve_user_id
from tests.support.http import chat_ws_path, ws_connect


async def test_access_expiry_deadline_renewal_and_permanent_access(env, monkeypatch):
    client, server, admin = env
    auth = await create_user(client, admin, username="temporary")
    uid = await resolve_user_id(client, admin, "temporary")
    deadline = int(time.time()) + 60
    notice = "体验授权已到期。\n请联系管理员续期。"
    response = await client.patch(
        f"/api/users/{uid}",
        headers=admin,
        json={"access_expires_at": deadline, "access_notice": notice},
    )
    assert response.status_code == 200, response.text
    # Reload the cache from persisted rows; a restart cannot lose the deadline.
    await server.user_manager.shutdown_all()
    await server.user_manager.boot()
    assert (await client.get("/api/auth/me", headers=auth)).status_code == 200
    with monkeypatch.context() as clock:
        clock.setattr(time, "time", lambda: float(deadline))
        for path in ("/api/auth/me", "/api/agents"):
            expired = await client.get(path, headers=auth)
            assert expired.status_code == 403, expired.text
            error = expired.json()["error"]
            assert error["code"] == "ACCESS_EXPIRED"
            assert error["details"] == {"notice": notice, "expires_at": deadline}
        expired_login = await client.post(
            "/api/auth/login",
            json={"username": "temporary", "password": "TestPass12"},
        )
        assert expired_login.status_code == 403
        assert expired_login.json()["error"]["details"]["notice"] == notice
        wrong_password = await client.post(
            "/api/auth/login",
            json={"username": "temporary", "password": "wrong"},
        )
        assert wrong_password.status_code == 401
        assert notice not in wrong_password.text
        renewed = await client.patch(
            f"/api/users/{uid}",
            headers=admin,
            json={"access_expires_at": deadline + 3600},
        )
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["access_notice"] == notice
        assert (await client.get("/api/auth/me", headers=auth)).status_code == 200
        cleared = await client.patch(
            f"/api/users/{uid}",
            headers=admin,
            json={"access_expires_at": None, "access_notice": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["access_expires_at"] is None
        assert cleared.json()["access_notice"] is None
    await client.patch(f"/api/users/{uid}", headers=admin, json={"disabled": True})
    await client.patch(f"/api/users/{uid}", headers=admin, json={"access_expires_at": None})
    assert (await client.get("/api/auth/me", headers=auth)).status_code == 403


async def test_only_system_admin_can_write_other_accounts_validity(env):
    client, _server, admin = env
    enterprise = await create_user(client, admin, username="enterprise", role="enterprise_admin")
    uid = await resolve_user_id(client, admin, "enterprise")
    for field in ("access_expires_at", "access_notice"):
        response = await client.patch(
            f"/api/users/{uid}",
            headers=enterprise,
            json={field: None, "display_name": "must not commit"},
        )
        assert response.status_code == 403
    row = (await client.get(f"/api/users/{uid}", headers=admin)).json()
    assert row["display_name"] is None
    own_id = (await client.get("/api/auth/me", headers=admin)).json()["id"]
    denied = await client.patch(
        f"/api/users/{own_id}",
        headers=admin,
        json={"access_expires_at": 1},
    )
    assert denied.status_code == 403
    assert (await client.get("/api/auth/me", headers=admin)).status_code == 200


@pytest.mark.parametrize("invalid", [0, -1, 253402300800, 1.5, True, "123"])
async def test_invalid_expiry_does_not_partially_update_profile(env, invalid):
    client, _server, admin = env
    await create_user(client, admin, username="expiry_validation")
    uid = await resolve_user_id(client, admin, "expiry_validation")
    response = await client.patch(
        f"/api/users/{uid}",
        headers=admin,
        json={"access_expires_at": invalid, "display_name": "must not commit"},
    )
    assert response.status_code == 422
    row = (await client.get(f"/api/users/{uid}", headers=admin)).json()
    assert row["display_name"] is None
    assert row["access_expires_at"] is None


async def test_expired_account_created_with_notice_cannot_login(env):
    client, _server, admin = env
    unit = await ensure_test_org_unit(client, admin)
    response = await client.post(
        "/api/users",
        headers=admin,
        json={
            "username": "already_expired",
            "password": "TestPass12",
            "org_unit": unit,
            "access_expires_at": 1,
            "access_notice": "请联系交付人员。",
        },
    )
    assert response.status_code == 201, response.text
    denied = await client.post(
        "/api/auth/login",
        json={"username": "already_expired", "password": "TestPass12"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["details"]["notice"] == "请联系交付人员。"


async def test_existing_chat_connection_rechecks_account_expiry(env_with_provider):
    client, _server, admin = env_with_provider
    auth = await create_user(client, admin, username="socket_user")
    uid = await resolve_user_id(client, admin, "socket_user")
    agent_id = await create_agent(client, auth)
    async with ws_connect(client._octop_app, chat_ws_path(agent_id, auth)) as socket:
        await socket.send_json({"type": "ping"})
        assert (await socket.receive_json())["type"] == "pong"
        response = await client.patch(
            f"/api/users/{uid}",
            headers=admin,
            json={"access_expires_at": 1, "access_notice": "会话授权已到期。"},
        )
        assert response.status_code == 200, response.text
        await socket.send_json({"type": "ping"})
        error = await socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "ACCESS_EXPIRED"
        assert error["details"]["notice"] == "会话授权已到期。"
