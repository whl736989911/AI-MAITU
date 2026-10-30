"""Unit tests for admin backup restore rehydrate and non-blocking paths."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.api.routers import backup as backup_router
from octop.config import load_config
from octop.infra.backup import auto
from octop.infra.backup.auto import BACKUP_LOCK
from octop.infra.backup.store import BackupFileInfo
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


@pytest.mark.asyncio
async def test_restore_backup_file_rehydrates_providers_channels_and_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After restore, sync providers/agents, channels, and cron without process restart."""
    on_provider_changed = AsyncMock()
    reload_channels = AsyncMock()
    reload_cron = AsyncMock()
    restored: dict[str, Any] = {
        "schema_version": 1,
        "octop_version": "0.0.0",
        "agents": 1,
        "workspace_files": 2,
        "restore_config": True,
    }

    monkeypatch.setattr(backup_router, "normalize_backup_filename", lambda name: name)
    monkeypatch.setattr(
        backup_router,
        "_restore_stored_backup",
        lambda **_k: restored,
    )

    server = MagicMock()
    server.services = MagicMock()
    server.services.db = MagicMock()
    server.services.config.database = MagicMock()
    server.services.audit_repo.write = MagicMock()
    server.paths = MagicMock()
    server.app_runtime = MagicMock()
    server.app_runtime.agent_registry.on_provider_changed = on_provider_changed
    server.app_runtime.gateway.reload_channels_from_db = reload_channels
    server.app_runtime.cron_manager.reload_from_db = reload_cron

    result = await backup_router.restore_backup_file(
        filename="octop-backup.tar.gz",
        restore_config=True,
        user=MagicMock(id=1, username="admin"),
        server=server,
    )

    on_provider_changed.assert_awaited_once_with()
    reload_channels.assert_awaited_once_with()
    reload_cron.assert_awaited_once_with()
    assert result["ok"] is True
    assert result["name"] == "octop-backup.tar.gz"
    assert result["agents"] == 1


@pytest.mark.asyncio
async def test_restore_backup_file_skips_rehydrate_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored: dict[str, Any] = {
        "schema_version": 1,
        "octop_version": "0.0.0",
        "agents": 0,
        "workspace_files": 0,
        "restore_config": False,
    }
    monkeypatch.setattr(backup_router, "normalize_backup_filename", lambda name: name)
    monkeypatch.setattr(
        backup_router,
        "_restore_stored_backup",
        lambda **_k: restored,
    )

    server = MagicMock()
    server.services = MagicMock()
    server.services.db = MagicMock()
    server.services.config.database = MagicMock()
    server.services.audit_repo.write = MagicMock()
    server.paths = MagicMock()
    server.app_runtime = None

    result = await backup_router.restore_backup_file(
        filename="octop-backup.tar.gz",
        restore_config=False,
        user=MagicMock(id=1, username="admin"),
        server=server,
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_rehydrate_reloads_channels_and_cron_even_if_provider_rehydrate_fails() -> None:
    """Channel and cron reload must still run when agent rehydrate raises."""
    on_provider_changed = AsyncMock(side_effect=RuntimeError("provider boom"))
    reload_channels = AsyncMock()
    reload_cron = AsyncMock()

    server = MagicMock()
    server.app_runtime = MagicMock()
    server.app_runtime.agent_registry.on_provider_changed = on_provider_changed
    server.app_runtime.gateway.reload_channels_from_db = reload_channels
    server.app_runtime.cron_manager.reload_from_db = reload_cron

    await backup_router._rehydrate_runtime_after_restore(server)

    on_provider_changed.assert_awaited_once_with()
    reload_channels.assert_awaited_once_with()
    reload_cron.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_create_backup_offloads_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual create must not run tar/sqlite work on the event loop."""
    entry = BackupFileInfo(
        name="octop-backup-20260101T000000Z.tar.gz",
        size=12,
        modified_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
    )
    called: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> BackupFileInfo:
        called.update(kwargs)
        return entry

    monkeypatch.setattr(backup_router, "_create_and_store_manual_backup", _fake_create)
    monkeypatch.setattr(backup_router, "_agent_rows", lambda _server: [])

    server = MagicMock()
    server.services = MagicMock()
    server.services.db = object()
    server.services.config.database = object()
    server.paths = object()
    server.app_runtime = MagicMock()

    result = await backup_router.create_backup(_=None, server=server)

    assert result == {"ok": True, "item": entry.to_dict()}
    assert called["pool"] is server.services.db
    assert called["options"].include_chats is False


@pytest.mark.asyncio
async def test_create_backup_passes_selected_content(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = BackupFileInfo(
        name="octop-backup-20260101T000000Z.tar.gz",
        size=12,
        modified_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
    )
    called: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> BackupFileInfo:
        called.update(kwargs)
        return entry

    monkeypatch.setattr(backup_router, "_create_and_store_manual_backup", _fake_create)
    monkeypatch.setattr(backup_router, "_agent_rows", lambda _server: [])
    server = MagicMock()
    server.services.db = object()
    server.services.config.database = object()

    body = backup_router.CreateBackupBody(
        include_config=False,
        include_workspaces=False,
        include_chats=True,
    )
    await backup_router.create_backup(body=body, _=None, server=server)

    assert called["options"] == body


@pytest.mark.asyncio
async def test_create_backup_rejects_when_lock_held() -> None:
    await BACKUP_LOCK.acquire()
    try:
        with pytest.raises(OctopError) as exc_info:
            await backup_router.create_backup(_=None, server=MagicMock(services=MagicMock()))
        assert exc_info.value.code == ErrorCode.BACKUP_IN_PROGRESS
    finally:
        BACKUP_LOCK.release()


@pytest.mark.asyncio
async def test_restore_backup_rejects_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_router, "normalize_backup_filename", lambda name: name)
    await BACKUP_LOCK.acquire()
    try:
        with pytest.raises(OctopError) as exc_info:
            await backup_router.restore_backup_file(
                filename="octop-backup.tar.gz",
                restore_config=True,
                user=MagicMock(id=1, username="admin"),
                server=MagicMock(services=MagicMock()),
            )
        assert exc_info.value.code == ErrorCode.BACKUP_IN_PROGRESS
    finally:
        BACKUP_LOCK.release()


@pytest.mark.asyncio
async def test_backup_status_reports_idle_and_busy() -> None:
    from octop.infra.backup.auto import hold_backup_lock

    idle = await backup_router.get_backup_status(_=None)
    assert idle == {"busy": False, "operation": None}

    async with hold_backup_lock("create"):
        busy = await backup_router.get_backup_status(_=None)
        assert busy == {"busy": True, "operation": "create"}


@pytest.fixture
def isolated_backup_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.setattr(auto, "_lock", asyncio.Lock())
    paths = PathLayout(tmp_path)
    config = load_config(paths.config)
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    UserRepo(pool).create(username="operator", password_hash="synthetic", role="admin")
    server = SimpleNamespace(
        paths=paths,
        services=SimpleNamespace(
            db=pool,
            config=config,
            audit_repo=SimpleNamespace(write=lambda **_kwargs: None),
        ),
        app_runtime=SimpleNamespace(
            agent_registry=SimpleNamespace(list_rows=AgentRepo(pool).list_all),
            gateway=None,
            cron_manager=None,
        ),
    )
    try:
        yield server
    finally:
        pool.close()


@pytest.mark.asyncio
async def test_cancelled_restore_finishes_runtime_rehydration(
    isolated_backup_server: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = isolated_backup_server
    settings = SettingsRepo(server.services.db)
    settings.set("backup_probe", "snapshot")
    filename = "restore-cancellation.tar.gz"
    archive = server.paths.ensure_backups_dir() / filename
    backup_router.create_system_backup(
        paths=server.paths,
        agent_rows=[],
        pool=server.services.db,
        db_config=server.services.config.database,
        dest=archive,
        include_config=False,
    )
    settings.set("backup_probe", "live")
    cached = {"probe": "live"}
    rehydrate_started = asyncio.Event()
    finish_rehydrate = asyncio.Event()

    async def refresh_runtime() -> None:
        rehydrate_started.set()
        await finish_rehydrate.wait()
        cached["probe"] = await asyncio.to_thread(settings.get, "backup_probe")

    server.app_runtime.agent_registry.on_provider_changed = refresh_runtime
    started = threading.Event()
    release = threading.Event()
    original = backup_router._restore_stored_backup

    def paused(**kwargs: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(20)
        return original(**kwargs)

    monkeypatch.setattr(backup_router, "_restore_stored_backup", paused)
    task = asyncio.create_task(
        backup_router.restore_backup_file(
            filename,
            restore_config=False,
            user=SimpleNamespace(id=1, username="operator"),
            server=server,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 20)
        task.cancel()
        await asyncio.sleep(0)
        assert auto.backup_status_payload() == {"busy": True, "operation": "restore"}
        release.set()
        await asyncio.wait_for(rehydrate_started.wait(), 20)
        task.cancel()
        await asyncio.sleep(0)
        assert auto.backup_status_payload() == {"busy": True, "operation": "restore"}
        finish_rehydrate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settings.get("backup_probe") == "snapshot"
        assert cached["probe"] == "snapshot"
    finally:
        release.set()
        finish_rehydrate.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_export_removes_its_completed_temporary_archive(
    isolated_backup_server: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import BackgroundTasks

    server = isolated_backup_server
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    created: list[Path] = []
    original = backup_router._create_ephemeral_backup

    def paused(**kwargs: Any) -> tuple[Path, str]:
        started.set()
        assert release.wait(20)
        try:
            result = original(**kwargs)
            created.append(result[0])
            return result
        finally:
            finished.set()

    monkeypatch.setattr(backup_router, "_create_ephemeral_backup", paused)
    task = asyncio.create_task(
        backup_router.export_backup(
            BackgroundTasks(),
            include_config=False,
            include_workspaces=False,
            include_skill_packages=False,
            include_plugins=False,
            include_knowledge=False,
            include_chats=False,
            _=None,
            server=server,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 20)
        task.cancel()
        await asyncio.sleep(0)
        assert auto.backup_status_payload() == {"busy": True, "operation": "export"}
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert created and not created[0].exists()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 20)
        for path in created:
            path.unlink(missing_ok=True)
