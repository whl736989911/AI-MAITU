"""Shared workspace helpers (not route handlers)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from harness_agent.backends.workspace import BackendWorkspace

from octop.api.common.agent import (
    AgentCapability,
    require_agent_capability_row,
    require_agent_owner_row,
    require_agent_row,
)
from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from harness_agent import HarnessAgent

# deepagents.backends.utils.EMPTY_CONTENT_WARNING — shown to LLM tools, not humans.
_DEEPAGENTS_EMPTY_WARNING = "System reminder: File exists but has empty contents"


def coerce_read_content(content: Any) -> str:
    """Normalize backend read payloads for the dashboard file viewer."""
    if content is None:
        return ""
    if isinstance(content, list):
        content = "\n".join(str(line) for line in content)
    text = str(content)
    if text == _DEEPAGENTS_EMPTY_WARNING:
        return ""
    return text


def require_running_agent(server: Any, agent_id: str) -> HarnessAgent:
    """Return the live harness agent or raise not-running / not-found errors."""
    assert server.app_runtime is not None
    return cast("HarnessAgent", server.app_runtime.agent_registry.get_agent(agent_id))


def _row_for(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
    owner_only: bool,
    capability: AgentCapability | None,
) -> Any:
    """The agent row, checked the way this module's callers declare it.

    *capability* names the group a **mutating** endpoint is about to write, and
    carries the matrix's rule for it (:func:`agent_capability_refusal`). It is the
    way to ask for a workspace write, so ``owner_only`` stays what it always was —
    a check on the agent itself — and is not passed together with a capability.
    """
    if capability is not None:
        if owner_only:
            raise ValueError("pass capability or owner_only, not both")
        return require_agent_capability_row(
            agent_id,
            user=user,
            as_user=as_user,
            server=server,
            capability=capability,
        )
    if owner_only:
        return require_agent_owner_row(agent_id, user=user, as_user=as_user, server=server)
    return require_agent_row(agent_id, user=user, as_user=as_user, server=server)


async def require_running_workspace(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
    owner_only: bool = False,
    capability: AgentCapability | None = None,
) -> BackendWorkspace:
    """Auth-checked :class:`BackendWorkspace` for a running agent.

    PATCH /agents triggers a background ``arebuild_agent`` that briefly
    removes the live harness handle while the DB row still says ``running``.
    Workspace file I/O does not need the compiled graph, so during that
    window we fall back to :meth:`AgentManager.workspace_for_agent`.
    Stopped / failed agents still raise — the fallback is only for the
    rebuild gap, not a way to edit a stopped workspace through this helper.
    """
    row = _row_for(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        owner_only=owner_only,
        capability=capability,
    )
    try:
        return require_running_agent(server, agent_id).workspace
    except OctopError as exc:
        if exc.code is not ErrorCode.AGENT_NOT_RUNNING:
            raise
        state = str(getattr(row, "last_state", "") or "").strip().lower()
        if state != "running":
            raise
        assert server.app_runtime is not None
        fallback = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
        if fallback is None:
            raise
        return cast("BackendWorkspace", fallback)


async def require_agent_workspace(
    agent_id: str,
    *,
    user: Any,
    server: Any,
    owner_only: bool = False,
    capability: AgentCapability | None = None,
) -> BackendWorkspace:
    """Auth-checked workspace even when the agent is stopped.

    Used for display files (e.g. expert avatar) that must work from the
    experts list without requiring a running harness handle.
    """
    _row_for(
        agent_id,
        user=user,
        as_user=None,
        server=server,
        owner_only=owner_only,
        capability=capability,
    )
    assert server.app_runtime is not None
    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
    return cast("BackendWorkspace", workspace)


def workspace_api_path(raw: str) -> str:
    """Map dashboard ``/`` / ``/foo`` paths to workspace-relative fragments for I/O."""
    text = raw.strip().replace("\\", "/")
    if not text or text == "/":
        return "."
    return text.lstrip("/")


def reanchor_entry_path(entry_path: str, *, parent: str) -> str:
    """Anchor a single-level listing entry under *parent* (the requested dir).

    A directory listing only carries entry names, so the dashboard must get
    ``{requested}/{name}`` back — otherwise a backend that presents longer
    paths yields keys the workspace API cannot resolve on the next request.
    """
    name = entry_path.strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    base = parent.strip().replace("\\", "/").rstrip("/")
    if base in ("", "."):
        return name
    return f"{base}/{name}" if name else base


def file_info_to_dict(info: Any) -> dict[str, Any]:
    """Coerce a ``FileInfo`` TypedDict into a JSON-friendly dict."""
    if isinstance(info, dict):
        path = info.get("path")
        is_dir = info.get("is_dir")
        size = info.get("size")
        modified_at = info.get("modified_at")
    else:
        path = getattr(info, "path", None)
        is_dir = getattr(info, "is_dir", None)
        size = getattr(info, "size", None)
        modified_at = getattr(info, "modified_at", None)
    out: dict[str, Any] = {"path": path}
    if is_dir is not None:
        out["is_dir"] = bool(is_dir)
    if size is not None:
        out["size"] = int(size)
    if modified_at is not None:
        out["modified_at"] = modified_at
    return out
