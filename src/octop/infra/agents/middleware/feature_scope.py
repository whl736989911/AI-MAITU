"""What one feature run may use — its tools, its subagents, its model knobs.

An enterprise feature declares its capability layer once (``feature.json``'s
``agent`` node) and every run of it is bound by that declaration: the feature's
author decides which tools, subagents and sampling settings a run gets, and the
caller keeps owning the data (knowledge bases, connector credentials — design
5.2, resolved before this middleware ever runs).

The pieces that already have a per-run channel need nothing here: the model rides
``request["model"]``, sampling and budget knobs ride
``configurable["octop_agent_runtime_overrides"]``
(:mod:`octop.infra.agents.runtime_limits`), skills ride ``configurable["skills"]``
(harness ``SkillFilterMiddleware``), and MCP rides
``configurable["mcp_servers"]``. The two below have no such channel —
``tools_disabled`` is an agent-level set, and subagents are compiled into the
``task`` tool's description at graph build time — so the router stamps them onto
``configurable`` and this middleware is the layer that applies them:

- :meth:`wrap_model_call` drops the disabled tools from the model's tool list and
  narrows the ``task`` tool to the allowed subagents.
- :meth:`wrap_tool_call` refuses a call for either one anyway. ``ToolNode``
  defers validation to the interceptor, so a hidden name is still callable — the
  same reason ``TurnMcpToolsMiddleware`` executes the caller's own tool here.

A run that stamps nothing passes through untouched: chat turns, cron delivery,
IM messages and rule extraction keep the tool surface their agent has.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

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

CONFIG_KEY = "octop_feature_scope"
"""``configurable`` entry carrying one feature run's capability scope."""

TASK_TOOL_NAME = "task"
"""Subagent dispatch tool (deepagents ``SubAgentMiddleware``)."""

_TASK_AGENTS_HEADER = "Available agent types and the tools they have access to:"
_ALLOWED_SUBAGENTS_LINE = "Only these subagent types may be used in this run: {names}"

ModelCallResult = ModelResponse[Any] | AIMessage | ExtendedModelResponse[Any]


@dataclass(frozen=True)
class FeatureRunScope:
    """The capability layer of one feature, already resolved for its caller.

    ``tools_disabled`` is fixed by the definition; ``subagents`` is ``None`` when
    the feature declares nothing ("inherit") and a tuple otherwise — including an
    empty one, which means the run may dispatch no subagent at all.
    """

    tools_disabled: tuple[str, ...] = ()
    subagents: tuple[str, ...] | None = None

    def is_empty(self) -> bool:
        return not self.tools_disabled and self.subagents is None


def stamp_feature_scope(request: dict[str, Any], scope: FeatureRunScope) -> None:
    """Write *scope* onto one harness request, in place.

    An empty scope (a feature that declares no capability layer) leaves the
    request untouched, so its run stays byte-identical to a run of a feature that
    predates this layer.
    """
    if scope.is_empty():
        return
    configurable = dict(request.get("configurable") or {})
    configurable[CONFIG_KEY] = {
        "tools_disabled": list(scope.tools_disabled),
        "subagents": None if scope.subagents is None else list(scope.subagents),
    }
    request["configurable"] = configurable


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


def feature_scope_from_config(configurable: Mapping[str, Any]) -> FeatureRunScope | None:
    """Read a stamped scope back; ``None`` when this run carries none."""
    raw = configurable.get(CONFIG_KEY)
    if not isinstance(raw, Mapping):
        return None
    disabled = raw.get("tools_disabled")
    subagents = raw.get("subagents")
    return FeatureRunScope(
        tools_disabled=tuple(str(name) for name in disabled) if isinstance(disabled, list) else (),
        subagents=tuple(str(name) for name in subagents) if isinstance(subagents, list) else None,
    )


def tool_name(tool: object) -> str:
    """Name of a tool or raw tool schema, whichever the request carries."""
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _restricted_task_description(description: str, allowed: tuple[str, ...]) -> str:
    """A ``task`` tool description that only offers *allowed* subagents.

    The types are the ``- name: description`` bullets deepagents renders under its
    own header. A template this does not recognise is left as it was and gets one
    explicit sentence instead — the run still refuses the others at call time, and
    guessing at a changed layout would silently advertise subagents that cannot be
    used.
    """
    allowed_line = _ALLOWED_SUBAGENTS_LINE.format(
        names=", ".join(allowed) if allowed else "(none)"
    )
    lines = description.splitlines()
    header_at = next(
        (index for index, line in enumerate(lines) if line.strip() == _TASK_AGENTS_HEADER),
        None,
    )
    if header_at is None:
        return f"{description.rstrip()}\n\n{allowed_line}"

    allowed_set = set(allowed)
    kept = lines[: header_at + 1]
    index = header_at + 1
    while index < len(lines) and lines[index].startswith("- "):
        name = lines[index][2:].split(":", 1)[0].strip()
        if name in allowed_set:
            kept.append(lines[index])
        index += 1
    kept.extend(lines[index:])
    kept.extend(["", allowed_line])
    return "\n".join(kept)


def _restrict_task_tool(tool: BaseTool, allowed: tuple[str, ...]) -> BaseTool:
    """A copy of the ``task`` tool that offers only *allowed* subagents."""
    description = str(getattr(tool, "description", "") or "")
    restricted = _restricted_task_description(description, allowed)
    if restricted == description:
        return tool
    return tool.model_copy(update={"description": restricted})


class FeatureScopeMiddleware(AgentMiddleware[Any, Any]):
    """Apply a feature run's declared capability scope to that run only."""

    def _scope(self) -> FeatureRunScope | None:
        return feature_scope_from_config(_run_configurable())

    def _apply(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        scope = self._scope()
        if scope is None:
            return request

        tools = list(request.tools or [])
        if scope.tools_disabled:
            disabled = set(scope.tools_disabled)
            tools = [tool for tool in tools if tool_name(tool) not in disabled]

        if scope.subagents is not None:
            # An empty allow-list hides ``task`` outright: a tool the run may
            # never use would be a standing invitation to call it.
            if not scope.subagents:
                tools = [tool for tool in tools if tool_name(tool) != TASK_TOOL_NAME]
            else:
                tools = [
                    _restrict_task_tool(tool, scope.subagents)
                    if isinstance(tool, BaseTool) and tool_name(tool) == TASK_TOOL_NAME
                    else tool
                    for tool in tools
                ]

        updated = request.override(tools=tools) if tools != list(request.tools or []) else request
        return updated

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult:
        return handler(self._apply(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult:
        return await handler(self._apply(request))

    # ------------------------------------------------------------------
    # Tool call — a hidden tool or subagent is refused, never served
    # ------------------------------------------------------------------

    def _refusal(self, request: ToolCallRequest) -> str | None:
        """Why this call is out of scope, or ``None`` when it is allowed."""
        scope = self._scope()
        if scope is None:
            return None
        name = str(request.tool_call.get("name") or "")
        if name in set(scope.tools_disabled):
            logger.info("feature scope: refused disabled tool %s", name)
            return (
                f"Tool {name!r} is not available in this run: it is disabled by the "
                "feature's configuration."
            )
        if name != TASK_TOOL_NAME or scope.subagents is None:
            return None
        args = request.tool_call.get("args")
        requested = str((args if isinstance(args, dict) else {}).get("subagent_type") or "")
        if requested in set(scope.subagents):
            return None
        allowed = ", ".join(scope.subagents) if scope.subagents else "(none)"
        logger.info("feature scope: refused subagent %s", requested)
        return (
            f"Subagent {requested!r} is not available in this run: this feature may only "
            f"dispatch {allowed}."
        )

    @staticmethod
    def _message(request: ToolCallRequest, reason: str) -> ToolMessage:
        return ToolMessage(
            content=reason,
            tool_call_id=str(request.tool_call.get("id") or ""),
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        reason = self._refusal(request)
        if reason is not None:
            return self._message(request, reason)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        reason = self._refusal(request)
        if reason is not None:
            return self._message(request, reason)
        return await handler(request)


__all__ = [
    "CONFIG_KEY",
    "TASK_TOOL_NAME",
    "FeatureRunScope",
    "FeatureScopeMiddleware",
    "feature_scope_from_config",
    "stamp_feature_scope",
]
