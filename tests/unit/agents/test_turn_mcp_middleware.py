"""The middleware that serves a caller's own MCP tools on somebody else's agent."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from octop.infra.agents.middleware import turn_mcp

USER = 7
SERVER = "probe"


def _tool(name: str, token: str) -> Any:
    def _run() -> str:
        return token

    def _coroutine() -> Any:
        async def _call() -> str:
            return token

        return _call()

    return StructuredTool.from_function(
        func=_run,
        coroutine=_coroutine,
        name=name,
        description=f"{name} from {token}",
    )


class _Source:
    """A registry with one turn's tools, or nothing at all."""

    def __init__(self, tools: dict[str, list[Any]] | None) -> None:
        self._tools = tools or {}
        self.asked: list[tuple[str, int, list[str]]] = []

    def turn_mcp_tools(
        self,
        agent_id: str,
        user_id: int,
        servers: list[str],
    ) -> dict[str, list[Any]]:
        self.asked.append((agent_id, user_id, list(servers)))
        return {name: self._tools[name] for name in servers if name in self._tools}


def _configure(
    monkeypatch: Any,
    *,
    user: object = str(USER),
    servers: Any = None,
) -> None:
    monkeypatch.setattr(
        turn_mcp,
        "get_config",
        lambda: {
            "configurable": {"user": user, "mcp_servers": [SERVER] if servers is None else servers}
        },
    )


def _middleware(source: _Source) -> turn_mcp.TurnMcpToolsMiddleware:
    return turn_mcp.TurnMcpToolsMiddleware(agent_id="agent-1", source=source)


def _model_request(tools: list[Any]) -> ModelRequest[Any]:
    return ModelRequest(
        model=ChatOpenAI(model="gpt-5.4", api_key="test"),
        messages=[AIMessage(content="hi")],
        tools=tools,
    )


def _tool_request(name: str, tool: Any) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call-1"},
        tool=tool,
        state=None,
        runtime=None,
    )


def test_a_peer_turn_runs_the_callers_tool_not_the_agents(monkeypatch) -> None:
    """The call the model makes executes the caller's connector, by identity."""
    _configure(monkeypatch)
    owner_tool = _tool("probe_whoami", "token=OWNER")
    peer_tool = _tool("probe_whoami", "token=PEER")
    seen: list[Any] = []

    def handler(request: ToolCallRequest) -> str:
        seen.append(request.tool)
        return "executed"

    result = _middleware(_Source({SERVER: [peer_tool]})).wrap_tool_call(
        _tool_request("probe_whoami", owner_tool),
        handler,
    )

    assert result == "executed"
    assert seen == [peer_tool]


def test_a_tool_the_caller_has_no_connector_for_is_refused(monkeypatch) -> None:
    """No fallback to the agent's own tool: the call is refused, not borrowed."""
    _configure(monkeypatch)
    owner_tool = _tool("probe_delete", "token=OWNER")
    called: list[Any] = []

    def handler(request: ToolCallRequest) -> str:
        called.append(request)
        return "executed"

    result = _middleware(_Source({SERVER: [_tool("probe_whoami", "token=PEER")]})).wrap_tool_call(
        _tool_request("probe_delete", owner_tool),
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "probe_delete" in str(result.content)
    assert called == []


def test_the_model_only_sees_the_callers_tools(monkeypatch) -> None:
    """The agent's own tools for that server are hidden for this turn."""
    _configure(monkeypatch)
    peer_tool = _tool("probe_whoami", "token=PEER")
    owner_tool = _tool("probe_whoami", "token=OWNER")
    unrelated = _tool("read_file", "unrelated")
    seen: list[Any] = []

    def handler(request: ModelRequest[Any]) -> str:
        seen.append(request.tools)
        return "response"

    _middleware(_Source({SERVER: [peer_tool]})).wrap_model_call(
        _model_request([owner_tool, unrelated]),
        handler,
    )

    assert seen[0] == [unrelated, peer_tool]


def test_a_turn_that_resolved_nothing_is_left_alone(monkeypatch) -> None:
    """Owners, feature runs, cron and IM keep the tool set the agent has."""
    _configure(monkeypatch)
    source = _Source(None)
    agent_tool = _tool("probe_whoami", "token=OWNER")
    middleware = _middleware(source)
    seen: list[Any] = []

    def handler(request: Any) -> str:
        seen.append(request)
        return "executed"

    assert middleware.wrap_tool_call(_tool_request("probe_whoami", agent_tool), handler) == (
        "executed"
    )
    assert seen[0].tool is agent_tool
    assert middleware.wrap_model_call(_model_request([agent_tool]), handler) == "executed"
    assert seen[1].tools == [agent_tool]
    # The registry is consulted, and having nothing for this turn changes nothing.
    assert source.asked == [("agent-1", USER, [SERVER]), ("agent-1", USER, [SERVER])]


def test_a_turn_without_a_user_is_left_alone(monkeypatch) -> None:
    """``configurable['user']`` is what identifies the caller; without it, nothing."""
    _configure(monkeypatch, user="not-a-user-id")
    source = _Source({SERVER: [_tool("probe_whoami", "token=PEER")]})
    agent_tool = _tool("probe_whoami", "token=OWNER")
    seen: list[Any] = []

    def handler(request: ToolCallRequest) -> str:
        seen.append(request.tool)
        return "executed"

    _middleware(source).wrap_tool_call(_tool_request("probe_whoami", agent_tool), handler)

    assert seen == [agent_tool]
    assert source.asked == []


def test_turn_user_id_reads_the_harness_string_form(monkeypatch) -> None:
    """The harness carries the caller as ``str(user_id)``."""
    assert turn_mcp.turn_user_id("42") == 42
    assert turn_mcp.turn_user_id(42) == 42
    assert turn_mcp.turn_user_id(" 42 ") == 42
    assert turn_mcp.turn_user_id(None) is None
    assert turn_mcp.turn_user_id("alice") is None
    assert turn_mcp.turn_user_id(True) is None
