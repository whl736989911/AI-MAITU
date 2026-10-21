"""Scoped current-agent memory backup and restore API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from octop.api.common.agent import AgentCapability, assert_agent_capability_write, require_agent_row
from octop.api.common.memory_client import (
    invalidate_cached_memory,
    open_memory_scope,
    resolve_memory_access,
)
from octop.api.deps import current_user, get_server
from octop.infra.agents.memory_backup import (
    backup_path,
    backup_root,
    create_backup,
    list_backups,
    restore_backup,
)
from octop.infra.agents.memory_backup import (
    delete_backup as remove_backup,
)
from octop.infra.agents.memory_context import (
    document_lock_path,
    exclusive_file_lock,
    managed_workspace_user_file,
    read_private_document,
    read_workspace_document,
    save_private_document,
    save_workspace_document,
)
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)
router = APIRouter()
MemoryScope = Literal["shared", "private"]


class BackupRequest(BaseModel):
    scope: MemoryScope
    confirmed: Literal[True] = Field(description="Confirm a scoped snapshot or restore operation")
    backup_id: str | None = Field(default=None, min_length=1, max_length=80)


class BackupInfo(BaseModel):
    backup_id: str
    created_at: float
    size_bytes: int
    backend: str
    total_rows: int = 0


class RestoreOutcome(BaseModel):
    restored_backup_id: str
    safety_backup_id: str
    restored_rows: int
    checkpoint_threads: int


def _checkpoint_settings(manager: Any, paths: Any, agent_id: str) -> tuple[Path, bool]:
    from octop.infra.agents.workspace_dir import host_system_dir, workspace_dir_from_config_json

    row = manager.get_row(agent_id)
    if row is None:
        raise ValueError("agent not found")
    config = json.loads(row.config_json or "{}")
    if not isinstance(config, dict):
        raise ValueError("invalid agent configuration")
    memory = config.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    workspace = workspace_dir_from_config_json(
        row.config_json, paths=paths, agent_id=agent_id, ensure=False
    )
    enabled = memory.get("memory_enabled")
    return host_system_dir(workspace, config) / "checkpoints.sqlite", enabled if isinstance(
        enabled, bool
    ) else True


def _namespace_threads(server: Any, agent_id: str, user_id: int) -> set[str]:
    repo = server.services.thread_repo
    result = set()
    for owner_agent, thread_id in repo.list_for_user(user_id):
        if owner_agent != agent_id:
            continue
        row = repo.get(thread_id)
        if row is not None and row.agent_id == agent_id and row.user_id == user_id:
            result.add(thread_id)
    return result


async def _scope_root(agent_id: str, scope: MemoryScope, user: Any, server: Any) -> Path:
    access = await resolve_memory_access(agent_id, user=user, as_user=None, server=server)
    if scope == "private" and (access.stage != "active" or not access.private_writable):
        raise HTTPException(
            status_code=403,
            detail="private memory backups are available only to the authenticated active-feature caller",
        )
    return await asyncio.to_thread(
        backup_root, Path(server.paths.root), agent_id, scope, int(user.id)
    )


@router.get(
    "/agents/{agent_id}/memory/backups",
    summary="List the current caller's scoped memory backups",
    response_model=list[BackupInfo],
)
async def backups(
    agent_id: str,
    scope: MemoryScope = Query("shared"),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    root = await _scope_root(agent_id, scope, user, server)
    return await asyncio.to_thread(
        list_backups, root, agent_id=agent_id, scope=scope, user_id=int(user.id)
    )


@router.post(
    "/agents/{agent_id}/memory/backups",
    summary="Create a scoped memory and caller-checkpoint backup",
    response_model=BackupInfo,
)
async def create(
    agent_id: str,
    body: BackupRequest,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    root = await _scope_root(agent_id, body.scope, user, server)
    manager = server.app_runtime.agent_registry
    fallback_path, memory_enabled = await asyncio.to_thread(
        _checkpoint_settings, manager, server.paths, agent_id
    )
    async with manager.memory_storage_maintenance(agent_id):
        await _run_thread(lambda: invalidate_cached_memory(agent_id))
        try:
            memory = await open_memory_scope(
                agent_id=agent_id, scope=body.scope, user=user, server=server
            )
            workspace = (
                await asyncio.to_thread(manager.workspace_for_agent, agent_id)
                if body.scope == "shared"
                else None
            )
            if body.scope == "shared" and workspace is None:
                raise HTTPException(status_code=404, detail="agent workspace not found")
            threads = await asyncio.to_thread(_namespace_threads, server, agent_id, int(user.id))
            return await _run_thread(
                lambda: _create_backup_locked(
                    server,
                    memory,
                    workspace,
                    root,
                    agent_id,
                    body.scope,
                    int(user.id),
                    threads,
                    fallback_path,
                    memory_enabled,
                ),
            )
        finally:
            await _run_thread(lambda: invalidate_cached_memory(agent_id))


def _create_backup_locked(
    server: Any,
    memory: Any,
    workspace: Any,
    root: Path,
    agent_id: str,
    scope: MemoryScope,
    user_id: int,
    threads: set[str],
    fallback_path: Path,
    memory_enabled: bool,
) -> dict[str, Any]:
    with ExitStack() as stack:
        for name in ("USER.md", "MEMORY.md"):
            lock = document_lock_path(
                server.paths,
                agent_id,
                scope=scope,
                user_id=user_id if scope == "private" else None,
                name=name,
            )
            stack.enter_context(exclusive_file_lock(lock))
        documents = (
            {name: read_private_document(memory, name).text for name in ("USER.md", "MEMORY.md")}
            if scope == "private"
            else {
                name: read_workspace_document(workspace, name).text
                for name in ("USER.md", "MEMORY.md")
            }
        )
        return create_backup(
            memory,
            root=root,
            agent_id=agent_id,
            scope=scope,
            user_id=user_id,
            thread_ids=sorted(threads),
            documents=documents,
            fallback_path=fallback_path,
            memory_enabled=memory_enabled,
        )


@router.get(
    "/agents/{agent_id}/memory/backups/{backup_id}/download",
    summary="Download a caller-owned scoped memory backup",
)
async def download(
    agent_id: str,
    backup_id: str,
    scope: MemoryScope = Query("shared"),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> FileResponse:
    root = await _scope_root(agent_id, scope, user, server)
    try:
        path = await asyncio.to_thread(
            backup_path, root, backup_id, agent_id=agent_id, scope=scope, user_id=int(user.id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="backup not found") from exc
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.delete(
    "/agents/{agent_id}/memory/backups/{backup_id}",
    summary="Permanently delete a caller-owned memory backup",
    status_code=204,
)
async def delete(
    agent_id: str,
    backup_id: str,
    scope: MemoryScope = Query(...),
    confirmed: bool = Query(False),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> Response:
    if not confirmed:
        raise HTTPException(status_code=400, detail="explicit confirmation is required")
    root = await _scope_root(agent_id, scope, user, server)
    try:
        await asyncio.to_thread(
            remove_backup, root, backup_id, agent_id=agent_id, scope=scope, user_id=int(user.id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="backup not found") from exc
    return Response(status_code=204)


@router.post(
    "/agents/{agent_id}/memory/restore",
    summary="Restore a scoped backup after creating a safety backup",
    description="Restores only this namespace and verified caller-owned checkpoints. Other users' checkpoints and immutable history transcripts are not overwritten.",
    response_model=RestoreOutcome,
)
async def restore(
    agent_id: str,
    body: BackupRequest,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    if not body.backup_id:
        raise HTTPException(status_code=400, detail="backup_id is required")
    access = await resolve_memory_access(agent_id, user=user, as_user=None, server=server)
    writable = access.private_writable if body.scope == "private" else access.shared_writable
    if not writable:
        raise OctopError.localized(ErrorCode.FORBIDDEN)
    row = require_agent_row(agent_id, user=user, as_user=None, server=server)
    assert_agent_capability_write(
        row,
        user,
        AgentCapability.MEMORY,
        memory_stage=access.stage,
        memory_scope=body.scope,
        server=server,
    )
    root = await _scope_root(agent_id, body.scope, user, server)
    manager = server.app_runtime.agent_registry
    fallback_path, memory_enabled = await asyncio.to_thread(
        _checkpoint_settings, manager, server.paths, agent_id
    )
    async with manager.memory_storage_maintenance(agent_id):
        await _run_thread(lambda: invalidate_cached_memory(agent_id))
        try:
            memory = await open_memory_scope(
                agent_id=agent_id, scope=body.scope, user=user, server=server
            )
            workspace = (
                await asyncio.to_thread(manager.workspace_for_agent, agent_id)
                if body.scope == "shared"
                else None
            )
            if body.scope == "shared" and workspace is None:
                raise HTTPException(status_code=404, detail="agent workspace not found")
            threads = await asyncio.to_thread(_namespace_threads, server, agent_id, int(user.id))
            result = await _run_thread(
                lambda: _restore_with_safety(
                    server,
                    memory,
                    root,
                    body.backup_id or "",
                    agent_id,
                    body.scope,
                    int(user.id),
                    threads,
                    workspace,
                    fallback_path,
                    memory_enabled,
                ),
            )
            try:
                await _run_async_drained(
                    lambda: _reset_restored_runtime(
                        server, manager, agent_id, int(user.id), threads
                    )
                )
            except Exception:
                try:
                    await _run_thread(
                        lambda: _restore_with_safety(
                            server,
                            memory,
                            root,
                            result["safety_backup_id"],
                            agent_id,
                            body.scope,
                            int(user.id),
                            threads,
                            workspace,
                            fallback_path,
                            memory_enabled,
                        )
                    )
                except Exception as rollback_error:
                    raise RuntimeError(
                        "rollback failed; manual recovery is required"
                    ) from rollback_error
                raise
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Scoped memory restore failed for agent=%s scope=%s", agent_id, body.scope
            )
            rollback_note = "memory restore failed; rollback was attempted"
            if "rollback failed" in str(exc):
                rollback_note = "memory restore and rollback failed; manual recovery is required"
            raise HTTPException(status_code=409, detail=rollback_note) from exc
        finally:
            await _run_thread(lambda: invalidate_cached_memory(agent_id))
    return result


def _restore_with_safety(
    server: Any,
    memory: Any,
    root: Path,
    backup_id: str,
    agent_id: str,
    scope: MemoryScope,
    user_id: int,
    threads: set[str],
    workspace: Any,
    fallback_path: Path,
    memory_enabled: bool,
) -> dict[str, Any]:
    with ExitStack() as stack:
        for name in ("USER.md", "MEMORY.md"):
            lock = document_lock_path(
                server.paths,
                agent_id,
                scope=scope,
                user_id=user_id if scope == "private" else None,
                name=name,
            )
            stack.enter_context(exclusive_file_lock(lock))
        docs = (
            {name: read_private_document(memory, name).text for name in ("USER.md", "MEMORY.md")}
            if scope == "private"
            else {
                name: read_workspace_document(workspace, name).text
                for name in ("USER.md", "MEMORY.md")
            }
        )
        namespace = str(memory.namespace)
        backend = type(memory.backend).__name__
        safety = create_backup(
            memory,
            root=root,
            agent_id=agent_id,
            scope=scope,
            user_id=user_id,
            thread_ids=sorted(threads),
            documents=docs,
            fallback_path=fallback_path,
            memory_enabled=memory_enabled,
        )

        def restore_docs(values: dict[str, str]) -> None:
            if scope == "private":
                for name, text in values.items():
                    current = read_private_document(memory, name)
                    save_private_document(memory, name, text, expected_snapshot=current.snapshot)
                return
            if workspace is None:
                raise ValueError("target workspace is unavailable")
            for name, text in values.items():
                current = read_workspace_document(workspace, name)
                if name == "USER.md" and managed_workspace_user_file(workspace):
                    if current.text != text:
                        raise ValueError("generated USER.md cannot be restored manually")
                    continue
                save_workspace_document(workspace, name, text, expected_snapshot=current.snapshot)

        def apply(target_id: str) -> dict[str, Any]:
            return restore_backup(
                memory,
                root=root,
                backup_id=target_id,
                agent_id=agent_id,
                scope=scope,
                namespace=namespace,
                backend=backend,
                user_id=user_id,
                allowed_threads=threads,
                restore_documents=restore_docs,
                fallback_path=fallback_path,
                memory_enabled=memory_enabled,
            )

        try:
            result = apply(backup_id)
            result["safety_backup_id"] = safety["backup_id"]
            return result
        except Exception:
            try:
                apply(safety["backup_id"])
            except Exception as rollback_error:
                raise RuntimeError(
                    "rollback failed; manual recovery is required"
                ) from rollback_error
            raise


async def _run_thread[Result](fn: Callable[[], Result]) -> Result:
    task = asyncio.create_task(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _run_async_drained(fn: Any) -> Any:
    task = asyncio.create_task(fn())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _reset_restored_runtime(
    server: Any, manager: Any, agent_id: str, user_id: int, threads: set[str]
) -> None:
    gateway = getattr(server.app_runtime, "gateway", None)
    for thread_id in sorted(threads):
        await asyncio.to_thread(manager.reset_restored_thread, agent_id, thread_id, user_id=user_id)
        if gateway is not None:
            gateway.processor.hitl_coordinator.store.expire_pending_for_thread(
                thread_id, agent_id=agent_id, user_id=user_id
            )
            turn_id = await gateway.steer.active_turn_id(thread_id)
            if turn_id is not None:
                await gateway.complete_steering_turn(thread_id, turn_id, cancelled=True)
        await asyncio.to_thread(
            server.services.thread_message_repo.mark_projection, thread_id, "pending"
        )
