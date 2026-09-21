"""MCP tools for the user of the current turn — never for another user.

A *shared agent* is one user's agent that somebody else runs: the ACL publishes
it, and every user it is shared with talks to that same ``HarnessAgent``. MCP
tools cannot ride on such an agent, because they carry credentials by
construction — a gateway tool closes over the decrypted credential of the
account it was built for, and a custom MCP tool is a live session opened with
one user's headers. An agent that already holds another user's tools would run
this turn's calls with that user's account, which is exactly the leak
``prepare_chat_mcp`` refuses to create (see ``AgentManager.prepare_chat_mcp``).

So the caller's tools live in a per-turn registry instead, and this middleware
is what serves them:

- :meth:`wrap_model_call` hides the agent's own MCP tools for the servers this
  turn resolved and exposes the caller's, so the model only ever sees — and
  only ever fills arguments for — the caller's connector.
- :meth:`wrap_tool_call` executes the caller's tool for the call, even when the
  agent never registered a tool of that name (``ToolNode`` defers validation to
  the interceptor), and refuses a call the caller has no connector for instead
  of falling back to whoever else the agent belongs to.

A turn that resolved nothing passes through untouched: the owner's own turns,
feature runs on the user's own agent, IM and cron delivery all keep the tool set
the agent has. The registry is keyed by ``(agent_id, user_id)`` and the turn's
identity comes from ``configurable`` — the same entry the harness itself reads
for ``user`` and ``mcp_servers``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.types import Command

logger = logging.getLogger(__name__)

ModelCallResult = ModelResponse[Any] | AIMessage | ExtendedModelResponse[Any]


class TurnMcpToolSource(Protocol):
    """Where the middleware reads one turn's MCP tools from."""

    def turn_mcp_tools(
        self,
        agent_id: str,
        user_id: int,
        servers: Sequence[str],
    ) -> dict[str, list[Any]]:
        """MCP tools ``user_id`` resolved for ``servers`` on ``agent_id``."""
        ...


def tool_name(tool: object) -> str:
    """Name of a tool or raw tool schema, whichever the request carries."""
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def turn_user_id(raw: object) -> int | None:
    """The user id of this turn, from ``configurable['user']``.

    The harness carries it as a string (``ChatRequest.to_runnable_config``) and
    Octop stamps ``str(user_id)``; anything else is not a user this middleware
    can resolve tools for.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _run_configurable() -> dict[str, Any]:
    """This run's ``configurable``, or ``{}`` outside a run (unit tests, CLI)."""
    try:
        config = get_config()
    except RuntimeError:
        return {}
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable")
    return dict(configurable) if isinstance(configurable, dict) else {}


class TurnMcpToolsMiddleware(AgentMiddleware[Any, Any]):
    """Serve this turn's own MCP tools on an agent they were not loaded into."""

    def __init__(self, *, agent_id: str, source: TurnMcpToolSource) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._source = source

    # ------------------------------------------------------------------
    # What this turn resolved
    # ------------------------------------------------------------------

    def _turn_tools(self) -> dict[str, list[Any]]:
        """The tools this turn's user resolved, keyed by MCP server name.

        Empty for every turn that prepared nothing — which is what keeps the
        single-owner path byte-identical: with no entry, no hook below rewrites
        the request at all.
        """
        configurable = _run_configurable()
        user_id = turn_user_id(configurable.get("user"))
        servers = configurable.get("mcp_servers")
        if user_id is None or not isinstance(servers, list) or not servers:
            return {}
        return self._source.turn_mcp_tools(self._agent_id, user_id, [str(name) for name in servers])

    def _resolve_call(self, name: str) -> tuple[BaseTool | None, str | None]:
        """``(tool, refusal)`` for a tool call of this turn.

        ``(None, None)`` — not this middleware's business, call it whatever the
        agent has. ``(tool, None)`` — the caller's own tool. ``(None, reason)``
        — the caller has no connector for that server, so the call is refused
        rather than served by the agent's owner.
        """
        turn_tools = self._turn_tools()
        if not turn_tools:
            return None, None
        for server, tools in turn_tools.items():
            for tool in tools:
                if tool_name(tool) == name:
                    return tool, None
            if name.startswith(f"{server}_"):
                return None, (
                    f"Tool {name!r} is unavailable: you have no connector configured "
                    f"for the {server!r} server."
                )
        return None, None

    # ------------------------------------------------------------------
    # Model call — hide the agent's MCP tools, expose this turn's
    # ------------------------------------------------------------------

    def _apply_turn_tools(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        turn_tools = self._turn_tools()
        if not turn_tools:
            return request
        exposed = [tool for tools in turn_tools.values() for tool in tools]
        resolved_names = {tool_name(tool) for tool in exposed}
        prefixes = tuple(f"{server}_" for server in turn_tools)
        kept = [
            tool
            for tool in list(request.tools or [])
            if tool_name(tool) not in resolved_names and not tool_name(tool).startswith(prefixes)
        ]
        return request.override(tools=[*kept, *exposed])

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult:
        return handler(self._apply_turn_tools(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult:
        return await handler(self._apply_turn_tools(request))

    # ------------------------------------------------------------------
    # Tool call — execute this turn's tool
    # ------------------------------------------------------------------

    @staticmethod
    def _refusal(request: ToolCallRequest, reason: str) -> ToolMessage:
        tool_call = request.tool_call
        return ToolMessage(
            content=reason,
            tool_call_id=str(tool_call.get("id") or ""),
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool, refusal = self._resolve_call(str(request.tool_call.get("name") or ""))
        if refusal is not None:
            logger.info("turn MCP tools: refused %s", request.tool_call.get("name"))
            return self._refusal(request, refusal)
        if tool is None:
            return handler(request)
        return handler(request.override(tool=tool))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool, refusal = self._resolve_call(str(request.tool_call.get("name") or ""))
        if refusal is not None:
            logger.info("turn MCP tools: refused %s", request.tool_call.get("name"))
            return self._refusal(request, refusal)
        if tool is None:
            return await handler(request)
        return await handler(request.override(tool=tool))


__all__ = ["TurnMcpToolSource", "TurnMcpToolsMiddleware", "tool_name", "turn_user_id"]
