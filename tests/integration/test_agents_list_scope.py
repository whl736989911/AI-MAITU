"""Agent list user isolation."""

from __future__ import annotations

import pytest

from tests.support.auth import create_user


@pytest.fixture
async def env(env_with_provider):
    yield env_with_provider


async def test_list_agents_default_scope_is_mine_only(env) -> None:
    c, _srv, admin_auth = env
    guest_auth = await create_user(c, admin_auth, username="guest_iso")
    jubaoliang_auth = await create_user(c, admin_auth, username="jubaoliang_iso")

    r = await c.post(
        "/api/agents/from-expert/default",
        headers=guest_auth,
        json={"name": "guest-bot"},
    )
    assert r.status_code == 201, r.text
    guest_agent_id = r.json()["agent_id"]

    r = await c.post(
        "/api/agents/from-expert/default",
        headers=jubaoliang_auth,
        json={"name": "juba-bot"},
    )
    assert r.status_code == 201, r.text

    r = await c.get("/api/agents", headers=jubaoliang_auth)
    assert r.status_code == 200
    rows = r.json()
    ids = {row["agent_id"] for row in rows}
    assert guest_agent_id not in ids
    assert all(row.get("user_id") is not None for row in rows)


async def test_admin_scope_all_lists_every_user(env) -> None:
    c, _srv, admin_auth = env
    guest_auth = await create_user(c, admin_auth, username="guest_all")
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=guest_auth,
        json={"name": "guest-all-bot"},
    )
    assert r.status_code == 201, r.text
    guest_agent_id = r.json()["agent_id"]

    r = await c.get("/api/agents?scope=all", headers=admin_auth)
    assert r.status_code == 200
    ids = {row["agent_id"] for row in r.json()}
    assert guest_agent_id in ids


async def test_non_admin_cannot_use_scope_all(env) -> None:
    c, _srv, admin_auth = env
    user_auth = await create_user(c, admin_auth, username="scope_user")
    r = await c.get("/api/agents?scope=all", headers=user_auth)
    assert r.status_code == 403


async def test_non_admin_cannot_get_other_users_agent(env) -> None:
    c, _srv, admin_auth = env
    guest_auth = await create_user(c, admin_auth, username="guest_get")
    other_auth = await create_user(c, admin_auth, username="other_get")
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=guest_auth,
        json={"name": "guest-only"},
    )
    assert r.status_code == 201, r.text
    guest_agent_id = r.json()["agent_id"]

    r = await c.get(f"/api/agents/{guest_agent_id}", headers=other_auth)
    assert r.status_code == 403


async def test_owner_can_delete_own_agent(env) -> None:
    c, _srv, admin_auth = env
    owner_auth = await create_user(c, admin_auth, username="owner_del")
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=owner_auth,
        json={"name": "deletable-bot"},
    )
    assert r.status_code == 201, r.text
    agent_id = r.json()["agent_id"]

    r = await c.delete(f"/api/agents/{agent_id}", headers=owner_auth)
    assert r.status_code == 204

    r = await c.get(f"/api/agents/{agent_id}", headers=owner_auth)
    assert r.status_code == 404


async def test_non_admin_cannot_delete_shared_agent(env) -> None:
    c, srv, admin_auth = env
    user_auth = await create_user(c, admin_auth, username="shared_guard")
    agent_id = "shared-del-test"
    srv.services.agent_repo.create(agent_id=agent_id, user_id=None, name="shared")

    r = await c.delete(f"/api/agents/{agent_id}", headers=user_auth)
    assert r.status_code == 403

    r = await c.delete(f"/api/agents/{agent_id}", headers=admin_auth)
    assert r.status_code == 204


async def test_regular_user_can_create_and_reload_agent(env) -> None:
    c, _srv, admin_auth = env
    user_auth = await create_user(c, admin_auth, username="agent_owner")
    r = await c.post(
        "/api/agents",
        headers=user_auth,
        json={"name": "user-bot", "config": {}},
    )
    assert r.status_code == 201, r.text
    agent_id = r.json()["agent_id"]

    r = await c.post(f"/api/agents/{agent_id}/reload", headers=user_auth)
    assert r.status_code == 204

    r = await c.post(f"/api/agents/{agent_id}/start", headers=user_auth)
    assert r.status_code == 204


async def test_agent_kinds_are_readable_without_the_deployment_list_scope(env) -> None:
    """Which kinds exist is the deployment's, and is read without the list's permission.

    The agent list is what the caller may see, so a caller without the ``users``
    permission decides from a subset: here their own list holds no feature at all,
    while the deployment does. A surface drawing one branch per kind has to get
    the deployment's answer, or it hides a branch that has rows in it.
    """
    c, _srv, admin_auth = env
    caller_auth = await create_user(c, admin_auth, username="kinds_caller")

    r = await c.post("/api/agents", headers=admin_auth, json={"name": "kinds-expert"})
    assert r.status_code == 201, r.text
    r = await c.post(
        "/api/features",
        headers=admin_auth,
        json={"feature_id": "kinds-feature", "name": "kinds feature"},
    )
    assert r.status_code == 201, r.text

    # The deployment's own list stays the permission's to read …
    r = await c.get("/api/agents?scope=all", headers=caller_auth)
    assert r.status_code == 403
    r = await c.get("/api/agents", headers=caller_auth)
    assert r.status_code == 200
    assert "feature" not in {row["kind"] for row in r.json()}

    # … and the kinds the deployment holds are not.
    r = await c.get("/api/agents/kinds", headers=caller_auth)
    assert r.status_code == 200
    assert r.json() == {"kinds": ["agent", "feature"]}


async def test_agent_kinds_count_only_enabled_rows(env) -> None:
    """A disabled row is not "the deployment holds this kind" — the list it is read from is enabled-only."""
    c, srv, admin_auth = env
    r = await c.post("/api/agents", headers=admin_auth, json={"name": "kinds-enabled"})
    assert r.status_code == 201, r.text
    r = await c.post(
        "/api/features",
        headers=admin_auth,
        json={"feature_id": "kinds-disabled", "name": "kinds disabled"},
    )
    assert r.status_code == 201, r.text

    r = await c.get("/api/agents/kinds", headers=admin_auth)
    assert "feature" in r.json()["kinds"]

    srv.services.agent_repo.set_enabled("feat-kinds-disabled", False)
    r = await c.get("/api/agents/kinds", headers=admin_auth)
    assert "feature" not in r.json()["kinds"]
    assert "agent" in r.json()["kinds"]
