"""Creating a feature creates its agent — one row, owned by its author, marked.

The feature model has no table of its own: a feature *is* an agent, so the two
things worth pinning here are the two facts that make it a feature rather than an
expert — the row's ``kind`` and who owns it. Everything else about it is the agents
API's, and is tested there.
"""

from __future__ import annotations

import pytest

from tests.support.auth import create_user


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

    # Its memory is written by nobody: the author is refused too.
    response = await client.post(
        "/api/agents/feat-shared-tool/memory/atoms",
        headers=author_auth,
        json={"assertion": "should not be writable"},
    )
    assert response.status_code == 403, response.text
