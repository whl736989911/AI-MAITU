"""Resolve agent memory storage backend for harness-agent / harness-memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octop.config import OctopConfig
from octop.infra.agents.workspace_dir import host_system_dir
from octop.infra.errors import ErrorCode, OctopError


def memory_namespace(agent_id: str, user_id: int | None = None) -> str:
    """The canonical memory namespace for an agent, optionally scoped to a user.

    ``user_id=None`` is the agent's *shared* namespace — the exact
    ``agent_{agent_id}`` string every expert and every feature has always had.
    A user id maps to that caller's *private* namespace
    ``agent_{agent_id}_user_{user_id}``, the partition a feature's active-stage
    caller reads and writes as their own.

    harness-memory builds SQLite table names as ``{namespace}_*``, so the
    result must stay a usable identifier stem; only a positive integer user id
    is accepted, and anything else is a caller bug worth raising on rather
    than silently redirecting writes into the shared namespace.
    """
    if user_id is None:
        return f"agent_{agent_id}"
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError(f"user_id must be a positive integer or None, got {user_id!r}")
    return f"agent_{agent_id}_user_{user_id}"


def memory_backend_from_agent_config(
    cfg: dict[str, Any],
    *,
    octop_config: OctopConfig,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Return HarnessAgentConfig kwargs for memory storage (may be empty).

    Recognized ``config_json.memory.backend`` shapes:

    * omitted / null → empty on SQLite control plane (harness default
      ``memory.sqlite``); on PostgreSQL control plane, default to the same
      DSN with per-agent schema (``use_control_plane_dsn``)
    * ``{"type": "sqlite", "db_path": "..."}`` (db_path optional)
    * ``{"type": "postgres", "dsn": "..."}``
    * ``{"type": "postgres", "use_control_plane_dsn": true}``
    """
    mem = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    backend = mem.get("backend") if isinstance(mem, dict) else None
    if backend is None:
        if octop_config.database.is_postgresql:
            return {
                "memory_backend": {
                    "type": "postgres",
                    "dsn": octop_config.database.postgresql_conninfo(),
                }
            }
        return {}
    if not isinstance(backend, dict):
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "memory.backend must be an object")

    btype = str(backend.get("type") or "sqlite").strip().lower()
    if btype == "sqlite":
        db_path = backend.get("db_path")
        if not db_path and workspace_dir is not None:
            db_path = str(host_system_dir(workspace_dir, cfg) / "memory.sqlite")
        spec: dict[str, Any] = {"type": "sqlite"}
        if db_path:
            spec["db_path"] = str(db_path)
        return {"memory_backend": spec}

    if btype == "postgres":
        dsn = backend.get("dsn")
        if backend.get("use_control_plane_dsn") or not dsn:
            if not octop_config.database.is_postgresql:
                raise OctopError(
                    ErrorCode.SLASH_BAD_ARGS,
                    "memory.backend use_control_plane_dsn requires postgresql control plane",
                )
            dsn = octop_config.database.postgresql_conninfo()
        return {"memory_backend": {"type": "postgres", "dsn": str(dsn)}}

    raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"unsupported memory.backend.type: {btype!r}")


def open_memory_kwargs(
    *,
    agent_id: str,
    user_id: int | None = None,
    cfg: dict[str, Any],
    octop_config: OctopConfig,
    workspace_dir: Path,
) -> tuple[str, str, dict[str, Any] | None]:
    """Return ``(namespace, backend_type, backend_config)`` for ``Memory(...)``.

    ``user_id=None`` opens the agent's shared namespace; a caller id opens
    that user's private namespace of the same agent (same backend, same
    database, partitioned by namespace). See :func:`memory_namespace`.
    """
    ns = memory_namespace(agent_id, user_id)
    resolved = memory_backend_from_agent_config(
        cfg, octop_config=octop_config, workspace_dir=workspace_dir
    )
    spec = resolved.get("memory_backend")
    if not isinstance(spec, dict):
        return ns, "sqlite", {"db_path": str(host_system_dir(workspace_dir, cfg) / "memory.sqlite")}
    btype = str(spec.get("type") or "sqlite")
    if btype == "postgres":
        return ns, "postgres", {"dsn": spec["dsn"]}
    db_path = spec.get("db_path") or str(host_system_dir(workspace_dir, cfg) / "memory.sqlite")
    return ns, "sqlite", {"db_path": str(db_path)}
