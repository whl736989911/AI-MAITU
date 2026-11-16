"""Unit tests for automatic backup helpers."""

from __future__ import annotations

import asyncio
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octop.config import BackupConfig, load_config
from octop.infra.backup import auto
from octop.infra.backup.auto import (
    apply_auto_backup_schedule,
    backup_config_from_payload,
    persist_backup_config,
    to_auto_backup_filename,
    update_server_backup_config,
)
from octop.infra.backup.manifest import BackupManifest
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.services import build_shared_services
from octop.infra.errors import OctopError
from octop.infra.utils.legacy_names import (
    LEGACY_AUTO_BACKUP_PREFIX,
    LEGACY_MANUAL_BACKUP_PREFIX,
)
from octop.infra.utils.paths import PathLayout


def test_to_auto_backup_filename() -> None:
    assert (
        to_auto_backup_filename("maitu-backup-20260101T000000Z.tar.gz")
        == "maitu-auto-backup-20260101T000000Z.tar.gz"
    )
    assert (
        to_auto_backup_filename("maitu-auto-backup-20260101T000000Z.tar.gz")
        == "maitu-auto-backup-20260101T000000Z.tar.gz"
    )


def test_to_auto_backup_filename_accepts_legacy_manual_prefix() -> None:
    legacy = f"{LEGACY_MANUAL_BACKUP_PREFIX}20260101T000000Z.tar.gz"
    assert to_auto_backup_filename(legacy) == "maitu-auto-backup-20260101T000000Z.tar.gz"


def test_to_auto_backup_filename_keeps_legacy_auto_name() -> None:
    legacy = f"{LEGACY_AUTO_BACKUP_PREFIX}20260101T000000Z.tar.gz"
    assert to_auto_backup_filename(legacy) == legacy


def test_backup_config_from_payload_rejects_bad_schedule() -> None:
    with pytest.raises(OctopError):
        backup_config_from_payload({"schedule": "not-a-trigger", "retention_count": 3})


def test_backup_config_from_payload_rejects_bad_retention() -> None:
    with pytest.raises(OctopError):
        backup_config_from_payload({"schedule": "cron:0 4 * * *", "retention_count": 0})


def test_backup_config_from_payload_reads_content_options() -> None:
    cfg = backup_config_from_payload(
        {
            "schedule": "cron:0 4 * * *",
            "retention_count": 3,
            "include_config": False,
            "include_workspaces": False,
            "include_skill_packages": False,
            "include_plugins": False,
            "include_knowledge": False,
            "include_chats": True,
        }
    )
    assert cfg.include_config is False
    assert cfg.include_workspaces is False
    assert cfg.include_skill_packages is False
    assert cfg.include_plugins is False
    assert cfg.include_knowledge is False
    assert cfg.include_chats is True


def test_persist_backup_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    load_config(cfg_path)
    out = persist_backup_config(
        cfg_path,
        BackupConfig(
            auto_enabled=True,
            schedule="interval:3600",
            retention_count=3,
            include_config=False,
            include_workspaces=False,
            include_skill_packages=False,
            include_plugins=False,
            include_knowledge=False,
            include_chats=True,
        ),
    )
    assert out.backup.auto_enabled is True
    assert out.backup.schedule == "interval:3600"
    assert out.backup.retention_count == 3
    assert out.backup.include_chats is True
    assert out.backup.include_config is False
    assert out.backup.include_workspaces is False
    assert out.backup.include_skill_packages is False
    assert out.backup.include_plugins is False
    assert out.backup.include_knowledge is False
    reloaded = load_config(cfg_path)
    assert reloaded.backup.auto_enabled is True
    assert reloaded.backup.include_chats is True
    assert reloaded.backup.include_config is False
    assert reloaded.backup.include_workspaces is False
    assert reloaded.backup.include_skill_packages is False
    assert reloaded.backup.include_plugins is False
    assert reloaded.backup.include_knowledge is False


@pytest.mark.asyncio
async def test_pending_scheduled_backup_observes_disable_but_manual_run_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto, "_lock", asyncio.Lock())
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    paths = PathLayout(tmp_path)
    initial = BackupConfig(
        auto_enabled=True,
        schedule="cron:0 4 * * *",
        retention_count=3,
    )
    config = persist_backup_config(paths.config, initial)
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    audit_repo = AuditRepo(pool)
    cron = MagicMock()
    server = SimpleNamespace(
        paths=paths,
        config=config,
        app_runtime=SimpleNamespace(cron_manager=cron, agent_registry=None),
        services=build_shared_services(db=pool, paths=paths, config=config),
    )
    try:
        apply_auto_backup_schedule(cron, server=server)
        scheduled_callback = cron.schedule_system_job.call_args.kwargs["func"]

        update_server_backup_config(
            server,
            BackupConfig(
                auto_enabled=False,
                schedule="cron:0 4 * * *",
                retention_count=3,
            ),
        )
        await scheduled_callback()
        assert not paths.backups_dir.exists()
        assert audit_repo.query(action="backup.auto_ok") == []
        assert audit_repo.query(action="backup.auto_failed") == []
        assert auto.backup_status_payload() == {"busy": False, "operation": None}

        SettingsRepo(pool).set("backup_probe", "manual_run_while_auto_disabled")
        entry = await auto.run_auto_backup(server)
        assert entry is not None
        archive_path = paths.backup_file(entry.name)
        assert archive_path.is_file()
        with tarfile.open(archive_path) as archive:
            manifest_file = archive.extractfile("manifest.json")
            assert manifest_file is not None
            manifest = BackupManifest.load_text(manifest_file.read().decode("utf-8"))
            database_file = archive.extractfile(manifest.db_file)
            assert database_file is not None
            snapshot = tmp_path / "snapshot.sqlite"
            snapshot.write_bytes(database_file.read())
        snapshot_pool = SqlitePool(snapshot)
        try:
            assert SettingsRepo(snapshot_pool).get("backup_probe") == (
                "manual_run_while_auto_disabled"
            )
        finally:
            snapshot_pool.close()
        assert len(audit_repo.query(action="backup.auto_ok")) == 1
    finally:
        pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_twice", [False, True])
async def test_cancelled_backup_keeps_exclusive_access_until_archive_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_twice: bool
) -> None:
    monkeypatch.setattr(auto, "_lock", asyncio.Lock())
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    paths = PathLayout(tmp_path)
    config = load_config(paths.config)
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    SettingsRepo(pool).set("backup_probe", "before_cancel")
    server = SimpleNamespace(
        paths=paths,
        app_runtime=None,
        services=SimpleNamespace(
            db=pool,
            config=config,
            agent_repo=AgentRepo(pool),
            audit_repo=SimpleNamespace(write=lambda **_kwargs: None),
        ),
    )
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    second_entered = asyncio.Event()
    original = auto.create_and_store_auto_backup

    def paused(**kwargs):
        started.set()
        assert release.wait(20)
        try:
            return original(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(auto, "create_and_store_auto_backup", paused)

    async def competing_restore() -> None:
        async with auto.hold_backup_lock("restore"):
            second_entered.set()

    first = asyncio.create_task(auto.run_auto_backup(server))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 20)
        first.cancel()
        await asyncio.sleep(0)
        if cancel_twice:
            first.cancel()
            await asyncio.sleep(0)
        second = asyncio.create_task(competing_restore())
        await asyncio.sleep(0)
        assert auto.backup_status_payload() == {"busy": True, "operation": "auto"}
        assert not second_entered.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second

        archive_path = next(paths.backups_dir.glob("*.tar.gz"))
        with tarfile.open(archive_path) as archive:
            manifest_file = archive.extractfile("manifest.json")
            assert manifest_file is not None
            manifest = BackupManifest.load_text(manifest_file.read().decode("utf-8"))
            database_file = archive.extractfile(manifest.db_file)
            assert database_file is not None
            snapshot = tmp_path / "snapshot.sqlite"
            snapshot.write_bytes(database_file.read())
        snapshot_pool = SqlitePool(snapshot)
        try:
            assert SettingsRepo(snapshot_pool).get("backup_probe") == "before_cancel"
        finally:
            snapshot_pool.close()
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )
        await asyncio.to_thread(finished.wait, 20)
        pool.close()
