"""A feature's own agent, workflow stages, and shared/private memory boundaries.

The author trains its shared memory, publication freezes that namespace, and
each caller can write only the caller's own private memory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage

from octop.infra.agents.feature_agent import feature_agent_id
from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.middleware import feature_workflow as workflow_middleware
from octop.infra.agents.middleware.feature_workflow import FeatureWorkflowMiddleware
from octop.infra.agents.middleware.shared_workspace_freeze import (
    SharedWorkspaceFreezeMiddleware,
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

    assert any(isinstance(item, SharedWorkspaceFreezeMiddleware) for item in feature_chain)
    assert not any(isinstance(item, SharedWorkspaceFreezeMiddleware) for item in expert_chain)


async def test_a_new_feature_explains_training_before_it_has_a_workflow(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, srv, (_admin_auth, _author_auth, author_id, _caller_auth) = env_with_author
    row = await _create_feature_agent(srv, author_id)
    middleware = next(
        item for item in _middleware_chain(srv, row) if isinstance(item, FeatureWorkflowMiddleware)
    )
    monkeypatch.setattr(
        workflow_middleware,
        "get_config",
        lambda: {"configurable": {"user": str(author_id), "octop_feature_locale": "zh"}},
    )
    received: list[ModelRequest[Any]] = []

    def model_call(request: ModelRequest[Any]) -> str:
        received.append(request)
        return "ok"

    request = ModelRequest(
        model=object(),
        messages=[],
        system_message=SystemMessage(content="通用档案模板：先写 USER.md"),
    )
    middleware.wrap_model_call(request, model_call)

    assert len(received) == 1
    content = received[0].system_message.content
    assert isinstance(content, str)
    assert content.startswith("通用档案模板")
    assert content.index("1. 工作流") < content.index("9. 人设文件")
    assert "不要收集个人身份和偏好写入 USER.md" in content


async def test_feature_training_memory_and_published_caller_memory(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """Deprecation works in training; publication freezes shared memory without freezing callers."""
    pytest.importorskip("harness_memory.adapters.bridge.handlers")
    client, srv, (admin_auth, author_auth, author_id, caller_auth) = env_with_author
    await _create_feature_agent(srv, author_id)
    base = f"/api/agents/{AGENT_ID}/memory"
    author_access = await client.get(f"{base}/access", headers=author_auth)
    assert author_access.status_code == 200, author_access.text
    assert author_access.json() == {
        "stage": "draft",
        "default_scope": "shared",
        "shared_writable": True,
        "private_writable": False,
    }
    refused_private_draft = await client.post(
        f"{base}/atoms/list?scope=private",
        headers=author_auth,
        json={},
    )
    assert refused_private_draft.status_code == 403, refused_private_draft.text
    trained = await client.post(
        f"{base}/atoms",
        headers=author_auth,
        json={
            "assertion": "训练中的共享知识",
            "entity_name": "功能",
            "entity_type": "Fact",
            "kind": "Fact",
        },
    )
    assert trained.status_code == 200, trained.text
    atom_id = trained.json()["atom"]["id"]
    deprecated = await client.post(
        f"{base}/atoms/{atom_id}:deprecate",
        headers=author_auth,
        json={"reason": "训练时修正"},
    )
    assert deprecated.status_code == 200, deprecated.text
    assert deprecated.json()["status"] == "deprecated"
    trained = await client.post(
        f"{base}/atoms",
        headers=author_auth,
        json={
            "assertion": "发布后的共享知识",
            "entity_name": "功能",
            "entity_type": "Fact",
            "kind": "Fact",
        },
    )
    assert trained.status_code == 200, trained.text
    workspace_file = f"/api/agents/{AGENT_ID}/workspace/file"
    trained_file = await client.put(
        workspace_file,
        headers=author_auth,
        params={"path": "MEMORY.md", "from_workspace": True},
        json={"content": "训练时的人设共享记忆"},
    )
    assert trained_file.status_code == 200, trained_file.text

    caller_id = await resolve_user_id(client, admin_auth, "feature_caller")
    granted = await client.post(
        f"/api/sharing/acl/feature/{FEATURE_ID}",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [{"grantee_type": "user", "grantee_id": str(caller_id)}],
        },
    )
    assert granted.status_code == 200, granted.text
    assert (await client.get(f"{base}/access", headers=caller_auth)).status_code == 403
    assert (await client.get(f"{base}/daily", headers=caller_auth)).status_code == 403

    active = {
        "version": 1,
        "status": "active",
        "inputs": {
            "type": "object",
            "required": ["subject"],
            "properties": {"subject": {"type": "string", "title": {"zh": "主题", "en": "Subject"}}},
        },
        "steps": [{"id": "summarize", "name": "整理", "prompt": "整理主题"}],
        "outputs": [{"name": "总结", "form": "markdown"}],
        "rules": [],
    }
    published = await client.put(
        f"/api/agents/{AGENT_ID}/workflow",
        headers=author_auth,
        json={"workflow": active},
    )
    assert published.status_code == 200, published.text
    access = await client.get(f"{base}/access", headers=caller_auth)
    assert access.status_code == 200, access.text
    assert access.json() == {
        "stage": "active",
        "default_scope": "private",
        "shared_writable": False,
        "private_writable": True,
    }
    instruction = "只用公制单位"
    overlay_path = f"/api/agents/{AGENT_ID}/workflow/overlay"
    saved_instruction = await client.put(
        overlay_path, headers=caller_auth, json={"overlay": instruction}
    )
    assert saved_instruction.status_code == 200, saved_instruction.text
    assert saved_instruction.json()["overlay"] == instruction
    shared = await client.post(
        f"{base}/atoms/list?scope=shared",
        headers=caller_auth,
        json={},
    )
    assert shared.status_code == 200, shared.text
    assert [item["assertion"] for item in shared.json()["items"]] == ["发布后的共享知识"]
    caller_daily = await client.get(f"{base}/daily", headers=caller_auth)
    assert caller_daily.status_code == 200, caller_daily.text
    shared_file = await client.get(
        workspace_file,
        headers=caller_auth,
        params={"path": "MEMORY.md", "from_workspace": True},
    )
    assert shared_file.status_code == 200, shared_file.text
    assert shared_file.json()["content"] == "训练时的人设共享记忆"
    frozen_file = await client.put(
        workspace_file,
        headers=author_auth,
        params={"path": "MEMORY.md", "from_workspace": True},
        json={"content": "不得改写"},
    )
    assert frozen_file.status_code == 403, frozen_file.text
    frozen_wal = await client.put(
        workspace_file,
        headers=author_auth,
        params={"path": ".octop/memory.sqlite-wal", "from_workspace": True},
        json={"content": "不得改写数据库"},
    )
    assert frozen_wal.status_code == 403, frozen_wal.text
    for headers in (author_auth, admin_auth, caller_auth):
        refused = await client.post(
            f"{base}/atoms?scope=shared",
            headers=headers,
            json={"assertion": "不得修改", "entity_name": "功能"},
        )
        assert refused.status_code == 403, refused.text
    personal = await client.post(
        f"{base}/atoms",
        headers=caller_auth,
        json={
            "assertion": "只属于调用者",
            "entity_name": "我",
            "entity_type": "User",
            "kind": "Preference",
        },
    )
    assert personal.status_code == 200, personal.text
    mine = await client.post(f"{base}/atoms/list", headers=caller_auth, json={})
    assert [item["assertion"] for item in mine.json()["items"]] == ["只属于调用者"]
    author_private = await client.post(f"{base}/atoms/list", headers=author_auth, json={})
    assert author_private.status_code == 200, author_private.text
    assert author_private.json()["items"] == []
    shared_after = await client.post(
        f"{base}/atoms/list?scope=shared", headers=caller_auth, json={}
    )
    assert [item["assertion"] for item in shared_after.json()["items"]] == ["发布后的共享知识"]
    deprecated_private = await client.post(
        f"{base}/atoms/{personal.json()['atom']['id']}:deprecate",
        headers=caller_auth,
        json={"reason": "私人记忆已过期"},
    )
    assert deprecated_private.status_code == 200, deprecated_private.text
    assert deprecated_private.json()["status"] == "deprecated"
    assert (
        await client.post(
            f"{base}/atoms/list?scope=shared",
            headers=caller_auth,
            json={},
        )
    ).json()["items"][0]["assertion"] == "发布后的共享知识"
    own_instruction = await client.get(overlay_path, headers=caller_auth)
    assert own_instruction.status_code == 200, own_instruction.text
    assert own_instruction.json()["overlay"] == instruction
    assert (await client.get(overlay_path, headers=author_auth)).json()["overlay"] is None
    assert (
        await client.post(
            f"{base}/atoms/list?scope=private&as_user={caller_id}",
            headers=admin_auth,
            json={},
        )
    ).status_code == 403


async def test_a_caller_reaches_a_feature_agent_but_writes_none_of_it(
    env_with_author: tuple[httpx.AsyncClient, OctopServer, Any],
) -> None:
    """A caller can read a published feature but cannot change its tool settings."""
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
