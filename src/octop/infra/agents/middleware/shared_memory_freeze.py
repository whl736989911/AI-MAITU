"""A feature agent's memory is frozen: no run writes the ``MEMORY.md`` it hands to all.

A feature's own agent (:data:`~octop.infra.agents.kinds.KIND_FEATURE`) serves *every*
caller of its feature and has no person whose memory it could be — its author owns
its configuration, not its context. Its workspace ``MEMORY.md`` is one file for all
of them: whatever one caller prompts the agent into storing, the next caller's run
reads back and writes over. That is cross-caller context pollution rather than
personalization, so the file is frozen for such an agent. Memory keeps being
*read* — the harness still injects the file into every prompt — and a write is
refused with a message the model can act on instead of a silent no-op.

Every other agent is untouched: the freeze is mounted on a feature's agent only
(:func:`shared_memory_freeze_chain` answers ``[]`` for every other agent), so an
expert's own memory keeps updating exactly as before.

Shell is out of scope, exactly as it is for the harness's own filesystem rules: this
bounds the file tools the memory skill tells the agent to use (``write_file`` /
``edit_file`` / ``append_file`` / ``delete``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.kinds import is_feature_agent

logger = logging.getLogger(__name__)

MEMORY_FILE_NAME = "MEMORY.md"
"""The workspace memory file a feature's agent hands to every caller."""

_WRITE_TOOLS = frozenset({"append_file", "delete", "edit_file", "write_file"})
"""File tools that change a file's bytes. Read tools are never touched."""

_PATH_ARG_KEYS = ("file_path", "path")
"""Argument names deepagents' file tools carry their target in."""


def memory_is_shared(*, kind: str) -> bool:
    """True when the agent row's memory is shared with every caller.

    *kind* is the agent row's ``kind`` column: a feature's own agent serves every
    caller of that feature, and its author owns the configuration, not the
    context — nobody whose memory that file could be. This is the one predicate
    the freeze is decided by, and the agent row is the only thing it reads: no id
    convention, no ownership guess, nothing that can drift from what the row says.
    The question itself is asked in one place
    (:func:`~octop.infra.agents.kinds.is_feature_agent`).
    """
    return is_feature_agent(kind)


def _tool_base_name(tool_name: str) -> str:
    """Tool name without a namespace prefix, the way the harness matches tools."""
    return tool_name.strip().rpartition("/")[2]


def _written_path(params: Mapping[str, Any]) -> str:
    for key in _PATH_ARG_KEYS:
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _is_memory_file(path: str) -> bool:
    """True when *path* is the workspace-root ``MEMORY.md`` — the shared one.

    Case-insensitively and at the root only: the harness reads its memory files from
    the workspace root, and on the case-insensitive filesystems this product runs on
    ``memory.md`` *is* ``MEMORY.md``. A ``MEMORY.md`` the agent keeps of its own
    under some subdirectory is a different file and stays writable.
    """
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/").casefold() == MEMORY_FILE_NAME.casefold()


def frozen_memory_refusal(tool_name: str, params: Mapping[str, Any]) -> str | None:
    """The refusal for a write to the frozen memory file, or ``None`` to let it through."""
    if _tool_base_name(tool_name) not in _WRITE_TOOLS:
        return None
    if not _is_memory_file(_written_path(params)):
        return None
    return (
        f"Blocked: `{MEMORY_FILE_NAME}` is frozen. This agent belongs to the product and "
        "serves every one of its callers, so its memory is shared and a run never writes "
        "it — anything stored here would be read back by the next caller's run. The file "
        "stays readable: put what matters in your answer instead, and do not work around "
        "this."
    )


class SharedMemoryFreezeMiddleware(AgentMiddleware[Any, Any]):
    """Refuse writes to the memory file a shared agent hands to every caller."""

    def __init__(self, *, agent_id: str) -> None:
        self._agent_id = agent_id

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        raw_args: Any = tool_call.get("args") or {}
        params = raw_args if isinstance(raw_args, Mapping) else {}
        reason = frozen_memory_refusal(tool_name, params)
        if reason is None:
            return None
        # The refusal is not silent: the model gets the sentence above as the tool's
        # own result, and the operator gets this line — named by agent, because more
        # than one shared agent can be running.
        logger.info(
            "shared agent %s: refused %s on its frozen memory file",
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


def shared_memory_freeze_chain(*, agent_id: str, kind: str) -> list[Any]:
    """The freeze for a feature agent's chain, or ``[]`` for any other agent.

    *agent_id* names the agent in the refusal's log line; *kind* is the agent row's
    ``kind``, which is what the decision is made on. ``[]`` means the chain is what
    it is without this module — every other agent, experts included, keeps writing
    its own memory.
    """
    if not memory_is_shared(kind=kind):
        return []
    return [SharedMemoryFreezeMiddleware(agent_id=agent_id)]


__all__ = [
    "MEMORY_FILE_NAME",
    "SharedMemoryFreezeMiddleware",
    "frozen_memory_refusal",
    "memory_is_shared",
    "shared_memory_freeze_chain",
]
