"""Tests for manual-only update API behavior and service restart."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks

from octop.api.routers import update as update_router
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup import self_update


@pytest.fixture(autouse=True)
def _block_package_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("manual-only update routes must not access PyPI or install packages")

    monkeypatch.setattr(update_router, "fetch_pypi_info", unexpected, raising=False)
    monkeypatch.setattr(update_router, "run_upgrade", unexpected, raising=False)
    monkeypatch.setattr(self_update, "fetch_pypi_info", unexpected)
    monkeypatch.setattr(self_update, "run_upgrade", unexpected)
    monkeypatch.setattr(update_router, "get_local_version", lambda: "1.2.3")
    monkeypatch.setattr(update_router, "get_editable_path", lambda: None)
    monkeypatch.setattr(update_router, "detect_service_mode", lambda: "systemd")
    monkeypatch.setattr(update_router, "_is_desktop_process", lambda: False)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["update_status", "check_for_updates"])
async def test_status_and_check_report_manual_only_status(handler: str) -> None:
    payload = await getattr(update_router, handler)(_=None)

    assert payload == {
        "current_version": "1.2.3",
        "latest_version": None,
        "has_update": False,
        "error": None,
        "source": None,
        "release_notes": None,
        "is_editable": False,
        "service_mode": "systemd",
        "desktop": False,
    }


@pytest.mark.asyncio
async def test_upgrade_is_forbidden_without_starting_an_install() -> None:
    with pytest.raises(OctopError) as exc_info:
        await update_router.trigger_upgrade(_=None)

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert update_router.UPGRADE_URL in str(exc_info.value)


@pytest.mark.asyncio
async def test_restart_endpoint_schedules_background_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
