"""Delete persisted LangGraph checkpoints without deleting workspace content."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from octop.config import OctopConfig
from octop.infra.agents.memory_backend import close_memory, open_memory_kwargs
from octop.infra.agents.workspace_dir import host_system_dir, workspace_dir_from_config_json
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)


def agent_config_from_row(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(getattr(row, "config_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def delete_thread_checkpoints(
    *,
    agent_id: str,
    thread_id: str,
    cfg: dict[str, Any],
    octop_config: OctopConfig,
    workspace_dir: Path,
) -> bool:
    try:
        return _delete_thread_checkpoints_impl(
            agent_id=agent_id,
            thread_id=thread_id,
            cfg=cfg,
            octop_config=octop_config,
            workspace_dir=workspace_dir,
        )
    except OctopError:
        raise
    except Exception as exc:
        raise OctopError(
            ErrorCode.CHECKPOINT_DELETE_FAILED,
            f"could not delete checkpoint data for thread {thread_id!r}",
        ) from exc


def _delete_thread_checkpoints_impl(
    *,
    agent_id: str,
    thread_id: str,
    cfg: dict[str, Any],
    octop_config: OctopConfig,
    workspace_dir: Path,
) -> bool:
    """Delete a thread from the configured durable store(s).

    A missing SQLite database or a database with no checkpoint schema is a
    normal empty state. Configured-store errors propagate, leaving the caller's
    thread metadata available for retry. Only checkpoint rows for this exact
    thread are removed; private/shared memory tables and workspace files are
    never deleted here.
    """
    system_dir = host_system_dir(workspace_dir, cfg)

    namespace, backend, backend_config = open_memory_kwargs(
        agent_id=agent_id,
        cfg=cfg,
        octop_config=octop_config,
        workspace_dir=workspace_dir,
    )
    deleted = False
    if backend == "sqlite":
        memory_path = Path(
            str((backend_config or {}).get("db_path") or system_dir / "memory.sqlite")
        )
        if memory_path.is_file():
            deleted = _delete_memory_thread(namespace, backend, backend_config, thread_id)
        fallback_path = system_dir / "checkpoints.sqlite"
        if fallback_path.is_file() and fallback_path.resolve() != memory_path.resolve():
            deleted = _delete_sqlite_saver_thread(fallback_path, thread_id) or deleted
    elif backend == "postgres":
        deleted = _delete_memory_thread(namespace, backend, backend_config, thread_id)
        fallback_path = system_dir / "checkpoints.sqlite"
        if fallback_path.is_file():
            deleted = _delete_sqlite_saver_thread(fallback_path, thread_id) or deleted
    else:
        raise ValueError(f"unsupported checkpoint backend: {backend!r}")
    return deleted


def delete_user_thread_checkpoints(services: Any, user_id: int) -> None:
    """Remove only a user's threads before their account cascade runs.

    Feature agents and their shared configuration/memory are intentionally
    untouched: only caller-owned thread ids are deleted from checkpoint stores.
    """
    for agent_id, thread_id in services.thread_repo.list_for_user(user_id):
        row = services.agent_repo.get(agent_id)
        if row is None:
            raise OctopError(
                ErrorCode.CHECKPOINT_DELETE_FAILED,
                f"cannot resolve agent {agent_id!r} for user thread {thread_id!r}",
            )
        delete_thread_checkpoints(
            agent_id=agent_id,
            thread_id=thread_id,
            cfg=agent_config_from_row(row),
            octop_config=services.config,
            workspace_dir=workspace_dir_from_config_json(
                getattr(row, "config_json", None),
                paths=services.paths,
                agent_id=agent_id,
                ensure=False,
            ),
        )


def _delete_memory_thread(
    namespace: str,
    backend: str,
    backend_config: dict[str, Any] | None,
    thread_id: str,
) -> bool:
    from harness_memory import Memory  # noqa: PLC0415

    memory = Memory(namespace=namespace, backend=backend, backend_config=backend_config)
    try:
        try:
            ensure = getattr(memory, "_ensure_checkpointer", None)
            if callable(ensure):
                ensure()
            saver = getattr(memory, "_checkpointer", None)
            delete = getattr(saver, "delete_thread", None)
            if not callable(delete):
                raise NotImplementedError(
                    f"checkpoint saver {type(saver).__name__!r} does not support thread deletion"
                )
            delete(thread_id)
            return True
        except Exception as exc:
            if _missing_checkpoint_schema(exc):
                return False
            raise
    finally:
        close_memory(memory)


def _delete_sqlite_saver_thread(db_path: Path, thread_id: str) -> bool:
    from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415

    conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not {"checkpoints", "writes"}.issubset(tables):
            return False
        saver = SqliteSaver(conn)
        saver.is_setup = True
        saver.delete_thread(thread_id)
        return True
    finally:
        conn.close()


def _missing_checkpoint_schema(exc: BaseException) -> bool:
    message = str(exc).lower()
    names = ("checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs")
    return any(
        f"no such table: {name}" in message
        or f'relation "{name}" does not exist' in message
        or f"relation {name} does not exist" in message
        for name in names
    )
