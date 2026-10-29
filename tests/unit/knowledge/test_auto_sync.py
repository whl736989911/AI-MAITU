from __future__ import annotations

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

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
