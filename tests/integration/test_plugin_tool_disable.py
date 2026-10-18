"""Disable shipped plugin tools via Admin Plugins API and Experts tool-settings."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def _install_weather(client: Any, auth: dict[str, str]) -> None:
    response = await client.post("/api/plugins/market/weather/install", headers=auth)
    assert response.status_code == 201, response.text


async def _create_agent(client: Any, auth: dict[str, str], name: str) -> str:
    created = await client.post("/api/agents", headers=auth, json={"name": name})
    assert created.status_code == 201, created.text
    return str(created.json()["agent_id"])


def _assert_weather_disabled_on_harness(srv: Any, agent_id: str, *, disabled: bool) -> None:
    agent = srv.app_runtime.agent_registry.get_agent(agent_id)
    names = set(getattr(agent.config, "tools_disabled", frozenset()) or ())
    assert ("get_weather" in names) is disabled


async def test_disable_plugin_tool_via_admin_plugins_api(env_with_provider: Any) -> None:
    client, srv, auth = env_with_provider
    await _install_weather(client, auth)
    aid = await _create_agent(client, auth, "plugin-admin-disable")

    listed = await client.get(f"/api/plugins/agents/{aid}/tools", headers=auth)
    assert listed.status_code == 200, listed.text
    weather = next(tool for tool in listed.json()["tools"] if tool["name"] == "get_weather")
    assert weather["enabled"] is True

    patch = await client.patch(
        f"/api/plugins/agents/{aid}/tools",
        headers=auth,
        json={"plugins": {"weather": {"tools": {"get_weather": {"enabled": False, "config": {}}}}}},
    )
    assert patch.status_code == 200, patch.text
    cfg = srv.app_runtime.agent_registry.get_config(aid)
    assert cfg["plugins"]["weather"]["tools"]["get_weather"]["enabled"] is False
    _assert_weather_disabled_on_harness(srv, aid, disabled=True)

    settings = await client.get(f"/api/agents/{aid}/tool-settings", headers=auth)
    assert settings.status_code == 200, settings.text
    assert all(tool["source"] == "builtin" for tool in settings.json()["tools"])


async def test_disable_plugin_tool_via_expert_tool_settings(env_with_provider: Any) -> None:

    client, srv, auth = env_with_provider
    await _install_weather(client, auth)
    aid = await _create_agent(client, auth, "expert-tool-disable")

    response = await client.patch(
        f"/api/agents/{aid}/tool-settings/get_weather",
        headers=auth,
        json={"enabled": False, "source": "plugin", "plugin_id": "weather"},
    )
    assert response.status_code == 200, response.text
    cfg = srv.app_runtime.agent_registry.get_config(aid)
    assert cfg["plugins"]["weather"]["tools"]["get_weather"]["enabled"] is False
    _assert_weather_disabled_on_harness(srv, aid, disabled=True)

    enabled = await client.patch(
        f"/api/agents/{aid}/tool-settings/get_weather",
        headers=auth,
        json={"enabled": True, "source": "plugin", "plugin_id": "weather"},
    )
    assert enabled.status_code == 200, enabled.text
    cfg = srv.app_runtime.agent_registry.get_config(aid)
    assert cfg["plugins"]["weather"]["tools"]["get_weather"]["enabled"] is True
    _assert_weather_disabled_on_harness(srv, aid, disabled=False)


async def test_admin_and_expert_share_plugin_tool_config(env_with_provider: Any) -> None:
    client, srv, auth = env_with_provider
    await _install_weather(client, auth)
    aid = await _create_agent(client, auth, "shared-plugin-config")

    await client.patch(
        f"/api/plugins/agents/{aid}/tools",
        headers=auth,
        json={
            "plugins": {
                "weather": {
                    "tools": {"get_weather": {"enabled": False, "config": {"units": "metric"}}}
                }
            }
        },
    )
    await client.patch(
        f"/api/agents/{aid}/tool-settings/get_weather",
        headers=auth,
        json={"enabled": True, "source": "plugin", "plugin_id": "weather"},
    )
    cfg = srv.app_runtime.agent_registry.get_config(aid)
    tool_cfg = cfg["plugins"]["weather"]["tools"]["get_weather"]
    assert tool_cfg["enabled"] is True
    assert tool_cfg.get("config", {}).get("units") == "metric"


async def test_agent_plugin_switch_defaults_on_and_preserves_tools(env_with_provider: Any) -> None:
    client, srv, auth = env_with_provider
    await _install_weather(client, auth)
    aid = await _create_agent(client, auth, "agent-plugin-switch")

    listed = await client.get(f"/api/plugins/agents/{aid}", headers=auth)
    assert listed.status_code == 200, listed.text
    plugin = next(item for item in listed.json()["plugins"] if item["id"] == "weather")
    assert plugin["agent_enabled"] is True
    assert plugin["enabled"] is True

    configured = await client.patch(
        f"/api/plugins/agents/{aid}/tools",
        headers=auth,
        json={
            "plugins": {
                "weather": {
                    "tools": {"get_weather": {"enabled": True, "config": {"units": "metric"}}}
                }
            }
        },
    )
    assert configured.status_code == 200, configured.text

    off = await client.patch(
        f"/api/plugins/agents/{aid}",
        headers=auth,
        json={"plugins": {"weather": {"enabled": False}}},
    )
    assert off.status_code == 200, off.text
    plugin = next(item for item in off.json()["plugins"] if item["id"] == "weather")
    assert plugin["agent_enabled"] is False
    assert plugin["enabled"] is False
    _assert_weather_disabled_on_harness(srv, aid, disabled=True)

    cfg = srv.app_runtime.agent_registry.get_config(aid)
    tool_cfg = cfg["plugins"]["weather"]["tools"]["get_weather"]
    assert tool_cfg == {"enabled": True, "config": {"units": "metric"}}
    on = await client.patch(
        f"/api/plugins/agents/{aid}", headers=auth, json={"plugins": {"weather": {"enabled": True}}}
    )
    assert on.status_code == 200, on.text
    cfg = srv.app_runtime.agent_registry.get_config(aid)
    assert cfg["plugins"]["weather"]["tools"]["get_weather"] == tool_cfg


async def test_global_plugin_disable_takes_priority(env_with_provider: Any) -> None:
    client, _srv, auth = env_with_provider
    await _install_weather(client, auth)
    aid = await _create_agent(client, auth, "global-plugin-off")
    off = await client.patch("/api/plugins/weather", headers=auth, json={"enabled": False})
    assert off.status_code == 200, off.text
    listed = await client.get(f"/api/plugins/agents/{aid}", headers=auth)
    assert listed.status_code == 200, listed.text
    plugin = next(item for item in listed.json()["plugins"] if item["id"] == "weather")
    assert plugin["global_enabled"] is False
    assert plugin["agent_enabled"] is True
    assert plugin["enabled"] is False


async def test_agent_plugin_endpoints_enforce_owner(env_with_provider: Any) -> None:
    client, _srv, admin_auth = env_with_provider
    await _install_weather(client, admin_auth)
    owner_auth = await create_user(client, admin_auth, username="plugin_owner")
    other_auth = await create_user(client, admin_auth, username="plugin_other")
    aid = await _create_agent(client, owner_auth, "owned-plugin-agent")
    listed = await client.get(f"/api/plugins/agents/{aid}", headers=other_auth)
    assert listed.status_code == 403, listed.text
    patched = await client.patch(
        f"/api/plugins/agents/{aid}",
        headers=other_auth,
        json={"plugins": {"weather": {"enabled": False}}},
    )
    assert patched.status_code == 403, patched.text
