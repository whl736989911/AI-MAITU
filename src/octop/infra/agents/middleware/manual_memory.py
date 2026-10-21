"""Session snapshots and write protection for manual USER/MEMORY documents."""

from __future__ import annotations

import asyncio
import logging
import posixpath
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from typing_extensions import TypeIs

from octop.infra.agents.kinds import is_feature_agent
from octop.infra.agents.memory_context import (
    MANUAL_CONTEXT_FILES,
    canonical_manual_document,
    managed_workspace_user_file,
    read_private_document,
    read_workspace_document,
)
from octop.infra.agents.middleware.feature_memory import (
    FeatureMemoryRuntime,
    turn_feature_memory_context,
)

logger = logging.getLogger(__name__)

_PRIVATE_SNAPSHOT_KEY = "octop_private_manual_docs"
_WORKSPACE_SNAPSHOT_KEY = "octop_workspace_manual_docs"
_PRIVATE_NAMESPACE_KEY = "octop_private_manual_namespace"
_WORKSPACE_VERSION_KEY = "octop_workspace_manual_versions"
_SEALED_EMPTY = "__sealed__"
_WRITE_TOOLS = frozenset({"append_file", "delete", "edit_file", "write_file"})
_PATH_ARG_KEYS = ("file_path", "path")

_OWNER_MEMORY_TEMPLATE = """<owner_memory>
{owner_memory}

</owner_memory>

<owner_memory_guidelines>
The documents above are manually maintained and authoritative for this conversation. Prefer them over recalled memory items when they disagree, and surface the disagreement instead of silently choosing. They are not writable through file tools; use the dashboard editor.
</owner_memory_guidelines>"""


def filter_native_memory_sources(sources: Any) -> tuple[Any, ...]:
    """Exclude manual context files from native host-file memory loading.

    A ``None`` source setting means the harness defaults, which include
    USER.md/MEMORY.md; return those defaults with just the manual files removed.
    An explicit empty setting remains empty (bootstrap suppression).
    """
    from harness_agent.backends.workspace import DEFAULT_MEMORY_FILES

    selected = DEFAULT_MEMORY_FILES if sources is None else sources
    if isinstance(selected, (str, bytes)):
        selected = (selected,)
    try:
        return tuple(
            source
            for source in selected
            if canonical_manual_document(posixpath.basename(str(source).replace("\\", "/"))) is None
        )
    except TypeError:
        return ()


def _tool_base_name(tool_name: str) -> str:
    return tool_name.strip().rpartition("/")[2]


def _written_path(params: Mapping[str, Any]) -> str:
    for key in _PATH_ARG_KEYS:
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _root_document_name(path: str) -> str | None:
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    stripped = normalized.lstrip("/")
    if "/" in stripped:
        return None
    return canonical_manual_document(stripped)


def _state_get(state: Any, key: str) -> Any:
    if isinstance(state, Mapping):
        return state.get(key)
    getter = getattr(state, "get", None)
    return getter(key) if callable(getter) else None


def _is_error_message(value: Any) -> TypeIs[ToolMessage]:
    return isinstance(value, ToolMessage)


class ManualMemoryState(AgentState):
    """Thread state containing immutable manual-document snapshots."""

    octop_private_manual_docs: NotRequired[Annotated[dict[str, str], PrivateStateAttr]]
    octop_workspace_manual_docs: NotRequired[Annotated[dict[str, str], PrivateStateAttr]]
    octop_private_manual_namespace: NotRequired[Annotated[str, PrivateStateAttr]]
    octop_workspace_manual_versions: NotRequired[Annotated[dict[str, str], PrivateStateAttr]]


class WorkspaceManualMemoryMiddleware(AgentMiddleware[ManualMemoryState, Any, ResponseT]):
    """Snapshot shared workspace documents once per thread and inject each call."""

    state_schema = ManualMemoryState

    def __init__(self, *, agent_id: str, workspace: Any) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._workspace = workspace

    def _snapshot(self, state: ManualMemoryState) -> dict[str, Any] | None:
        if _state_get(state, _WORKSPACE_SNAPSHOT_KEY) is not None:
            return None
        documents: dict[str, str] = {}
        versions: dict[str, str] = {}
        try:
            for name in MANUAL_CONTEXT_FILES:
                document = read_workspace_document(self._workspace, name)
                documents[name] = document.text
                versions[name] = document.snapshot
        except Exception:
            logger.warning(
                "workspace manual documents could not be snapshotted for %s",
                self._agent_id,
                exc_info=True,
            )
            documents = {}
            versions = {}
        documents[_SEALED_EMPTY] = ""
        versions[_SEALED_EMPTY] = ""
        return {
            _WORKSPACE_SNAPSHOT_KEY: documents,
            _WORKSPACE_VERSION_KEY: versions,
        }

    def before_agent(self, state: ManualMemoryState, runtime: Any) -> dict[str, Any] | None:
        return self._snapshot(state)

    async def abefore_agent(self, state: ManualMemoryState, runtime: Any) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._snapshot, state)

    def _modify(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        stamped = request.state.get(_WORKSPACE_SNAPSHOT_KEY)
        if not isinstance(stamped, Mapping):
            return request
        sections = [
            f"{name}\n\n{text.strip()}"
            for name, text in stamped.items()
            if name != _SEALED_EMPTY and isinstance(text, str) and text.strip()
        ]
        if not sections:
            return request
        from deepagents.middleware._utils import append_to_system_message

        block = _OWNER_MEMORY_TEMPLATE.format(owner_memory="\n\n".join(sections))
        system = append_to_system_message(request.system_message, block)
        return (
            request if system is request.system_message else request.override(system_message=system)
        )

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Any]
    ) -> Any:
        return handler(self._modify(request))

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[Any]]
    ) -> Any:
        return await handler(self._modify(request))


class PrivateManualMemoryMiddleware(AgentMiddleware[ManualMemoryState, Any, ResponseT]):
    """Snapshot only the verified caller's private documents for a feature thread."""

    state_schema = ManualMemoryState

    def __init__(self, *, agent_id: str, runtime: FeatureMemoryRuntime) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._runtime = runtime

    def _snapshot(self, state: ManualMemoryState) -> dict[str, Any] | None:
        context = turn_feature_memory_context()
        namespace = context.private_namespace if context is not None else None
        if _state_get(state, _PRIVATE_SNAPSHOT_KEY) is not None and _state_get(
            state, _PRIVATE_NAMESPACE_KEY
        ) == (namespace or ""):
            return None
        if not namespace:
            # Fail closed even when state happens to contain a previous caller's
            # private snapshot.
            return {
                _PRIVATE_SNAPSHOT_KEY: {_SEALED_EMPTY: ""},
                _PRIVATE_NAMESPACE_KEY: "",
            }
        service = self._runtime.service(namespace)
        memory = getattr(service, "memory", None) if service is not None else None
        documents: dict[str, str] = {}
        if memory is not None:
            for name in MANUAL_CONTEXT_FILES:
                try:
                    document = read_private_document(memory, name)
                except Exception:
                    logger.warning(
                        "private document load failed for %s/%s",
                        self._agent_id,
                        name,
                        exc_info=True,
                    )
                    continue
                if document.text.strip():
                    documents[name] = document.text
        documents[_SEALED_EMPTY] = ""
        return {
            _PRIVATE_SNAPSHOT_KEY: documents,
            _PRIVATE_NAMESPACE_KEY: namespace,
        }

    def before_agent(self, state: ManualMemoryState, runtime: Any) -> dict[str, Any] | None:
        return self._snapshot(state)

    async def abefore_agent(self, state: ManualMemoryState, runtime: Any) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._snapshot, state)

    def _modify(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        stamped = request.state.get(_PRIVATE_SNAPSHOT_KEY)
        if not isinstance(stamped, Mapping):
            return request
        sections = [
            f"{name}\n\n{text.strip()}"
            for name, text in stamped.items()
            if name != _SEALED_EMPTY and isinstance(text, str) and text.strip()
        ]
        if not sections:
            return request
        from deepagents.middleware._utils import append_to_system_message

        block = _OWNER_MEMORY_TEMPLATE.format(owner_memory="\n\n".join(sections))
        system = append_to_system_message(request.system_message, block)
        return (
            request if system is request.system_message else request.override(system_message=system)
        )

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Any]
    ) -> Any:
        return handler(self._modify(request))

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[Any]]
    ) -> Any:
        return await handler(self._modify(request))


class ExpertManualMemoryGuardMiddleware(AgentMiddleware[ManualMemoryState, Any, ResponseT]):
    """Block stale writes to manual workspace docs and generated clinical USER.md."""

    state_schema = ManualMemoryState

    def __init__(self, *, agent_id: str, workspace: Any) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._workspace = workspace

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if _tool_base_name(str(tool_call.get("name") or "")) not in _WRITE_TOOLS:
            return None
        raw_args = tool_call.get("args") or {}
        params = raw_args if isinstance(raw_args, Mapping) else {}
        name = _root_document_name(_written_path(params))
        if name is None:
            return None
        call_id = str(tool_call.get("id") or "")
        if name == "USER.md" and managed_workspace_user_file(self._workspace):
            return ToolMessage(
                content=(
                    f"Blocked: `{name}` is generated by this agent's clinical profile script. "
                    "It is read-only; profile changes go through the structured profile, not file edits."
                ),
                tool_call_id=call_id,
                status="error",
            )
        versions = _state_get(request.state, _WORKSPACE_VERSION_KEY)
        stamped = versions.get(name) if isinstance(versions, Mapping) else None
        if not isinstance(stamped, str):
            return ToolMessage(
                content=f"Blocked: `{name}` has no authoritative session snapshot; keep the useful content in your reply.",
                tool_call_id=call_id,
                status="error",
            )
        try:
            current = read_workspace_document(self._workspace, name)
        except Exception:
            logger.warning(
                "manual memory guard could not read %s; refusing write", name, exc_info=True
            )
            current = None
        if current is None or current.snapshot != stamped:
            return ToolMessage(
                content=f"Blocked: `{name}` changed after this session loaded it. Preserve your useful content in the reply; the owner's version stays authoritative.",
                tool_call_id=call_id,
                status="error",
            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._refusal(request)
        if _is_error_message(blocked):
            logger.info("agent %s: refused stale manual document write", self._agent_id)
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = await asyncio.to_thread(self._refusal, request)
        if _is_error_message(blocked):
            logger.info("agent %s: refused stale manual document write", self._agent_id)
            return blocked
        return await handler(request)


def manual_memory_chain(
    *,
    agent_id: str,
    kind: str,
    workspace: Any,
    feature_runtime: Any = None,
) -> list[Any]:
    """Mount workspace snapshots and any applicable private feature snapshot."""
    chain: list[Any] = []
    if workspace is not None:
        chain.append(WorkspaceManualMemoryMiddleware(agent_id=agent_id, workspace=workspace))
    if is_feature_agent(kind) and feature_runtime is not None:
        chain.append(PrivateManualMemoryMiddleware(agent_id=agent_id, runtime=feature_runtime))
    if workspace is not None:
        chain.append(ExpertManualMemoryGuardMiddleware(agent_id=agent_id, workspace=workspace))
    return chain


__all__ = [
    "ExpertManualMemoryGuardMiddleware",
    "ManualMemoryState",
    "PrivateManualMemoryMiddleware",
    "WorkspaceManualMemoryMiddleware",
    "filter_native_memory_sources",
    "manual_memory_chain",
]
