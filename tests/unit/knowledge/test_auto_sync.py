from __future__ import annotations

import asyncio
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.knowledge_sync_runs import KnowledgeSyncRunRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.knowledge import auto_sync


class _License:
    def require_business_access(self) -> None:
        return None


class _SourceRepo:
    def __init__(self, sources):
        self.sources = sources

    def list_all(self):
        return list(self.sources)


class _Services:
    def __init__(self, sources, enabled=True):
        self.data_sources_repo = _SourceRepo(sources)
        self.knowledge_repo = SimpleNamespace(list_source_files=lambda _source_id: [])
        self.settings_repo = SimpleNamespace(get=lambda key: "true" if enabled else "false")
        self.provider_repo = None


def _source(kind="local", interval=30, last_scan_at=None, ds_id="source"):
    return SimpleNamespace(
        id=ds_id,
        kind=kind,
        scan_interval_seconds=interval,
        last_scan_at=last_scan_at,
    )


@pytest.fixture(autouse=True)
def _allow_license(monkeypatch):
    monkeypatch.setattr("octop.infra.licensing.current_license", lambda: _License())


def test_due_local_and_smb_attempts_use_latest_interval_and_failures_do_not_block(
    monkeypatch,
):
    services = _Services(
        [
            _source("local", 30, None, "first"),
            _source("smb", 10, 1_000, "due"),
            _source("local", 0, None, "disabled"),
            _source("upload", 10, None, "not-folder"),
            _source("local", 60, 1_999, "not-due"),
        ]
    )
    server = SimpleNamespace(services=services)
    called = []

    class _DataSourceService:
        def __init__(self, _services):
            pass

        def sync_scheduled(self, ds_id):
            called.append(ds_id)
            if ds_id == "first":
                raise RuntimeError("persisted scan failure")

    monkeypatch.setattr(auto_sync.time, "time", lambda: 2_000)
    monkeypatch.setattr("octop.infra.knowledge.data_sources.DataSourceService", _DataSourceService)
    asyncio.run(auto_sync.AutoSyncRunner(server).run())
    assert called == ["first", "due"]


def test_disabled_knowledge_feature_skips_scans(monkeypatch):
    server = SimpleNamespace(services=_Services([_source()], enabled=False))
    called = []
    monkeypatch.setattr(
        "octop.infra.knowledge.data_sources.DataSourceService",
        lambda _services: SimpleNamespace(sync_scheduled=lambda ds_id: called.append(ds_id)),
    )
    asyncio.run(auto_sync.AutoSyncRunner(server).run())
    assert called == []


@pytest.mark.parametrize(
    ("disabled", "expires_at", "should_scan"),
    [(True, None, False), (False, 1, False), (False, None, True)],
)
def test_scheduled_scan_respects_live_owner_account_state(
    tmp_path: Path,
    monkeypatch,
    disabled: bool,
    expires_at: int | None,
    should_scan: bool,
):
    from octop.infra.knowledge import jobs
    from octop.infra.knowledge.data_sources import DataSourceService, FolderSettings
    from octop.infra.knowledge.gate import set_feature_enabled
    from octop.infra.knowledge.sources.scan import DEBOUNCE_SECONDS
    from octop.infra.utils.paths import PathLayout

    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    db = SqlitePool(tmp_path / "auto-sync-owner.db")
    run_migrations(db)
    users = UserRepo(db)
    user_id = users.create(
        username="source-owner",
        password_hash="h",
        role="admin",
        access_expires_at=None,
    )
    services = SimpleNamespace(
        db=db,
        repos=RepoBundle.from_pool(db),
        config=SimpleNamespace(max_upload_bytes=1_000_000, max_upload_mb=1),
        paths=PathLayout.from_env(),
        knowledge_repo=KnowledgeRepo(db),
        knowledge_collections_repo=KnowledgeCollectionsRepo(db),
        data_sources_repo=DataSourceRepo(db),
        knowledge_sync_runs_repo=KnowledgeSyncRunRepo(db),
        settings_repo=SettingsRepo(db),
        user_repo=users,
        secret_repo=SecretRepo(db),
        audit_repo=AuditRepo(db),
        provider_repo=None,
    )
    set_feature_enabled(
        services.settings_repo.get,
        services.settings_repo.set,
        enabled=True,
        model=None,
    )
    source_service = DataSourceService(services)
    base = services.knowledge_repo.create_base(owner_user_id=user_id, name="Docs")
    root = tmp_path / "folder"
    root.mkdir()
    (root / "policy.md").write_text("refunds take five business days", encoding="utf-8")
    source = source_service.create(
        base.id,
        actor_user_id=user_id,
        name="Policies",
        kind="local",
        folder=FolderSettings(root_path=str(root), scan_interval_seconds=30),
    )
    settled_time = time.time() + 60
    monkeypatch.setattr(auto_sync.time, "time", lambda: settled_time)
    if disabled:
        users.set_disabled(user_id, True)
    if expires_at is not None:
        with db.transaction() as conn:
            users.update_account(user_id, {"access_expires_at": expires_at}, conn=conn)

    monkeypatch.setattr(jobs, "assert_embedding_usable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jobs,
        "embed_knowledge_texts",
        lambda _services, texts: [[float(len(text)), 1.0, 0.0, 0.0] for text in texts],
    )
    jobs.reset_index_semaphore_for_tests()
    indexing_tasks = []
    enqueue = jobs.enqueue_index_document

    def track_indexing_task(*args):
        task = enqueue(*args)
        indexing_tasks.append(task)
        return task

    monkeypatch.setattr(jobs, "enqueue_index_document", track_indexing_task)
    server = SimpleNamespace(services=services)

    async def run_sync():
        nonlocal settled_time
        runner = auto_sync.AutoSyncRunner(server)
        await runner.run()
        # Let both debounce and the configured source interval elapse.
        settled_time += max(DEBOUNCE_SECONDS, source.scan_interval_seconds) + 1
        await runner.run()
        if indexing_tasks:
            await asyncio.gather(*indexing_tasks)

    try:
        asyncio.run(run_sync())
        runs = services.knowledge_sync_runs_repo.list_for_source(source.id)
        documents = services.knowledge_repo.list_source_files(source.id)
        files = [document for document in documents if not document.is_dir]
        if should_scan:
            assert len(runs) == 2 and all(run.status == "ok" for run in runs)
            assert len(files) == 1 and files[0].status == "ready"
        else:
            assert runs == []
            assert documents == []
    finally:
        db.close()
        jobs.reset_index_semaphore_for_tests()


def test_overlapping_ticks_are_skipped(monkeypatch):
    started = Event()
    release = Event()
    scanned = []

    def scan(source_id):
        scanned.append(source_id)
        started.set()
        if not release.wait(5):
            raise TimeoutError("the test did not release its running scan")

    monkeypatch.setattr(
        "octop.infra.knowledge.data_sources.DataSourceService",
        lambda _services: SimpleNamespace(sync_scheduled=scan),
    )

    async def scenario():
        runner = auto_sync.AutoSyncRunner(SimpleNamespace(services=_Services([_source()])))
        first = asyncio.create_task(runner.run())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await asyncio.wait_for(runner.run(), timeout=2)
        finally:
            release.set()
            await first

    asyncio.run(scenario())
    assert scanned == ["source"]


@pytest.mark.parametrize("worker_fails", [False, True])
def test_cancelled_tick_and_shutdown_wait_for_the_worker(monkeypatch, worker_fails):
    started = Event()
    release = Event()

    def scan(_source_id):
        started.set()
        if not release.wait(5):
            raise TimeoutError("the test did not release its running scan")
        if worker_fails:
            raise RuntimeError("source disconnected while cancelling the scan")

    monkeypatch.setattr(
        "octop.infra.knowledge.data_sources.DataSourceService",
        lambda _services: SimpleNamespace(sync_scheduled=scan),
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        runner = auto_sync.AutoSyncRunner(SimpleNamespace(services=_Services([_source()])))
        tick = asyncio.create_task(runner.run())
        shutdown = None
        try:
            assert await loop.run_in_executor(None, started.wait, 5)
            tick.cancel()
            shutdown = asyncio.create_task(runner.shutdown())
            await asyncio.sleep(0)
            tick.cancel()
            await asyncio.sleep(0)
            assert not tick.done()
            assert not shutdown.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await tick
            await shutdown
        finally:
            release.set()
            pending = [tick] if shutdown is None else [tick, shutdown]
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(scenario())
