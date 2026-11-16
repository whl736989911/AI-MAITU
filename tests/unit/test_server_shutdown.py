from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.infra.server import OctopServer


@pytest.mark.asyncio
async def test_shutdown_continues_after_component_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = OctopServer()
    resources = {
        name: io.BytesIO() for name in ("auto", "proactive", "cron", "gateway", "registry", "index")
    }

    def closer(name: str, error: Exception | None = None):
        async def close(*_args: object) -> None:
            resources[name].close()
            if error is not None:
                raise error

        return close

    proactive = SimpleNamespace(shutdown=closer("proactive"))
    cron = SimpleNamespace(shutdown=closer("cron"))
    gateway = SimpleNamespace(shutdown=closer("gateway", RuntimeError("gateway close failed")))
    registry = SimpleNamespace(shutdown=closer("registry"))
    server.app_runtime = SimpleNamespace(
        proactive_scheduler=proactive,
        cron_manager=cron,
        gateway=gateway,
        agent_registry=registry,
        business_running=True,
    )
    server.services = SimpleNamespace()
    monkeypatch.setattr("octop.infra.knowledge.auto_sync.shutdown_auto_sync", closer("auto"))
    monkeypatch.setattr("octop.infra.knowledge.jobs.shutdown_index_jobs", closer("index"))

    with pytest.raises(RuntimeError, match="gateway close failed"):
        await server._shutdown_business()

    assert all(resource.closed for resource in resources.values())
    assert server.app_runtime.proactive_scheduler is None
    assert server.app_runtime.cron_manager is None
    assert server.app_runtime.gateway is None
    assert server.app_runtime.business_running is False


@pytest.mark.asyncio
async def test_shutdown_finishes_cleanup_before_propagating_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = OctopServer()
    started = asyncio.Event()
    release = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def gateway_shutdown() -> None:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise
        cleanup_finished.set()

    proactive = SimpleNamespace(shutdown=AsyncMock())
    cron = SimpleNamespace(shutdown=AsyncMock())
    gateway = SimpleNamespace(shutdown=gateway_shutdown)
    registry = SimpleNamespace(shutdown=AsyncMock())
    server.app_runtime = SimpleNamespace(
        proactive_scheduler=proactive,
        cron_manager=cron,
        gateway=gateway,
        agent_registry=registry,
        business_running=True,
    )
    server.services = SimpleNamespace()
    auto_sync = AsyncMock()
    index_jobs = AsyncMock()
    monkeypatch.setattr("octop.infra.knowledge.auto_sync.shutdown_auto_sync", auto_sync)
    monkeypatch.setattr("octop.infra.knowledge.jobs.shutdown_index_jobs", index_jobs)

    shutdown = asyncio.create_task(server._shutdown_business())
    await started.wait()
    shutdown.cancel()
    await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    assert not cleanup_cancelled.is_set()
    assert not cleanup_finished.is_set()
    assert not shutdown.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert cleanup_finished.is_set()

    auto_sync.assert_awaited_once_with(server)
    proactive.shutdown.assert_awaited_once()
    cron.shutdown.assert_awaited_once()
    registry.shutdown.assert_awaited_once()
    index_jobs.assert_awaited_once_with(server.services)
    assert server.app_runtime.proactive_scheduler is None
    assert server.app_runtime.cron_manager is None
    assert server.app_runtime.gateway is None
    assert server.app_runtime.business_running is False
