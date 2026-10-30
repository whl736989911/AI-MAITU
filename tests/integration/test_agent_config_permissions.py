"""Agent create and patch enforce config-module write permissions."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def test_create_and_patch_protect_sensitive_config_but_allow_chat_edits(env: Any) -> None:
    client, server, admin_auth = env
    no_modules = await create_user(client, admin_auth, username="config_limited", permissions=[])
    denied_create = await client.post(
        "/api/agents",
        headers=no_modules,
        json={"name": "blocked-config", "config": {"tools_disabled": ["execute"]}},
    )
    assert denied_create.status_code == 403

    created = await client.post(
        "/api/agents",
        headers=no_modules,
        json={
            "name": "ordinary-config",
            "description": "initial",
            "default_model": "provider:model",
            "config": {"providers": ["provider"]},
        },
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    # Ordinary profile/chat edits are independent of module grants.
    ordinary_patch = await client.patch(
        f"/api/agents/{agent_id}",
        headers=no_modules,
        json={"name": "renamed", "description": "updated", "default_model": "other:model"},
    )
    assert ordinary_patch.status_code == 200, ordinary_patch.text
    assert ordinary_patch.json()["name"] == "renamed"
    denied_skill_create = await client.post(
        "/api/agents",
        headers=no_modules,
        json={"name": "blocked-skill-list", "skill_package_ids": ["package-1"]},
    )
    assert denied_skill_create.status_code == 403
    denied_persona_create = await client.post(
        "/api/agents",
        headers=no_modules,
        json={"name": "blocked-persona", "persona_mbti": "INTJ"},
    )
    assert denied_persona_create.status_code == 403
    denied_persona_patch = await client.patch(
        f"/api/agents/{agent_id}", headers=no_modules, json={"persona_mbti": "INTJ"}
    )
    assert denied_persona_patch.status_code == 403
    agent_after_persona_denial = await client.get(f"/api/agents/{agent_id}", headers=no_modules)
    assert agent_after_persona_denial.status_code == 200
    assert agent_after_persona_denial.json()["persona_mbti"] is None

    denied_connector_patch = await client.patch(
        f"/api/agents/{agent_id}",
        headers=no_modules,
        json={"mcp_servers": ["connector-1"]},
    )
    assert denied_connector_patch.status_code == 403
    agent_after_denial = await client.get(f"/api/agents/{agent_id}", headers=no_modules)
    assert agent_after_denial.status_code == 200, agent_after_denial.text
    assert agent_after_denial.json()["mcp_servers"] == []

    # Seed an existing protected setting as trusted service setup, then prove
    # a whole-config replacement cannot remove it before any write occurs.
    registry = server.app_runtime.agent_registry
    await registry.update_config_json(
        agent_id,
        '{"providers":["provider"],"tools_disabled":["execute"]}',
    )
    before = registry.get_config(agent_id)
    denied_remove = await client.patch(
        f"/api/agents/{agent_id}",
        headers=no_modules,
        json={"config": {"providers": ["provider"]}},
    )
    assert denied_remove.status_code == 403
    assert registry.get_config(agent_id) == before


async def test_module_holder_can_create_and_patch_protected_config(env: Any) -> None:
    client, server, admin_auth = env
    tool_user = await create_user(
        client,
        admin_auth,
        username="config_tool_holder",
        permissions=["tools", "terminal"],
    )
    created = await client.post(
        "/api/agents",
        headers=tool_user,
        json={"name": "tool-config", "config": {"tools_disabled": ["execute"]}},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    updated = await client.patch(
        f"/api/agents/{agent_id}",
        headers=tool_user,
        json={"config": {"tools_disabled": ["web_fetch"]}},
    )
    assert updated.status_code == 200, updated.text
    assert server.app_runtime.agent_registry.get_config(agent_id)["tools_disabled"] == ["web_fetch"]


async def test_unchanged_protected_config_roundtrip_is_allowed(env: Any) -> None:
    client, server, admin_auth = env
    no_modules = await create_user(client, admin_auth, username="config_roundtrip", permissions=[])
    created = await client.post("/api/agents", headers=no_modules, json={"name": "roundtrip"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    registry = server.app_runtime.agent_registry
    await registry.update_config_json(
        agent_id, '{"tools_disabled":["execute"],"providers":["provider"]}'
    )
    before_config = registry.get_config(agent_id)
    public_response = await client.get(f"/api/agents/{agent_id}", headers=no_modules)
    assert public_response.status_code == 200, public_response.text
    current_config = public_response.json()["config"]

    response = await client.patch(
        f"/api/agents/{agent_id}", headers=no_modules, json={"config": current_config}
    )
    assert response.status_code == 200, response.text
    assert registry.get_config(agent_id) == before_config


async def test_plugin_settings_require_plugin_permission_before_tool_write(env: Any) -> None:
    client, server, admin_auth = env
    tools_only = await create_user(
        client, admin_auth, username="config_tools_only", permissions=["tools"]
    )
    created = await client.post("/api/agents", headers=tools_only, json={"name": "plugin-config"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    response = await client.put(
        f"/api/agents/{agent_id}/tool-settings",
        headers=tools_only,
        json={
            "disabled_builtin": ["execute"],
            "plugins": {"sample": {"tools": {"sample_tool": {"enabled": False}}}},
        },
    )
    assert response.status_code == 403
    assert server.app_runtime.agent_registry.get_config(agent_id).get("tools_disabled", []) == []


async def test_tools_manager_cannot_enable_terminal_without_terminal_permission(env: Any) -> None:
    client, server, admin_auth = env
    tools_only = await create_user(
        client, admin_auth, username="config_tools_no_terminal", permissions=["tools"]
    )
    created = await client.post(
        "/api/agents",
        headers=tools_only,
        json={"name": "terminal-toggle", "config": {"tools_disabled": ["execute"]}},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    response = await client.patch(
        f"/api/agents/{agent_id}/tool-settings/execute",
        headers=tools_only,
        json={"enabled": True, "source": "builtin"},
    )
    assert response.status_code == 403
    assert server.app_runtime.agent_registry.get_config(agent_id)["tools_disabled"] == ["execute"]


async def test_expert_creation_options_require_corresponding_modules(env: Any) -> None:
    client, _server, admin_auth = env
    experts_only = await create_user(
        client, admin_auth, username="config_expert_creator", permissions=["experts"]
    )
    for body in (
        {"name": "expert-skill", "skill_package_ids": ["package-1"]},
        {"name": "expert-connector", "mcp_servers": ["connector-1"]},
        {"name": "expert-backend", "backend": {"type": "local_shell"}},
    ):
        response = await client.post(
            "/api/agents/from-expert/default", headers=experts_only, json=body
        )
        assert response.status_code == 403, response.text


async def test_clearing_persona_requires_permission_and_preserves_the_previous_value(
    env: Any,
) -> None:
    client, server, admin_auth = env
    auth = await create_user(client, admin_auth, username="persona_limited", permissions=[])
    created = await client.post("/api/agents", headers=auth, json={"name": "persona-limited"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    registry = server.app_runtime.agent_registry
    await registry.update(agent_id, persona_mbti="INTJ")

    response = await client.patch(
        f"/api/agents/{agent_id}", headers=auth, json={"persona_mbti": None}
    )

    assert response.status_code == 403
    assert registry.get_row(agent_id).persona_mbti == "INTJ"


async def test_numeric_false_cannot_reenable_search_without_browser_permission(env: Any) -> None:
    client, server, admin_auth = env
    auth = await create_user(client, admin_auth, username="search_limited", permissions=[])
    created = await client.post("/api/agents", headers=auth, json={"name": "search-limited"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    registry = server.app_runtime.agent_registry
    await registry.update_config_json(agent_id, '{"web_search_tools": false}')
    replacement = dict(registry.get_config(agent_id))
    replacement["web_search_tools"] = 0

    response = await client.patch(
        f"/api/agents/{agent_id}", headers=auth, json={"config": replacement}
    )

    assert response.status_code == 403
    assert registry.get_config(agent_id)["web_search_tools"] is False
