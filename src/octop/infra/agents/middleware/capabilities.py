"""Enforce agent-module permissions at model and tool execution boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from harness_agent import HarnessAgent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.capabilities import UserCapabilityPolicy

ModelCallResult = ModelResponse[Any] | AIMessage


def _user_id(configurable: object) -> int | None:
    if not isinstance(configurable, Mapping):
        return None
    value = configurable.get("user")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _run_configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except RuntimeError:
        return {}
    configurable = config.get("configurable") or {}
    return dict(configurable) if isinstance(configurable, dict) else {}


def _tool_name(tool: object) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


class CapabilityMiddleware(AgentMiddleware[Any, Any]):
    """Apply live permissions, denylist, and closed-world dynamic-tool checks."""

    def __init__(
        self,
        *,
        policy: UserCapabilityPolicy,
        config_for_agent: Callable[[], Mapping[str, Any]],
        plugin_tool_names: set[str] | frozenset[str] = frozenset(),
        plugin_tool_plugins: Mapping[str, tuple[str, str]] | None = None,
        mcp_tool_status: Callable[[int | None, list[str], object | None], str | None] | None = None,
        floor_disabled_tools: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self._policy = policy
        self._config_for_agent = config_for_agent
        self._plugin_tool_names = frozenset(plugin_tool_names)
        self._plugin_tool_plugins = dict(plugin_tool_plugins or {})
        self._mcp_tool_status = mcp_tool_status or (lambda _user_id, _servers, _tool: None)
        self._floor_disabled_tools = frozenset(floor_disabled_tools)

    def _required_permission(
        self, name: str, user_id: int | None, servers: list[str], tool: object | None
    ) -> str | None:
        mcp_status = self._mcp_tool_status(user_id, servers, tool)
        if mcp_status == "active":
            return "connectors"
        if mcp_status == "mcp":
            return "__mcp_tool_not_active__"
        if name in self._plugin_tool_names:
            return "agent_plugins"
        from octop.infra.agents.tool_catalog import (
            BUILTIN_TOOL_CATALOG,
            required_permission_for_tool,
        )

        required = required_permission_for_tool(name)
        if required is not None:
            return required
        if name in {entry.name for entry in BUILTIN_TOOL_CATALOG} or name in {
            "agent_list",
            "ask_agent",
            "tool_search",
        }:
            return None
        return "__unknown_dynamic_tool__"

    def _denial(self, name: str, user_id: int | None, tool: object | None = None) -> str | None:
        try:
            self._policy.require_permissions(user_id, ())
        except OctopError as exc:
            return "inactive" if exc.code is ErrorCode.USER_DISABLED else "forbidden"
        config = self._config_for_agent()
        from octop.infra.agents.tool_catalog import (
            plugin_tool_explicitly_disabled,
            tools_disabled_set,
        )

        plugin_record = self._plugin_tool_plugins.get(name)
        if plugin_record is not None:
            plugin_id, original_name = plugin_record
            if plugin_tool_explicitly_disabled(
                config, plugin_id=plugin_id, tool_name=original_name
            ):
                return "disabled"
        if name in self._floor_disabled_tools or name in tools_disabled_set(config):
            return "disabled"
        configurable = _run_configurable()
        raw_servers = configurable.get("mcp_servers")
        servers = [str(server) for server in raw_servers] if isinstance(raw_servers, list) else []
        required = self._required_permission(name, user_id, servers, tool)
        if required in {"__unknown_dynamic_tool__", "__mcp_tool_not_active__"}:
            return "unknown"
        if required is None:
            return None
        try:
            self._policy.require_permissions(user_id, (required,))
        except OctopError:
            return required
        return None

    def _visible_tools(self, tools: list[Any]) -> list[Any]:
        user_id = _user_id(_run_configurable())
        self._policy.require_permissions(user_id, ())
        return [tool for tool in tools if self._denial(_tool_name(tool), user_id, tool) is None]

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelCallResult],
    ) -> ModelCallResult:
        tools = list(getattr(request, "tools", None) or [])
        visible = self._visible_tools(tools)
        return handler(request.override(tools=visible) if len(visible) != len(tools) else request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        tools = list(getattr(request, "tools", None) or [])
        visible = self._visible_tools(tools)
        return await handler(
            request.override(tools=visible) if len(visible) != len(tools) else request
        )

    def _tool_error(self, request: Any, reason: str) -> ToolMessage:
        configurable = _run_configurable()
        locale = str(configurable.get("locale") or configurable.get("language") or "en")
        code = ErrorCode.USER_DISABLED if reason == "inactive" else ErrorCode.FORBIDDEN
        return ToolMessage(
            content=OctopError.localized(code, locale).message,
            tool_call_id=str(request.tool_call.get("id") or ""),
            status="error",
        )

    def guard(self, name: str, user_id: int | None) -> None:
        reason = self._denial(name, user_id)
        if reason is not None:
            configurable = _run_configurable()
            locale = str(configurable.get("locale") or configurable.get("language") or "en")
            code = ErrorCode.USER_DISABLED if reason == "inactive" else ErrorCode.FORBIDDEN
            raise OctopError.localized(code, locale)

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        configurable = _run_configurable()
        name = _tool_name(request.tool)
        reason = self._denial(name, _user_id(configurable), request.tool)
        if reason is not None:
            return self._tool_error(request, reason)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        configurable = _run_configurable()
        name = _tool_name(request.tool)
        reason = self._denial(name, _user_id(configurable), request.tool)
        if reason is not None:
            return self._tool_error(request, reason)
        return await handler(request)


def guarded_subagent_specs(
    specs: list[dict[str, Any]],
    middleware: CapabilityMiddleware,
    *,
    parent_tools: list[Any] | None = None,
    mcp_tool_names: frozenset[str] = frozenset(),
    mcp_server_names: frozenset[str] = frozenset(),
    turn_mcp_middleware: Any | None = None,
) -> list[dict[str, Any]]:
    """Guard every inline subagent and remove the owner's static MCP tools."""
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
    from harness_agent.mcp import filter_tools_for_mcp_servers

    specs = list(specs)
    if not any(spec.get("name") == "general-purpose" for spec in specs):
        specs.append(dict(GENERAL_PURPOSE_SUBAGENT))
    safe_parent_tools = filter_tools_for_mcp_servers(
        list(parent_tools or []),
        mcp_tool_names=mcp_tool_names,
        server_names=mcp_server_names,
        active_servers=None,
    )
    guarded: list[dict[str, Any]] = []
    for spec in specs:
        updated = dict(spec)
        if "runnable" in updated or "graph_id" in updated:
            raise ValueError("Compiled and remote subagent graphs cannot be capability-guarded")
        source_tools = updated.get("tools", safe_parent_tools)
        updated["tools"] = filter_tools_for_mcp_servers(
            list(source_tools or []),
            mcp_tool_names=mcp_tool_names,
            server_names=mcp_server_names,
            active_servers=None,
        )
        chain = list(updated.get("middleware") or [])
        if turn_mcp_middleware is not None and turn_mcp_middleware not in chain:
            chain.insert(0, turn_mcp_middleware)
        if middleware not in chain:
            chain.append(middleware)
        updated["middleware"] = chain
        guarded.append(updated)
    return guarded


def mark_mcp_tool_source(tool: object, *, server: str | None, user_id: int | None = None) -> None:
    """Attach trusted provenance to a project-registered MCP tool object."""
    object.__setattr__(tool, "__octop_mcp_source__", (server, user_id))


class CapabilityHarnessAgent(HarnessAgent):
    """Harness agent that installs the caller policy on declarative subagents."""

    def _init_mcp(self) -> None:
        super()._init_mcp()
        for tool in self._mcp_tools:
            mark_mcp_tool_source(tool, server=None)

    def append_mcp_tools(self, extra_tools: list[Any]) -> None:
        for tool in extra_tools:
            mark_mcp_tool_source(tool, server=None)
        super().append_mcp_tools(extra_tools)

    def replace_mcp_tools(self, tools: list[Any]) -> None:
        for tool in tools:
            mark_mcp_tool_source(tool, server=None)
        super().replace_mcp_tools(tools)

    def _resolve_subagents(self, *, parent_tools: list[Any] | None = None) -> list[Any]:
        specs = super()._resolve_subagents(parent_tools=parent_tools)
        middleware = next(
            (
                item
                for item in (self._config.middleware or [])
                if isinstance(item, CapabilityMiddleware)
            ),
            None,
        )
        if middleware is None:
            return specs
        from octop.infra.agents.middleware.turn_mcp import TurnMcpToolsMiddleware

        turn_mcp = next(
            (
                item
                for item in (self._config.middleware or [])
                if isinstance(item, TurnMcpToolsMiddleware)
            ),
            None,
        )
        return guarded_subagent_specs(
            specs,
            middleware,
            parent_tools=parent_tools,
            mcp_tool_names=frozenset(getattr(self, "_mcp_tool_name_set", ()) or ()),
            mcp_server_names=frozenset(getattr(self._config, "mcp_server_configs", {}) or {}),
            turn_mcp_middleware=turn_mcp,
        )


__all__ = [
    "CapabilityHarnessAgent",
    "CapabilityMiddleware",
    "guarded_subagent_specs",
    "mark_mcp_tool_source",
]
