"""A shared expert runs MCP tool calls with the CALLER's connector, not the owner's.

Connector credentials are baked into the tool objects: a custom MCP tool is a
live session opened with one user's headers, and a gateway tool closes over the
decrypted credential it was built with. An agent somebody else is allowed to use
therefore cannot serve the tools its owner loaded — that was the leak this test
locks down: the peer's turn ran with the owner's account.

The probe HTTP MCP tool reports the credential in its request headers, so the
answer to ``probe_whoami`` identifies the account the call used.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests.support.auth import (
    create_agent,
    create_user,
    resolve_user_id,
    seed_openai_provider,
)
from tests.support.http import ws_chat_turn

OWNER_TOKEN = "TOKEN-OWNER"
PEER_TOKEN = "TOKEN-PEER"


def _probe_spec(token: str) -> dict[str, Any]:
    """One user's HTTP MCP server uses that user's own credential header."""
    return {
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "headers": {"X-Probe-Token": token},
    }


def _text(result: Any) -> str:
    """Text of a tool result, whichever shape the MCP adapter returned it in."""
    if isinstance(result, str):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text") or "")
    raise AssertionError(f"unexpected tool result: {result!r}")


async def _call(tool: Any) -> str:
    """Invoke a no-argument MCP tool and read back the credential it reports."""
    return _text(await tool.ainvoke({}))


def _tool_named(tools: list[Any], name: str) -> Any:
    for tool in tools:
        if getattr(tool, "name", "") == name:
            return tool
    raise AssertionError(f"no {name!r} tool in {[getattr(t, 'name', t) for t in tools]}")


def _agent_tools(srv: Any, agent_id: str) -> list[Any]:
    """The tools the agent itself holds (what its owner's turns run with)."""
    return list(getattr(srv.app_runtime.agent_registry.get_agent(agent_id), "_mcp_tools", []))


async def _save_probe(client: httpx.AsyncClient, auth: dict[str, str], token: str) -> None:
    response = await client.put(
        "/api/connectors/custom-mcp",
        headers=auth,
        json={"servers": {"probe": _probe_spec(token)}},
    )
    assert response.status_code == 200, response.text


async def test_shared_agent_serves_the_callers_own_mcp_connector(env, monkeypatch) -> None:
    """The peer's turn resolves the peer's connector — never the owner's."""
    from langchain_core.tools import StructuredTool

    async def _load_http_tools(configs: dict[str, dict[str, Any]]) -> list[Any]:
        tools = []
        for server_name, spec in configs.items():
            assert spec["transport"] == "streamable_http"
            token = spec["headers"]["X-Probe-Token"]

            async def _whoami(token: str = token) -> str:
                return f"token={token}"

            tools.append(
                StructuredTool.from_function(
                    coroutine=_whoami,
                    name=f"{server_name}_whoami",
                    description="Return the credential associated with this HTTP MCP server.",
                )
            )
        return tools

    monkeypatch.setattr("harness_agent.mcp.aload_mcp_tools", _load_http_tools)
    client, srv, admin_auth = env
    await seed_openai_provider(client, admin_auth)
    owner_auth = await create_user(client, admin_auth, username="mcp_owner")
    peer_auth = await create_user(client, admin_auth, username="mcp_peer")
    owner_id = await resolve_user_id(client, admin_auth, "mcp_owner")
    peer_id = await resolve_user_id(client, admin_auth, "mcp_peer")
    agent_id = await create_agent(client, owner_auth, name="shared-mcp-bot")
    await _save_probe(client, owner_auth, OWNER_TOKEN)
    await _save_probe(client, peer_auth, PEER_TOKEN)

    shared = await client.patch(
        f"/api/agents/{agent_id}",
        headers=owner_auth,
        json={"is_shared": True},
    )
    assert shared.status_code == 200, shared.text

    # The owner's own turn: her credential is on the agent, as it always was.
    frames = await ws_chat_turn(
        client,
        agent_id,
        owner_auth,
        mcp_servers=["probe"],
        text="owner turn",
    )
    assert all(frame.get("type") != "error" for frame in frames), frames
    assert await _call(_tool_named(_agent_tools(srv, agent_id), "probe_whoami")) == (
        f"token={OWNER_TOKEN}"
    )
    # The owner's own turns are unchanged: her tools sit on the agent, and the
    # turn-scoped registry is only ever involved when somebody else runs it.
    assert srv.app_runtime.agent_registry.turn_mcp_tools(agent_id, owner_id, ["probe"]) == {}

    # The peer's turn on that same agent: the tools it resolves are its own.
    frames = await ws_chat_turn(
        client,
        agent_id,
        peer_auth,
        mcp_servers=["probe"],
        text="peer turn",
    )
    assert all(frame.get("type") != "error" for frame in frames), frames

    registry = srv.app_runtime.agent_registry
    peer_tools = registry.turn_mcp_tools(agent_id, peer_id, ["probe"])["probe"]
    assert await _call(_tool_named(peer_tools, "probe_whoami")) == f"token={PEER_TOKEN}"

    # ... and every tool the agent holds still belongs to its owner, because
    # nothing of the peer's may be loaded onto an agent others also run.
    owner_tools = _agent_tools(srv, agent_id)
    assert owner_tools, "the owner's turn left no tools on her own agent"
    assert [await _call(tool) for tool in owner_tools] == [f"token={OWNER_TOKEN}"] * len(
        owner_tools
    )
