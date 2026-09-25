"""Scoped ``harness_memory`` dashboard clients for agents and feature callers.

A feature's existing agent namespace is its shared training memory. Once its
workflow is active, API calls default to the authenticated caller's private
namespace; explicitly selecting shared permits reads but never writes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from octop.api.common.agent import (
    AgentCapability,
    assert_agent_capability_write,
    require_agent_owner_row,
    require_agent_row,
)
from octop.api.common.workspace import require_agent_workspace
from octop.i18n import tr
from octop.infra.agents.feature_workflow import STATUS_ACTIVE, STATUS_DRAFT, feature_memory_stage
from octop.infra.agents.kinds import is_feature_agent
from octop.infra.agents.memory_backend import memory_namespace, open_memory_kwargs
from octop.infra.agents.workspace_dir import host_system_dir
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.locale import resolve_user_locale

logger = logging.getLogger(__name__)

MemoryScope = Literal["shared", "private"]


@dataclass(frozen=True)
class MemoryAccess:
    """The authenticated caller's feature-memory view for one API request."""

    stage: Literal["draft", "active"] | None
    default_scope: MemoryScope
    shared_writable: bool
    private_writable: bool


def feature_memory_reason(key: str, *, user: Any, server: Any) -> str:
    """Localize a feature memory refusal using the authenticated user's preference."""
    locale = resolve_user_locale(user_repo=server.services.user_repo, user_id=int(user.id))
    return tr(f"feature_memory.{key}", locale)


async def resolve_memory_access(
    agent_id: str, *, user: Any, as_user: int | None, server: Any
) -> MemoryAccess:
    row = require_agent_row(agent_id, user=user, as_user=as_user, server=server)
    if not is_feature_agent(row.kind):
        require_agent_owner_row(agent_id, user=user, as_user=as_user, server=server)
        return MemoryAccess(
            stage=None, default_scope="shared", shared_writable=True, private_writable=False
        )
    workspace = await require_agent_workspace(agent_id, user=user, server=server)
    stage = cast('Literal["draft", "active"]', await feature_memory_stage(workspace))
    author = user.is_admin or row.user_id == user.id
    if stage == STATUS_DRAFT and not author:
        reason = feature_memory_reason("draft_author_only", user=user, server=server)
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    return MemoryAccess(
        stage=stage,
        default_scope="private" if stage == STATUS_ACTIVE else "shared",
        shared_writable=stage == STATUS_DRAFT and author,
        private_writable=stage == STATUS_ACTIVE and (as_user is None or as_user == user.id),
    )


def memory_db_path(workspace_dir: Path) -> Path:
    """Return the default SQLite file path within an agent workspace.

    Prefer an explicit on-disk file. When neither exists, treat a populated
    ``.octop/_builtin_skills`` tree as the new-layout signal (empty ``.octop/``
    alone is not enough for legacy agents).
    """
    nested = workspace_dir / ".octop" / "memory.sqlite"
    root = workspace_dir / "memory.sqlite"
    if nested.exists():
        return nested
    if root.exists():
        return root
    if (workspace_dir / ".octop" / "_builtin_skills").is_dir():
        return nested
    return root


def memory_db_path_for_cfg(workspace_dir: Path, cfg: dict[str, Any] | None) -> Path:
    """Return the default SQLite file path for a specific agent config."""
    return host_system_dir(workspace_dir, cfg) / "memory.sqlite"


_MAX_CACHED = 16


class _MemoryCache:
    """Thread-safe LRU of ``namespace -> (memory, bridge, backend fingerprint)``."""

    def __init__(self, max_size: int = _MAX_CACHED) -> None:
        self._max_size = max_size
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[Any, Any, str]] = OrderedDict()

    def get_or_open(
        self,
        namespace: str,
        *,
        backend: str,
        backend_config: dict[str, Any] | None,
        fingerprint: str,
    ) -> tuple[Any, Any]:
        from harness_memory.adapters.bridge.handlers import Bridge  # noqa: PLC0415
        from harness_memory.core import Memory  # noqa: PLC0415

        with self._lock:
            cached = self._entries.get(namespace)
            if cached is not None and cached[2] == fingerprint:
                self._entries.move_to_end(namespace)
                return cached[0], cached[1]

            memory = Memory(
                namespace=namespace,
                backend=backend,
                backend_config=backend_config,
            )
            bridge = Bridge(memory)
            self._entries[namespace] = (memory, bridge, fingerprint)
            while len(self._entries) > self._max_size:
                evicted_namespace, _ = self._entries.popitem(last=False)
                logger.debug("memory dashboard cache evicted namespace=%s", evicted_namespace)
            return memory, bridge

    def invalidate(self, agent_id: str | None = None) -> None:
        with self._lock:
            if agent_id is None:
                self._entries.clear()
            else:
                prefix = memory_namespace(agent_id)
                for namespace in tuple(self._entries):
                    if namespace == prefix or namespace.startswith(f"{prefix}_user_"):
                        self._entries.pop(namespace, None)


_CACHE = _MemoryCache()


def _open_memory_for_agent(
    server: Any, agent_id: str, *, user_id: int | None = None
) -> tuple[Any, Any]:
    from octop.api.common.agent_workspace import resolve_agent_workspace_dir  # noqa: PLC0415

    workspace = resolve_agent_workspace_dir(server, agent_id)
    row = server.services.agent_repo.get(agent_id)
    cfg: dict[str, Any] = {}
    if row is not None and row.config_json:
        import json  # noqa: PLC0415

        try:
            parsed = json.loads(row.config_json)
            if isinstance(parsed, dict):
                cfg = parsed
        except json.JSONDecodeError:
            cfg = {}

    ns, backend, backend_config = open_memory_kwargs(
        agent_id=agent_id,
        user_id=user_id,
        cfg=cfg,
        octop_config=server.services.config,
        workspace_dir=workspace,
    )
    fingerprint = f"{ns}:{backend}:{backend_config}"
    if backend == "sqlite":
        db_path = Path(
            (backend_config or {}).get("db_path") or memory_db_path_for_cfg(workspace, cfg)
        )
        if not db_path.exists():
            logger.debug(
                "memory db not yet created for agent %s; opening will create empty schema",
                agent_id,
            )
    return _CACHE.get_or_open(
        ns,
        backend=backend,
        backend_config=backend_config,
        fingerprint=fingerprint,
    )


async def call_memory_rpc(
    *,
    agent_id: str,
    method: str,
    params: dict[str, Any] | None,
    user: Any,
    as_user: int | None,
    server: Any,
    capability: AgentCapability | None = None,
    scope: MemoryScope | None = None,
) -> Any:
    """Dispatch to a namespace selected by stage and authenticated caller.

    The query can choose *which* of the caller's two scopes to inspect, never
    whose private namespace it is. A caller cannot turn a shared feature memory
    write into a private one by sending another user's id.
    """
    access = await resolve_memory_access(agent_id, user=user, as_user=as_user, server=server)
    selected = scope or access.default_scope
    if selected not in ("shared", "private"):
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"unknown memory scope {selected!r}")
    if selected == "private" and (
        not access.private_writable or (as_user is not None and as_user != user.id)
    ):
        reason = feature_memory_reason("private_caller_only", user=user, server=server)
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    if selected == "shared" and capability is not None and not access.shared_writable:
        reason = feature_memory_reason("shared_read_only", user=user, server=server)
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    if capability is not None:
        row = require_agent_row(agent_id, user=user, as_user=as_user, server=server)
        assert_agent_capability_write(
            row, user, capability, memory_stage=access.stage, memory_scope=selected
        )
    runtime = server.app_runtime
    coordinator = runtime.agent_registry.memory_slim if runtime is not None else None
    if coordinator is not None:
        status = coordinator.status(agent_id)
        if isinstance(status, dict) and status.get("phase") in {
            "backing_up",
            "deduplicating",
            "compacting",
        }:
            # Avoid synchronous dashboard writes waiting on the maintenance SQLite lock.
            raise OctopError.localized(ErrorCode.AGENT_BUSY)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }

    def dispatch() -> dict[str, Any]:
        from harness_memory.storage.backends.sqlite import SqliteMemoryBackend  # noqa: PLC0415

        memory, bridge = _open_memory_for_agent(
            server, agent_id, user_id=user.id if selected == "private" else None
        )
        if isinstance(memory.backend, SqliteMemoryBackend):
            # The upstream transaction flag is set before its first per-thread
            # connection is bound; binding resets the flag and commits early.
            # Prime this worker's connection before any bridge write begins.
            _ = memory.backend._conn
        return cast("dict[str, Any]", bridge.handle(payload))

    response = await asyncio.to_thread(dispatch)
    if "error" in response:
        err = response["error"]
        code = err.get("code")
        message = err.get("message", "memory bridge error")
        if code == -32010:  # ERR_PATH_NOT_FOUND
            raise OctopError(ErrorCode.NOT_FOUND, message)
        if code == -32602:  # ERR_INVALID_PARAMS
            raise OctopError(ErrorCode.INTERNAL_ERROR, message, status=400)
        if code == -32601:  # ERR_METHOD_NOT_FOUND
            raise OctopError(
                ErrorCode.INTERNAL_ERROR,
                f"unknown memory dashboard method: {method!r}",
            )
        raise OctopError(ErrorCode.INTERNAL_ERROR, message)
    return response["result"]


def invalidate_cached_memory(agent_id: str | None = None) -> None:
    _CACHE.invalidate(agent_id)


__all__ = [
    "MemoryAccess",
    "feature_memory_reason",
    "MemoryScope",
    "call_memory_rpc",
    "invalidate_cached_memory",
    "memory_db_path",
    "memory_db_path_for_cfg",
    "resolve_memory_access",
]
