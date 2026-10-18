"""Tests for update status, queue handoff, and service restart."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

from octop.api.routers import update as update_router
from octop.infra.errors import ErrorCode, OctopError


@pytest.mark.asyncio
async def test_upgrade_surfaces_queue_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    def collision(version: str | None) -> None:
        raise update_router.UpdateQueueConflict("An upgrade request is already queued.")

    monkeypatch.setattr(update_router.update_client, "queue_upgrade", collision)
    with pytest.raises(HTTPException) as exc_info:
        await update_router.trigger_upgrade(
            Request({"type": "http", "headers": []}),
            update_router.UpgradeRequest(version="1.3.0"),
            _=None,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_restart_endpoint_schedules_background_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOP_SERVICE_MODE", "systemd")
    restarted: list[object] = []
    fake_runtime = type(
        "Runtime",
        (),
        {"mode": "systemd", "scope": None, "run_as_user": None},
    )()

    monkeypatch.setattr(update_router, "build_runtime", lambda mode: fake_runtime)
    monkeypatch.setattr(update_router, "is_service_installed", lambda *_, **__: True)
    monkeypatch.setattr(update_router, "restart_service", lambda runtime: restarted.append(runtime))

    bg = BackgroundTasks()
    result = await update_router.restart_service_endpoint(bg, _=None)

    assert result == {"status": "restarting", "service_mode": "systemd"}
    assert restarted == []
    for task in bg.tasks:
        await task()
    assert restarted == [fake_runtime]


@pytest.mark.asyncio
async def test_restart_endpoint_rejects_when_service_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOP_SERVICE_MODE", "systemd")
    fake_runtime = type(
        "Runtime",
        (),
        {"mode": "systemd", "scope": None, "run_as_user": None},
    )()
    monkeypatch.setattr(update_router, "build_runtime", lambda mode: fake_runtime)
    monkeypatch.setattr(update_router, "is_service_installed", lambda *_, **__: False)

    bg = BackgroundTasks()
    with pytest.raises(OctopError) as exc_info:
        await update_router.restart_service_endpoint(bg, _=None)

    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
    assert bg.tasks == []


@pytest.mark.asyncio
async def test_restart_endpoint_desktop_schedules_process_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(update_router, "_is_desktop_process", lambda: True)
    monkeypatch.setattr(update_router, "_restart_desktop_process", lambda: called.append(True))

    bg = BackgroundTasks()
    result = await update_router.restart_service_endpoint(bg, _=None)

    assert result == {"status": "restarting", "service_mode": "desktop"}
    assert called == []
    for task in bg.tasks:
        await task()
    assert called == [True]
