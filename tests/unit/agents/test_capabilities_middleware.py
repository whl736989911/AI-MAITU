from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from octop.infra.agents.middleware.capabilities import (
    CapabilityMiddleware,
    guarded_subagent_specs,
)
from octop.infra.agents.middleware.turn_mcp import TurnMcpToolsMiddleware
from octop.infra.errors import ErrorCode, OctopError


class _Policy:
    def __init__(self, grants: set[str] | None = None, active: bool = True) -> None:
        self.grants = grants or set()
        self.active = active

    def require_permissions(self, user_id: int | None, keys: Any) -> None:
        if user_id != 17 or not self.active:
            raise OctopError.localized(ErrorCode.USER_DISABLED)
        if not set(keys) <= self.grants:
            raise OctopError.localized(ErrorCode.FORBIDDEN)


def _tool(name: str) -> Any:
    return StructuredTool.from_function(
        func=lambda: "executed", name=name, description=f"{name} tool"
    )


def _request(
    name: str,
    *,
    args: dict[str, Any] | None = None,
    tool: Any | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": "call-1"},
        tool=tool or _tool(name),
        state=None,
        runtime=None,
    )


def _middleware(
    policy: _Policy,
    *,
    config: dict[str, Any] | None = None,
    plugin_tools: set[str] | None = None,
    plugin_map: dict[str, tuple[str, str]] | None = None,
    known_mcp_tools: list[Any] | None = None,
    active_mcp_tools: list[Any] | None = None,
) -> CapabilityMiddleware:
    known = known_mcp_tools or []
    active = active_mcp_tools or []
    return CapabilityMiddleware(
        policy=policy,  # type: ignore[arg-type]
        config_for_agent=lambda: config or {},
        plugin_tool_names=plugin_tools or set(),
        plugin_tool_plugins=plugin_map or {},
        mcp_tool_status=lambda user, servers, tool: (
            "active"
            if user == 17
            and "caller-server" in servers
            and any(tool is registered for registered in active)
            else "mcp"
            if any(tool is registered for registered in known)
            else None
        ),
    )


def test_sync_tool_call_rechecks_permission_at_execution_time(monkeypatch: Any) -> None:
    policy = _Policy({"terminal"})
    middleware = _middleware(policy)
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": "17"}},
    )
    reached: list[bool] = []
    request = _request("execute")

    assert middleware.wrap_tool_call(request, lambda _request: reached.append(True)) is None
    assert reached == [True]
    policy.grants.clear()
    result = middleware.wrap_tool_call(request, lambda _request: reached.append(True))
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert reached == [True]


@pytest.mark.asyncio
async def test_async_call_rejects_unmapped_dynamic_tool_and_never_executes(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17}},
    )
    middleware = _middleware(_Policy({"terminal"}))
    handler = AsyncMock(return_value=ToolMessage(content="done", tool_call_id="call-1"))

    result = await middleware.awrap_tool_call(_request("unrecognized_dynamic_tool"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage) and result.status == "error"


def test_tool_list_filtering_is_separate_from_execution_enforcement(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17}},
    )
    middleware = _middleware(_Policy({"workspace"}))
    request = ModelRequest(model=MagicMock(), messages=[], tools=[_tool("execute"), _tool("ls")])
    captured: list[ModelRequest[Any]] = []

    middleware.wrap_model_call(
        request, lambda filtered: captured.append(filtered) or AIMessage("ok")
    )

    assert [tool.name for tool in captured[0].tools] == ["ls"]
    executed: list[bool] = []
    result = middleware.wrap_tool_call(_request("execute"), lambda _request: executed.append(True))
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert executed == []


def test_missing_or_disabled_identity_is_rejected_for_chat_tools_and_empty_requirements(
    monkeypatch: Any,
) -> None:
    middleware = _middleware(_Policy(active=False))
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": None}},
    )
    called: list[bool] = []

    result = middleware.wrap_tool_call(_request("agent_list"), lambda _request: called.append(True))

    assert isinstance(result, ToolMessage) and result.status == "error"
    assert called == []


def test_hard_tools_disabled_cannot_be_bypassed_by_forged_or_historical_args(
    monkeypatch: Any,
) -> None:
    middleware = _middleware(_Policy({"terminal"}), config={"tools_disabled": ["execute"]})
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17}},
    )
    called: list[bool] = []

    result = middleware.wrap_tool_call(
        _request("execute", args={"name": "safe_tool", "tool_name": "read_file"}),
        lambda _request: called.append(True),
    )

    assert isinstance(result, ToolMessage) and result.status == "error"
    assert called == []


def test_registered_plugin_and_caller_mcp_tools_are_classified_by_registry(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17, "mcp_servers": ["caller-server"]}},
    )
    plugin = "real_plugin_tool"
    mcp = "caller-server_lookup"
    caller_mcp_tool = _tool(mcp)
    middleware = _middleware(
        _Policy({"agent_plugins", "connectors"}),
        plugin_tools={plugin},
        plugin_map={plugin: ("plugin-42", "original name")},
        known_mcp_tools=[caller_mcp_tool],
        active_mcp_tools=[caller_mcp_tool],
    )
    called: list[str] = []

    middleware.wrap_tool_call(_request(plugin), lambda _request: called.append(plugin))
    middleware.wrap_tool_call(
        _request(mcp, tool=caller_mcp_tool), lambda _request: called.append(mcp)
    )

    assert called == [plugin, mcp]


def test_cleaned_plugin_name_uses_original_config_key_for_model_and_execution(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17}},
    )
    middleware = _middleware(
        _Policy({"agent_plugins"}),
        config={
            "plugins": {
                "plugin-42": {
                    "tools": {"original name": {"enabled": False}},
                }
            }
        },
        plugin_tools={"original_name"},
        plugin_map={"original_name": ("plugin-42", "original name")},
    )
    request = ModelRequest(model=MagicMock(), messages=[], tools=[_tool("original_name")])
    captured: list[ModelRequest[Any]] = []
    middleware.wrap_model_call(
        request, lambda filtered: captured.append(filtered) or AIMessage("ok")
    )
    called: list[bool] = []

    result = middleware.wrap_tool_call(
        _request("original_name"),
        lambda _request: called.append(True),
    )

    assert captured[0].tools == []
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert called == []


def test_revoked_dynamic_permission_is_denied_without_trusting_call_args(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17, "mcp_servers": ["caller-server"]}},
    )
    policy = _Policy({"connectors"})
    mcp = _tool("caller-server_lookup")
    middleware = _middleware(policy, known_mcp_tools=[mcp], active_mcp_tools=[mcp])
    called: list[bool] = []
    policy.grants.clear()

    result = middleware.wrap_tool_call(
        _request("caller-server_lookup", args={"user": 999}, tool=mcp),
        lambda _request: called.append(True),
    )

    assert isinstance(result, ToolMessage) and result.status == "error"
    assert called == []


def test_mcp_server_classification_uses_exact_registry_entry_not_name_prefix() -> None:
    from octop.infra.agents.manager import AgentManager

    manager = object.__new__(AgentManager)
    foo_tool = _tool("foo_lookup")
    foo_bar_tool = _tool("foo_bar_lookup")
    manager._turn_mcp_tools = {
        ("agent-1", 17): {
            "foo": [foo_tool],
            "foo_bar": [foo_bar_tool],
        }
    }

    assert manager._mcp_tool_status("agent-1", 17, ["foo"], foo_tool) == "active"
    assert manager._mcp_tool_status("agent-1", 17, ["foo"], foo_bar_tool) == "mcp"


def test_removed_mcp_cache_object_keeps_closed_world_mcp_classification() -> None:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.agents.middleware.capabilities import mark_mcp_tool_source

    manager = object.__new__(AgentManager)
    old_tool = _tool("web_fetch")
    mark_mcp_tool_source(old_tool, server="custom", user_id=17)
    manager._turn_mcp_tools = {}

    assert manager._mcp_tool_status("agent-1", 17, [], old_tool) == "mcp"


def test_mcp_object_collision_cannot_fall_back_to_builtin_permission(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.capabilities.get_config",
        lambda: {"configurable": {"user": 17, "mcp_servers": []}},
    )
    owner_mcp_tool = _tool("web_fetch")
    middleware = _middleware(
        _Policy({"browser"}),
        known_mcp_tools=[owner_mcp_tool],
    )
    called: list[bool] = []

    result = middleware.wrap_tool_call(
        _request("web_fetch", tool=owner_mcp_tool),
        lambda _request: called.append(True),
    )

    assert isinstance(result, ToolMessage) and result.status == "error"
    assert called == []


def test_unwrappable_compiled_or_remote_subagent_is_rejected_before_invocation() -> None:
    middleware = _middleware(_Policy())
    invoked: list[bool] = []

    with pytest.raises(ValueError, match="cannot be capability-guarded"):
        guarded_subagent_specs(
            [{"name": "compiled", "runnable": lambda _input: invoked.append(True)}],
            middleware,
        )
    with pytest.raises(ValueError, match="cannot be capability-guarded"):
        guarded_subagent_specs([{"name": "remote", "graph_id": "remote-graph"}], middleware)

    assert invoked == []


def test_general_purpose_subagent_strips_owner_mcp_and_uses_callers_tool(
    monkeypatch: Any,
) -> None:
    from octop.infra.agents.middleware import turn_mcp

    config = {"configurable": {"user": 17, "mcp_servers": ["caller-server"]}}
    monkeypatch.setattr(turn_mcp, "get_config", lambda: config)
    monkeypatch.setattr("octop.infra.agents.middleware.capabilities.get_config", lambda: config)
    owner_tool = _tool("caller-server_lookup")
    caller_tool = _tool("caller-server_lookup")
    ordinary_tool = _tool("ls")

    class _Source:
        def turn_mcp_tools(
            self, agent_id: str, user_id: int, servers: list[str]
        ) -> dict[str, list[Any]]:
            assert (agent_id, user_id, servers) == (
                "agent-1",
                17,
                ["caller-server"],
            )
            return {"caller-server": [caller_tool]}

    turn_middleware = TurnMcpToolsMiddleware(agent_id="agent-1", source=_Source())
    capability = _middleware(
        _Policy({"connectors", "workspace"}),
        known_mcp_tools=[owner_tool, caller_tool],
        active_mcp_tools=[caller_tool],
    )
    [general_purpose] = guarded_subagent_specs(
        [],
        capability,
        parent_tools=[owner_tool, ordinary_tool],
        mcp_tool_names=frozenset({owner_tool.name}),
        mcp_server_names=frozenset({"caller-server"}),
        turn_mcp_middleware=turn_middleware,
    )
    assert [tool.name for tool in general_purpose["tools"]] == ["ls"]
    assert turn_middleware in general_purpose["middleware"]
    actual: list[Any] = []

    result = turn_middleware.wrap_tool_call(
        _request("caller-server_lookup", tool=owner_tool),
        lambda request: capability.wrap_tool_call(
            request, lambda forwarded: actual.append(forwarded.tool) or "executed"
        ),
    )

    assert result == "executed"
    assert actual == [caller_tool]
