"""Team hosts retain their workspace roster across API reads and rejected edits."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.support.auth import create_agent, create_user


async def test_team_roster_persists_and_rejects_single_member(
    env_with_provider: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env_with_provider
    first = await create_agent(client, auth, name="team-member-a")
    second = await create_agent(client, auth, name="team-member-b")

    created = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "coordinators", "member_ids": [first, second]},
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["team_id"]
    assert created.json()["member_ids"] == [first, second]

    rejected = await client.patch(
        f"/api/teams/{team_id}",
        headers=auth,
        json={"member_ids": [first]},
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "TEAM_MEMBERS_TOO_FEW"

    after = await client.get(f"/api/teams/{team_id}", headers=auth)
    assert after.status_code == 200, after.text
    assert after.json()["member_ids"] == [first, second]
    workspace = server.app_runtime.agent_registry.workspace_for_agent(team_id)
    manifest = json.loads(await workspace.aread_text(".maitu/manifest.json"))
    assert manifest["members"] == [first, second]


async def test_private_team_and_members_remain_owner_scoped(
    env_with_provider: tuple[Any, Any, dict[str, str]],
) -> None:
    client, _server, admin_auth = env_with_provider
    alice = await create_user(
        client,
        admin_auth,
        username="team-alice",
        permissions=["experts", "teams"],
    )
    bob = await create_user(
        client,
        admin_auth,
        username="team-bob",
        permissions=["experts", "teams"],
    )
    first = await create_agent(client, alice, name="private-a")
    second = await create_agent(client, alice, name="private-b")
    created = await client.post(
        "/api/teams",
        headers=alice,
        json={"name": "private-team", "member_ids": [first, second]},
    )
    assert created.status_code == 201, created.text

    foreign = await client.get(f"/api/teams/{created.json()['team_id']}", headers=bob)
    assert foreign.status_code == 403
    forged = await client.post(
        "/api/teams",
        headers=bob,
        json={"name": "borrowed-members", "member_ids": [first, second]},
    )
    assert forged.status_code == 403


async def test_team_api_requires_teams_and_effective_experts_permission(
    env_with_provider: tuple[Any, Any, dict[str, str]],
) -> None:
    client, _server, admin_auth = env_with_provider

    unit = "teams-permission-test"
    created_unit = await client.post(
        "/api/org-units",
        headers=admin_auth,
        json={"key": unit, "label_zh": "团队测试", "label_en": "Team Permission Test"},
    )
    assert created_unit.status_code == 201, created_unit.text

    users = {}
    for username, permissions in (
        ("teams-experts-only", ["experts"]),
        ("teams-key-only", ["teams"]),
        ("teams-both", ["experts", "teams"]),
        ("teams-denied-experts", ["experts", "teams"]),
        ("teams-unit-grant", ["teams"]),
    ):
        users[username] = await create_user(
            client,
            admin_auth,
            username=username,
            permissions=permissions,
            org_unit=unit if username == "teams-unit-grant" else None,
        )

    ids_response = await client.get("/api/users", headers=admin_auth)
    ids_response.raise_for_status()
    user_ids = {item["username"]: item["id"] for item in ids_response.json()}
    denied = await client.patch(
        f"/api/users/{user_ids['teams-denied-experts']}",
        headers=admin_auth,
        json={"denied_permissions": ["experts"]},
    )
    assert denied.status_code == 200, denied.text

    grant = await client.put(
        f"/api/org-units/{unit}/permissions",
        headers=admin_auth,
        json={"permissions": ["experts"]},
    )
    assert grant.status_code == 200, grant.text

    def auth(username: str) -> dict[str, str]:
        return users[username]

    assert (await client.get("/api/teams", headers=auth("teams-experts-only"))).status_code == 403
    assert (await client.get("/api/teams", headers=auth("teams-key-only"))).status_code == 403
    assert (await client.get("/api/teams", headers=auth("teams-both"))).status_code == 200
    # Explicitly denied experts cannot be recovered by the separately granted teams key.
    assert (await client.get("/api/teams", headers=auth("teams-denied-experts"))).status_code == 403
    # A unit grant may supply the prerequisite; denying it still removes team access.
    unit_member = auth("teams-unit-grant")
    assert (await client.get("/api/teams", headers=unit_member)).status_code == 200
    denied_unit = await client.patch(
        f"/api/users/{user_ids['teams-unit-grant']}",
        headers=admin_auth,
        json={"denied_permissions": ["experts"]},
    )
    assert denied_unit.status_code == 200, denied_unit.text
    assert (await client.get("/api/teams", headers=unit_member)).status_code == 403


async def test_deleting_legacy_roster_members_clears_legacy_alias(
    env_with_provider: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env_with_provider
    first = await create_agent(client, auth, name="legacy-team-member-a")
    second = await create_agent(client, auth, name="legacy-team-member-b")
    created = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "legacy-roster", "member_ids": [first, second]},
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["team_id"]

    workspace = server.app_runtime.agent_registry.workspace_for_agent(team_id)
    manifest = json.loads(await workspace.aread_text(".maitu/manifest.json"))
    manifest["member"] = [first]
    await workspace.awrite_text(
        ".maitu/manifest.json",
        json.dumps(manifest),
        force=True,
    )

    teams = server.app_runtime.agent_registry.teams
    assert await teams.adrop_member(first) == [team_id]
    assert await teams.adrop_member(second) == [team_id]

    persisted = json.loads(await workspace.aread_text(".maitu/manifest.json"))
    assert persisted["members"] == []
    assert "member" not in persisted
    assert await teams.member_ids(team_id, workspace=workspace) == []

    # Old versions could already have persisted an empty canonical roster
    # alongside the obsolete alias. Reading it must not resurrect a member.
    persisted["member"] = [first]
    await workspace.awrite_text(
        ".maitu/manifest.json",
        json.dumps(persisted),
        force=True,
    )
    assert await teams.member_ids(team_id, workspace=workspace) == []
    visible = await client.get(f"/api/teams/{team_id}", headers=auth)
    assert visible.status_code == 200, visible.text
    assert visible.json()["member_ids"] == []


@pytest.mark.parametrize("target_kind", ["member", "team"])
async def test_active_team_job_blocks_deletion_until_finished(
    env_with_provider: tuple[Any, Any, dict[str, str]], target_kind: str
) -> None:
    client, server, auth = env_with_provider
    first = await create_agent(client, auth, name="busy-team-member-a")
    second = await create_agent(client, auth, name="busy-team-member-b")
    created = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "busy-team", "member_ids": [first, second]},
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["team_id"]
    target_id = first if target_kind == "member" else team_id
    teams = server.app_runtime.agent_registry.teams
    teams.jobs.begin(team_id, first, "active-peer-job")
    try:
        rejected = await client.delete(f"/api/agents/{target_id}", headers=auth)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "TEAM_MEMBER_BUSY"
        assert server.services.agent_repo.get(target_id) is not None
        assert await teams.member_ids(team_id) == [first, second]
    finally:
        teams.jobs.end(team_id, first, "active-peer-job")

    deleted = await client.delete(f"/api/agents/{target_id}", headers=auth)
    assert deleted.status_code == 204, deleted.text
    assert server.services.agent_repo.get(target_id) is None
    if target_kind == "member":
        assert await teams.member_ids(team_id) == [second]
