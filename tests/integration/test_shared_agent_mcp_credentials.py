"""A shared expert runs MCP tool calls with the CALLER's connector, not the owner's.

Connector credentials are baked into the tool objects: a custom MCP tool is a
live session opened with one user's headers, and a gateway tool closes over the
decrypted credential it was built with. An agent somebody else is allowed to use
therefore cannot serve the tools its owner loaded — that was the leak this test
locks down: the peer's turn ran with the owner's account.

The controlled HTTP MCP transport exposes a private report selected by the
credential; a turn must not read the other account's report.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
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
    """Read the private report available to the loaded MCP account."""
    return _text(await tool.ainvoke({}))


def _tool_named(tools: list[Any], name: str) -> Any:
    for tool in tools:
        if getattr(tool, "name", "") == name:
            return tool
    raise AssertionError(f"no {name!r} tool in {[getattr(t, 'name', t) for t in tools]}")


async def _save_probe(client: httpx.AsyncClient, auth: dict[str, str], token: str) -> None:
    response = await client.put(
        "/api/connectors/custom-mcp",
        headers=auth,
        json={"servers": {"probe": _probe_spec(token)}},
    )
    assert response.status_code == 200, response.text


async def test_shared_agent_serves_the_callers_own_mcp_connector(
    env, monkeypatch, tmp_path: Path
) -> None:
    """The peer's turn resolves the peer's connector — never the owner's."""
    from langchain_core.tools import StructuredTool

    owner_report = tmp_path / "owner-private.txt"
    peer_report = tmp_path / "peer-private.txt"
    await asyncio.to_thread(owner_report.write_text, "Owner-only report", encoding="utf-8")
    await asyncio.to_thread(peer_report.write_text, "Peer-only report", encoding="utf-8")
    reports = {OWNER_TOKEN: owner_report, PEER_TOKEN: peer_report}

    def _private_tool(server_name: str, report: Path) -> StructuredTool:
        def read_report() -> str:
            """Read the private report available to this connector account."""
            return report.read_text(encoding="utf-8")

        return StructuredTool.from_function(
            read_report,
            name=f"{server_name}_read_report",
            description="Read this connector account's private report.",
        )

    async def _load_http_tools(configs: dict[str, dict[str, Any]]) -> list[Any]:
        return [
            _private_tool(server_name, reports[spec["headers"]["X-Probe-Token"]])
            for server_name, spec in configs.items()
        ]

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

    # The owner's turn uses only the report available to the owner's credential.
    frames = await ws_chat_turn(
        client,
        agent_id,
        owner_auth,
        mcp_servers=["probe"],
        text="owner turn",
    )
    assert all(frame.get("type") != "error" for frame in frames), frames
    registry = srv.app_runtime.agent_registry
    owner_tools = registry.turn_mcp_tools(agent_id, owner_id, ["probe"])["probe"]
    assert await _call(_tool_named(owner_tools, "probe_read_report")) == "Owner-only report"

    # The peer's turn on that same agent: the tools it resolves are its own.
    frames = await ws_chat_turn(
        client,
        agent_id,
        peer_auth,
        mcp_servers=["probe"],
        text="peer turn",
    )
    assert all(frame.get("type") != "error" for frame in frames), frames

    peer_tools = registry.turn_mcp_tools(agent_id, peer_id, ["probe"])["probe"]
    assert await _call(_tool_named(peer_tools, "probe_read_report")) == "Peer-only report"

    # Preparing a peer's turn must not replace the owner's account binding.
    owner_after_peer = registry.turn_mcp_tools(agent_id, owner_id, ["probe"])["probe"]
    assert await _call(_tool_named(owner_after_peer, "probe_read_report")) == "Owner-only report"
