"""Refuse writes to the profile and memory shared by a feature's callers.

The feature's workspace is the same for every caller. Its root ``USER.md`` and
``MEMORY.md`` are read into later turns, so saving one caller's identity or
preferences there exposes them to the next. Expert workspaces are private and
keep their normal write behavior.

This guard covers harness file tools, like the existing memory freeze. Shell
access is outside the harness filesystem guard's scope.
"""

from __future__ import annotations

import logging
import posixpath
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.kinds import is_feature_agent

logger = logging.getLogger(__name__)

_SHARED_FILES = {"memory.md": "MEMORY.md", "user.md": "USER.md"}
"""Case-insensitive root filenames, with their canonical display spelling."""

_WRITE_TOOLS = frozenset({"append_file", "delete", "edit_file", "write_file"})
"""File tools that change a file's bytes. Read tools are never touched."""

_PATH_ARG_KEYS = ("file_path", "path")
"""Argument names deepagents' file tools carry their target in."""


def _tool_base_name(tool_name: str) -> str:
    """Tool name without a namespace prefix, the way the harness matches tools."""
    return tool_name.strip().rpartition("/")[2]


def _written_path(params: Mapping[str, Any]) -> str:
    for key in _PATH_ARG_KEYS:
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _shared_file_name(path: str) -> str | None:
    """Canonical name of a shared root file, if *path* names one."""
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return _SHARED_FILES.get(normalized.lstrip("/").casefold())


def frozen_shared_file_refusal(tool_name: str, params: Mapping[str, Any]) -> str | None:
    """Explain a blocked write to the feature's shared root profile or memory."""
    if _tool_base_name(tool_name) not in _WRITE_TOOLS:
        return None
    file_name = _shared_file_name(_written_path(params))
    if file_name is None:
        return None
    return (
        f"Blocked: `{file_name}` is frozen. This feature serves every one of its callers; "
        "its profile and memory are shared, so a run never writes personal information "
        "there for the next caller to read. The files stay readable. Put caller-specific "
        "information in this response instead, and do not work around this."
    )


class SharedWorkspaceFreezeMiddleware(AgentMiddleware[Any, Any]):
    """Refuse feature writes to the shared root profile and memory files."""

    def __init__(self, *, agent_id: str) -> None:
        self._agent_id = agent_id

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        raw_args: Any = tool_call.get("args") or {}
        params = raw_args if isinstance(raw_args, Mapping) else {}
        reason = frozen_shared_file_refusal(tool_name, params)
        if reason is None:
            return None
        # The refusal is not silent: the model gets the sentence above as the tool's
        # own result, and the operator gets this line — named by agent, because more
        # than one shared agent can be running.
        logger.info(
            "shared agent %s: refused %s on its frozen workspace file",
            self._agent_id,
            tool_name,
        )
        return ToolMessage(
            content=reason,
            tool_call_id=str(tool_call.get("id") or ""),
            status="error",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._refusal(request)
        if blocked is not None:
            return blocked
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._refusal(request)
        if blocked is not None:
            return blocked
        return handler(request)


def shared_workspace_freeze_chain(*, agent_id: str, kind: str) -> list[Any]:
    """Protect a feature's shared root files; leave every expert untouched."""
    if not is_feature_agent(kind):
        return []
    return [SharedWorkspaceFreezeMiddleware(agent_id=agent_id)]


__all__ = [
    "SharedWorkspaceFreezeMiddleware",
    "frozen_shared_file_refusal",
    "shared_workspace_freeze_chain",
]
