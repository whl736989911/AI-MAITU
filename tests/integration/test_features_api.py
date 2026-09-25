"""Creating a feature creates its agent — one row, owned by its author, marked.

The feature model has no table of its own: a feature *is* an agent, so the two
things worth pinning here are the two facts that make it a feature rather than an
expert — the row's ``kind`` and who owns it. Everything else about it is the agents
API's, and is tested there.
"""

from __future__ import annotations

import pytest

from tests.support.auth import create_user, resolve_user_id


@pytest.fixture
async def env(env_with_provider):
    yield env_with_provider


async def _two_users(env):
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="feature_author")
    caller_auth = await create_user(client, admin_auth, username="feature_caller")
    return client, author_auth, caller_auth


def _create(client, auth, *, feature_id: str, name: str):
    return client.post(
        "/api/features",
        headers=auth,
        json={"feature_id": feature_id, "name": name},
    )


async def test_create_feature_creates_its_agent(env) -> None:
    client, author_auth, _caller_auth = await _two_users(env)

    response = await _create(client, author_auth, feature_id="weekly-report", name="周报")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["agent_id"] == "feat-weekly-report"
    assert created["kind"] == "feature"
    assert created["name"] == "周报"

    response = await client.get("/api/agents", headers=author_auth)
    assert response.status_code == 200
    rows = {row["agent_id"]: row for row in response.json()}
    assert "feat-weekly-report" in rows
    assert rows["feat-weekly-report"]["kind"] == "feature"
    # The author owns it: an IM inbound resolves the Octop user from the owner.
    assert rows["feat-weekly-report"]["is_owner"] is True

    # An expert stays an ordinary agent — the marker is what separates them.
    response = await client.post("/api/agents", headers=author_auth, json={"name": "my-expert"})
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "agent"


async def test_feature_id_must_name_an_agent(env) -> None:
    """A feature id the agent id rule refuses is reported, not silently renamed."""
    client, author_auth, _caller_auth = await _two_users(env)

    response = await _create(client, author_auth, feature_id="has spaces", name="bad id")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AGENT_ID_INVALID"


async def test_feature_id_taken_is_refused(env) -> None:
    client, author_auth, _caller_auth = await _two_users(env)

    assert (await _create(client, author_auth, feature_id="taken", name="first")).status_code == 201
    response = await _create(client, author_auth, feature_id="taken", name="second")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_ID_TAKEN"


async def test_caller_reads_the_feature_but_cannot_configure_it(env) -> None:
    """The capability matrix, seen from the API the feature's page calls."""
    client, author_auth, caller_auth = await _two_users(env)

    assert (
        await _create(client, author_auth, feature_id="shared-tool", name="共用工具")
    ).status_code == 201
    response = await client.patch(
        "/api/agents/feat-shared-tool", headers=author_auth, json={"is_shared": True}
    )
    assert response.status_code == 200, response.text

    # Readable by a caller, whoever defined it.
    response = await client.get("/api/agents/feat-shared-tool", headers=caller_auth)
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "feature"
    assert response.json()["is_owner"] is False

    # Configurable by its author.
    response = await client.patch(
        "/api/agents/feat-shared-tool", headers=author_auth, json={"description": "by author"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "by author"

    # And not by anyone else.
    response = await client.patch(
        "/api/agents/feat-shared-tool", headers=caller_auth, json={"description": "by caller"}
    )
    assert response.status_code == 403, response.text


async def _grant_feature(client, admin_auth, *, feature_id: str, grantee_id: int):
    """One directed grant on ``resource_type='feature'`` — the sharing API's own call."""
    return await client.post(
        f"/api/sharing/acl/feature/{feature_id}",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [{"grantee_type": "user", "grantee_id": str(grantee_id)}],
        },
    )


async def test_a_feature_grant_decides_the_agent_that_carries_it(env) -> None:
    """①: the ``feature`` entry is a second name for the feature's agent.

    ``resource_type='feature'`` is a type ``RESOURCE_TYPES`` advertises and the
    sharing API accepts, and no access decision read it: a grant written there
    reached nobody — the grantee got 403 on the agent that *is* that feature and
    a list without it — while the same grant on ``agent`` worked. A feature is its
    agent (:mod:`octop.infra.agents.kinds`), so both entries decide it, and
    (``resource_acl``'s own rule) grants only ever widen: the verdict is the
    union, with no precedence to get wrong.
    """
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="feature_grant_author")
    caller_auth = await create_user(client, admin_auth, username="feature_grant_caller")
    stranger_auth = await create_user(client, admin_auth, username="feature_grant_stranger")
    caller_id = await resolve_user_id(client, admin_auth, "feature_grant_caller")

    assert (
        await _create(client, author_auth, feature_id="granted-tool", name="授权工具")
    ).status_code == 201

    # Before the grant, the feature reaches nobody but its author.
    before = await client.get("/api/agents/feat-granted-tool", headers=caller_auth)
    assert before.status_code == 403, before.text

    assert (
        await _grant_feature(client, admin_auth, feature_id="granted-tool", grantee_id=caller_id)
    ).status_code == 200

    opened = await client.get("/api/agents/feat-granted-tool", headers=caller_auth)
    assert opened.status_code == 200, opened.text
    assert opened.json()["kind"] == "feature"
    assert opened.json()["is_owner"] is False

    # ③: the dashboard only draws the list, so the grant has to reach that too.
    listed = {
        row["agent_id"] for row in (await client.get("/api/agents", headers=caller_auth)).json()
    }
    assert "feat-granted-tool" in listed

    # Its author still configures it; the grant is not ownership.
    assert (
        await client.patch(
            "/api/agents/feat-granted-tool", headers=author_auth, json={"description": "by author"}
        )
    ).status_code == 200
    assert (
        await client.patch(
            "/api/agents/feat-granted-tool", headers=caller_auth, json={"description": "by grantee"}
        )
    ).status_code == 403

    # And a third account is still refused, list and detail alike.
    denied = await client.get("/api/agents/feat-granted-tool", headers=stranger_auth)
    assert denied.status_code == 403, denied.text
    assert "feat-granted-tool" not in {
        row["agent_id"] for row in (await client.get("/api/agents", headers=stranger_auth)).json()
    }


async def test_a_stranger_cannot_claim_a_feature_entry(env) -> None:
    """A feature with no entry yet belongs to its author, not to whoever asks.

    ``apply_change`` synthesizes the entry for a resource that has none, and it
    takes that entry's owner from the request. For a ``feature`` the owner is on
    the agent row instead (``feat-<feature_id>``): without that, any account
    holding ``users`` could write the first entry on somebody else's feature and —
    now that the entry decides the agent — grant itself the feature.
    """
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="feature_owner_author")
    claimer_auth = await create_user(
        client, admin_auth, username="feature_claimer", permissions=["users"]
    )
    claimer_id = await resolve_user_id(client, admin_auth, "feature_claimer")

    assert (
        await _create(client, author_auth, feature_id="owned-tool", name="有主工具")
    ).status_code == 201

    refused = await _grant_feature(
        client, claimer_auth, feature_id="owned-tool", grantee_id=claimer_id
    )
    assert refused.status_code == 403, refused.text

    # Nothing was claimed: the feature is still not theirs.
    assert (
        await client.get("/api/agents/feat-owned-tool", headers=claimer_auth)
    ).status_code == 403
    assert "feat-owned-tool" not in {
        row["agent_id"] for row in (await client.get("/api/agents", headers=claimer_auth)).json()
    }
