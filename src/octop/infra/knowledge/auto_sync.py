"""Process-local scheduler for configured local and SMB knowledge sources."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from octop.infra.cron.manager import CronManager
from octop.infra.db.repos.data_sources import KIND_LOCAL, KIND_SMB

logger = logging.getLogger(__name__)
AUTO_SYNC_JOB_ID = "knowledge.data_sources.auto_sync"
AUTO_SYNC_TICK_SECONDS = 15


class AutoSyncRunner:
    """Refresh source configuration each tick and wait for scans during shutdown."""

    def __init__(self, server: Any) -> None:
        self._server = server
        self._stopping = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self._active: set[asyncio.Task[Any]] = set()

    async def run(self) -> None:
        if self._stopping.is_set() or self._tick_lock.locked():
            return
        async with self._tick_lock:
            task = asyncio.current_task()
            if task is not None:
                self._active.add(task)
            try:
                await self._run_due_sources()
            finally:
                if task is not None:
                    self._active.discard(task)

    async def _run_due_sources(self) -> None:
        server = self._server
        services = server.services
        if services is None or self._stopping.is_set():
            return
        try:
            from octop.infra.licensing import current_license

            current_license().require_business_access()
        except Exception:
            return
        from octop.infra.knowledge.gate import assert_knowledge_usable

        try:
            assert_knowledge_usable(services.settings_repo.get, services.provider_repo)
        except Exception:
            return

        now = int(time.time())
        for source in services.data_sources_repo.list_all():
            if self._stopping.is_set():
                return
            interval = source.scan_interval_seconds
            if source.kind not in (KIND_LOCAL, KIND_SMB) or interval <= 0:
                continue
            # last_scan_at records attempts, including failed ones, so failures
            # wait a full configured interval instead of spinning every tick.
            if source.last_scan_at is not None and now - source.last_scan_at < interval:
                continue
            try:
                from octop.infra.knowledge.data_sources import DataSourceService
                from octop.infra.knowledge.jobs import enqueue_index_document

                def scan_and_harvest(source_id: str) -> list[tuple[str, str]]:
                    DataSourceService(services).sync_scheduled(source_id)
                    return [
                        (document.kb_id, document.id)
                        for document in services.knowledge_repo.list_source_files(source_id)
                        if document.status == "pending" and not document.is_dir
                    ]

                pending_documents = await asyncio.to_thread(scan_and_harvest, source.id)
                for kb_id, document_id in pending_documents:
                    enqueue_index_document(services, kb_id, document_id)
            except Exception:
                # The sync service persists the actual failure and its run row.
                logger.exception("scheduled knowledge source scan failed: %s", source.id)

    async def shutdown(self) -> None:
        self._stopping.set()
        active = tuple(task for task in self._active if task is not asyncio.current_task())
        if active:
            await asyncio.gather(*active, return_exceptions=True)


def install_auto_sync_job(cron_manager: CronManager, *, server: Any) -> None:
    """Register one bounded-frequency system job; source intervals are live data."""
    runner = AutoSyncRunner(server)
    server._knowledge_auto_sync_runner = runner
    cron_manager.schedule_system_job(
        AUTO_SYNC_JOB_ID,
        trigger=f"interval:{AUTO_SYNC_TICK_SECONDS}",
        func=runner.run,
    )


async def shutdown_auto_sync(server: Any) -> None:
    runner = getattr(server, "_knowledge_auto_sync_runner", None)
    if runner is not None:
        await runner.shutdown()
        server._knowledge_auto_sync_runner = None
