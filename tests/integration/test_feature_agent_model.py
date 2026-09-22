"""A feature's own agent, end to end: who it belongs to, and what freezes it.

The rebuilt model is "a feature *is* an agent": creating a feature creates one
agent, owned by whoever defined the feature, marked as a feature's own. What that
marker buys is asserted here on a real server — the workspace memory freeze is
mounted on that agent and on no other — and so is the capability matrix over
HTTP: the author configures it, nobody at all writes its memory, and a caller who
can reach it is read-only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from octop.infra.agents.feature_agent import feature_agent_id
from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.middleware.shared_memory_freeze import (
    SharedMemoryFreezeMiddleware,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from tests.support.auth import (
    create_agent,
    ensure_users,
    resolve_user_id,
    seed_openai_provider,
)

FEATURE_ID = "weekly-report"
AGENT_ID = f"feat-{FEATURE_ID}"


@pytest.fixture
async def env_with_author(env) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, Any]]:
    """``(client, server, (admin_auth, author_auth, author_id, caller_auth))``."""
    client, srv, admin_auth = env
    await seed_openai_provider(client, admin_auth)
    auths = await ensure_users(client, admin_auth, "feature_author", "feature_caller")
    author_id = await resolve_user_id(client, admin_auth, "feature_author")
    yield client, srv, (admin_auth, auths["feature_author"], author_id, auths["feature_caller"])


async def _create_feature_agent(srv: OctopServer, author_id: int) -> Any:
    from octop.infra.agents.feature_agent import create_feature_agent

    return await create_feature_agent(
        srv,
        feature_id=FEATURE_ID,
        author_user_id=author_id,
        name="周报",
        description="把零散记录整理成周报",
        icon_name="clipboard-list",
        color="#4C7DFF",
    )


def _middleware_chain(srv: OctopServer, row: Any) -> list[Any]:
    assert srv.app_runtime is not None
    cfg = srv.app_runtime.agent_registry._build_harness_config(row)
    return list(cfg.middleware or [])


async def test_creating_a_feature_creates_its_agent_owned_by_the_author(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """The row is the record: derived id, the author as owner, and the marker."""
    _client, srv, (_admin_auth, _author_auth, author_id, _caller_auth) = env_with_author

    row = await _create_feature_agent(srv, author_id)

    assert row.agent_id == AGENT_ID == feature_agent_id(FEATURE_ID)
    assert row.user_id == author_id
    assert row.kind == KIND_FEATURE
    assert row.name == "周报"
    assert row.description == "把零散记录整理成周报"
    assert row.icon_name == "clipboard-list"
    assert row.color == "#4C7DFF"


async def test_the_freeze_is_mounted_on_a_feature_agent_and_on_no_other(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """Both directions on a real built config: feature ⇒ frozen, expert ⇒ untouched."""
    client, srv, (admin_auth, _author_auth, author_id, _caller_auth) = env_with_author
    feature_row = await _create_feature_agent(srv, author_id)
    expert_id = await create_agent(client, admin_auth, name="my own expert")
    assert srv.app_runtime is not None
    expert_row = srv.app_runtime.agent_registry.get_row(expert_id)
    assert expert_row is not None and expert_row.kind == KIND_AGENT

    feature_chain = _middleware_chain(srv, feature_row)
    expert_chain = _middleware_chain(srv, expert_row)

    assert any(isinstance(item, SharedMemoryFreezeMiddleware) for item in feature_chain)
    assert not any(isinstance(item, SharedMemoryFreezeMiddleware) for item in expert_chain)


async def test_the_author_configures_the_feature_agent_but_not_its_memory(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """The matrix over HTTP: configuration is the author's, memory is nobody's."""
    client, srv, (_admin_auth, author_auth, author_id, _caller_auth) = env_with_author
    await _create_feature_agent(srv, author_id)

    response = await client.put(
        f"/api/agents/{AGENT_ID}/tool-settings",
        headers=author_auth,
        json={"disabled_builtin": []},
    )
    assert response.status_code == 200, response.text

    response = await client.put(
        f"/api/agents/{AGENT_ID}/memory/extract-config",
        headers=author_auth,
        json={"memory_enabled": True},
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        f"/api/agents/{AGENT_ID}/memory/candidates/cand-1:promote",
        headers=author_auth,
    )
    assert response.status_code == 403, response.text
    assert "never written" in response.json()["error"]["details"]["reason"]


async def test_a_caller_reaches_a_feature_agent_but_writes_none_of_it(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """A published feature agent is readable; every write is refused with the reason."""
    client, srv, (_admin_auth, _author_auth, author_id, caller_auth) = env_with_author
    await _create_feature_agent(srv, author_id)
    assert srv.app_runtime is not None
    srv.services.repos.agent_repo.set_shared(AGENT_ID, True)

    assert (await client.get(f"/api/agents/{AGENT_ID}", headers=caller_auth)).status_code == 200

    response = await client.put(
        f"/api/agents/{AGENT_ID}/tool-settings",
        headers=caller_auth,
        json={"disabled_builtin": []},
    )
    assert response.status_code == 403, response.text
    assert "read-only" in response.json()["error"]["details"]["reason"]


async def test_a_feature_id_that_cannot_name_an_agent_says_so(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """The id rule is the manager's, and the refusal names the id it was applied to."""
    _client, srv, (_admin_auth, _author_auth, author_id, _caller_auth) = env_with_author
    from octop.infra.agents.feature_agent import create_feature_agent

    assert feature_agent_id(f"{'x' * 70}") is None
    with pytest.raises(OctopError) as excinfo:
        await create_feature_agent(
            srv, feature_id=f"{'x' * 70}", author_user_id=author_id, name="too long"
        )

    assert excinfo.value.code is ErrorCode.AGENT_ID_INVALID
    assert "cannot name an agent" in str(excinfo.value)
