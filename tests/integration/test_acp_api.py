"""tests/integration/test_acp_api.py — ACP runner configuration API."""

from __future__ import annotations

import pytest

from tests.support.auth import create_agent, create_user

_RUNNER = {
    "enabled": True,
    "command": "python",
    "args": ["-m", "my_runner"],
    "env": {},
    "trusted": False,
    "tool_parse_mode": "call_title",
    "stdio_buffer_limit_bytes": 52428800,
}


@pytest.fixture
async def env(env_acp_agent):
    yield env_acp_agent


@pytest.fixture
async def key_holder(env):
    """``(client, auth, agent_id)`` — an account holding ``acp`` and an agent it owns.

    The key is what the surface answers to for reads and the per-agent tool
    toggle; the writes that name a command this host executes are the system
    administrator's (design §4.4).
    """
    client, _srv, admin_auth, _admin_agent = env
    user_auth = await create_user(client, admin_auth, username="acp_holder", permissions=["acp"])
    agent_id = await create_agent(client, user_auth, name="acp-holder-bot", config={})
    yield client, user_auth, agent_id


@pytest.fixture
async def plain_account(env):
    """``(client, auth, agent_id)`` — a signed-in account whose keys are the baseline set."""
    client, _srv, admin_auth, _admin_agent = env
    user_auth = await create_user(client, admin_auth, username="acp_outsider")
    agent_id = await create_agent(client, user_auth, name="acp-outsider-bot", config={})
    yield client, user_auth, agent_id


async def test_acp_config_round_trip(env) -> None:
    c, _srv, auth, agent_id = env

    r = await c.get(f"/api/agents/{agent_id}/acp", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["tool_enabled"] is False
    assert "opencode" in body["runners"]

    payload = {
        "tool_enabled": True,
        "runners": {
            "opencode": {
                "enabled": True,
                "command": "opencode",
                "args": ["acp"],
                "env": {},
                "trusted": False,
                "tool_parse_mode": "update_detail",
                "stdio_buffer_limit_bytes": 52428800,
            },
        },
    }
    r = await c.put(f"/api/agents/{agent_id}/acp", headers=auth, json=payload)
    assert r.status_code == 200
    assert r.json()["tool_enabled"] is True
    assert r.json()["runners"]["opencode"]["enabled"] is True

    r = await c.get(f"/api/agents/{agent_id}/acp/opencode", headers=auth)
    assert r.status_code == 200
    assert r.json()["command"] == "opencode"


async def test_acp_custom_runner_crud(env) -> None:
    c, _srv, auth, agent_id = env

    runner = {
        "enabled": True,
        "command": "python",
        "args": ["-m", "my_runner"],
        "env": {"FOO": "bar"},
        "trusted": True,
        "tool_parse_mode": "call_title",
        "stdio_buffer_limit_bytes": 52428800,
    }
    r = await c.put(f"/api/agents/{agent_id}/acp/my_runner", headers=auth, json=runner)
    assert r.status_code == 200
    assert r.json()["env"]["FOO"] == "bar"

    r = await c.delete(f"/api/agents/{agent_id}/acp/my_runner", headers=auth)
    assert r.status_code == 204

    r = await c.get(f"/api/agents/{agent_id}/acp/my_runner", headers=auth)
    assert r.status_code == 404


async def test_acp_builtin_runner_cannot_delete(env) -> None:
    c, _srv, auth, agent_id = env
    r = await c.delete(f"/api/agents/{agent_id}/acp/opencode", headers=auth)
    assert r.status_code == 403


async def test_acp_tool_rejected_for_directory_sandbox(env, tmp_path) -> None:
    c, _srv, auth, _agent_id = env
    scoped_root = tmp_path / "scoped"
    scoped_root.mkdir()
    agent_id = await create_agent(
        c,
        auth,
        name="acp-scoped",
        config={
            "backend": {
                "type": "local_shell",
                "root_dir": str(scoped_root),
                "virtual_mode": True,
            }
        },
    )

    response = await c.put(
        f"/api/agents/{agent_id}/acp/tool",
        headers=auth,
        json={"tool_enabled": True},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ACP_BACKEND_UNSUPPORTED"
    saved = await c.get(f"/api/agents/{agent_id}/acp", headers=auth)
    assert saved.json()["tool_enabled"] is False


# --- the surface's gates (design §4.4) --------------------------------------
#
# Two levels, and the tests below pin both: ``acp`` reads the runner list and
# toggles the per-agent tool; writing a runner *definition* — the command line
# this host executes — additionally needs the system administrator role, which
# is the extra check the design keeps for system-level operations. Before this
# stage every ``/api/acp`` route was admin-only and the agent-scoped ones were
# open to any signed-in account, so all four quadrants are asserted here:
# the key holder, the account without the key, the admin, and ownership.


async def test_acp_reads_and_toggle_follow_the_key(key_holder) -> None:
    c, auth, agent_id = key_holder

    for path in (
        "/api/acp",
        "/api/acp/opencode",
        f"/api/agents/{agent_id}/acp",
        f"/api/agents/{agent_id}/acp/opencode",
    ):
        r = await c.get(path, headers=auth)
        assert r.status_code == 200, f"{path}: {r.text}"

    # Enabling the tool for an agent it owns is the surface's own capability,
    # not a host-definition write: ``acp`` alone, on either route.
    r = await c.put(f"/api/agents/{agent_id}/acp/tool", headers=auth, json={"tool_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["tool_enabled"] is True

    r = await c.put(f"/api/agents/{agent_id}/acp", headers=auth, json={"tool_enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["tool_enabled"] is False


async def test_acp_refuses_an_account_without_the_key(plain_account) -> None:
    c, auth, agent_id = plain_account

    for path in (
        "/api/acp",
        "/api/acp/opencode",
        f"/api/agents/{agent_id}/acp",
        f"/api/agents/{agent_id}/acp/opencode",
    ):
        r = await c.get(path, headers=auth)
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"
        assert r.json()["error"]["details"]["permission"] == "acp"

    # The per-agent toggle used to be open to every signed-in account, which is
    # the bypass the module key closes (§2.4).
    r = await c.put(f"/api/agents/{agent_id}/acp/tool", headers=auth, json={"tool_enabled": True})
    assert r.status_code == 403, r.text


async def test_runner_definitions_need_the_system_administrator(key_holder) -> None:
    c, auth, agent_id = key_holder

    writes = (
        ("put", "/api/acp", {"runners": {"my_runner": _RUNNER}}),
        ("put", "/api/acp/my_runner", _RUNNER),
        ("delete", "/api/acp/my_runner", None),
        ("put", f"/api/agents/{agent_id}/acp/my_runner", _RUNNER),
        ("delete", f"/api/agents/{agent_id}/acp/my_runner", None),
        # The toggle route's optional ``runners`` leg is the same definition
        # write, which is why it checks the role in the body.
        ("put", f"/api/agents/{agent_id}/acp", {"tool_enabled": True, "runners": {"x": _RUNNER}}),
    )
    for method, path, body in writes:
        call = getattr(c, method)
        r = await (
            call(path, headers=auth, json=body) if body is not None else call(path, headers=auth)
        )
        assert r.status_code == 403, f"{method.upper()} {path}: {r.status_code} {r.text}"
        assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_runner_definitions_still_written_by_the_admin(env) -> None:
    """The role check is *extra*, not a replacement: an admin still writes them."""
    c, _srv, auth, agent_id = env

    r = await c.put("/api/acp", headers=auth, json={"runners": {"my_runner": _RUNNER}})
    assert r.status_code == 200, r.text
    assert r.json()["runners"]["my_runner"]["command"] == "python"

    r = await c.get("/api/acp", headers=auth)
    assert r.status_code == 200
    assert "my_runner" in r.json()["runners"]

    r = await c.delete("/api/acp/my_runner", headers=auth)
    assert r.status_code == 204

    r = await c.put(f"/api/agents/{agent_id}/acp", headers=auth, json={"runners": {"x": _RUNNER}})
    assert r.status_code == 200, r.text


async def test_acp_toggle_still_bounded_by_ownership(key_holder, env_acp_agent) -> None:
    """The key does not stand in for owning the agent (``api/common/agent.py``)."""
    c, auth, _own_agent = key_holder
    _c, _srv, _admin_auth, admin_agent = env_acp_agent

    r = await c.put(
        f"/api/agents/{admin_agent}/acp/tool", headers=auth, json={"tool_enabled": True}
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
