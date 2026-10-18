"""Authorization-center release checks, host updater handoff, and restart API."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from octop.api.deps import current_user, require_permission
from octop.i18n import tr
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup.service import (
    ServiceRuntime,
    build_runtime,
    detect_service_mode,
    is_service_installed,
    restart_service,
)
from octop.infra.setup.update_client import UpdateClientError, UpdateQueueConflict, update_client
from octop.infra.utils.locale import resolve_request_locale

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/update", tags=["update"])


UpgradePhase = Literal[
    "idle",
    "queued",
    "downloading",
    "validating",
    "building",
    "backing_up",
    "stopping",
    "installing",
    "restarting",
    "verifying",
    "succeeded",
    "failed",
]


class UpdateStatus(BaseModel):
    current_version: str
    latest_version: str | None
    has_update: bool
    error: str | None
    source: str | None
    release_notes: str | None
    is_editable: bool
    service_mode: str | None
    desktop: bool
    center_configured: bool
    install_available: bool
    checked_at: str | None
    upgrade_phase: UpgradePhase
    upgrade_error: str | None
    upgrade_target: str | None


class UpgradeRequest(BaseModel):
    version: str | None = None


class UpgradeQueueResponse(BaseModel):
    status: Literal["queued"]
    request_id: str
    version: str


def _is_desktop_process() -> bool:
    from octop.infra.setup.version_info import green_packages_dir

    if green_packages_dir() is not None:
        return True
    raw = (os.environ.get("OCTOP_DESKTOP") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _status(locale: str) -> dict[str, Any]:
    from octop.infra.setup.version_info import get_editable_path

    data = update_client.status_payload()
    data["is_editable"] = get_editable_path() is not None
    data["service_mode"] = detect_service_mode()
    data["desktop"] = _is_desktop_process()
    if data["error"]:
        data["error"] = tr("update.check_failed", locale, detail=data["error"])
    if data["upgrade_phase"] == "failed" and data["upgrade_error"]:
        data["upgrade_error"] = tr("update.host_failed", locale, detail=data["upgrade_error"])
    return data


@router.get("/status", response_model=UpdateStatus, summary="Get signed release update status")
async def update_status(request: Request, _: Any = Depends(current_user)) -> dict[str, Any]:
    await asyncio.to_thread(update_client.check)
    return await asyncio.to_thread(_status, resolve_request_locale(request))


@router.post("/check", response_model=UpdateStatus, summary="Check authorization-center releases")
async def check_for_updates(
    request: Request, _: Any = Depends(require_permission("update"))
) -> dict[str, Any]:
    await asyncio.to_thread(update_client.check, force=True)
    return await asyncio.to_thread(_status, resolve_request_locale(request))


@router.post(
    "/upgrade", response_model=UpgradeQueueResponse, summary="Queue a confirmed source upgrade"
)
async def trigger_upgrade(
    request: Request,
    body: UpgradeRequest | None = None,
    _: Any = Depends(require_permission("update")),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(update_client.queue_upgrade, body.version if body else None)
    except UpdateQueueConflict as exc:
        raise HTTPException(
            status_code=409, detail=tr("update.queue_conflict", resolve_request_locale(request))
        ) from exc
    except UpdateClientError as exc:
        raise HTTPException(
            status_code=503,
            detail=tr("update.queue_failed", resolve_request_locale(request), detail=str(exc)),
        ) from exc


def _restart_desktop_process() -> None:
    time.sleep(0.4)
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.execv(argv[0], argv)


def _restart_service_task(runtime: ServiceRuntime) -> None:
    try:
        restart_service(runtime)
    except Exception:
        logger.exception("background service restart failed")


@router.post("/restart")
async def restart_service_endpoint(
    background_tasks: BackgroundTasks,
    _: Any = Depends(require_permission("update")),
) -> dict[str, Any]:
    if _is_desktop_process():
        background_tasks.add_task(_restart_desktop_process)
        return {"status": "restarting", "service_mode": "desktop"}
    mode = detect_service_mode()
    if mode is None:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "service restart is only available when OCTOP_SERVICE_MODE is set",
        )
    try:
        runtime = build_runtime(mode=mode)
        if not is_service_installed(
            runtime.mode,
            scope=runtime.scope,
            run_as_user=runtime.run_as_user,
        ):
            raise RuntimeError(
                f"octop system service is not installed (expected unit for mode={runtime.mode})"
            )
    except RuntimeError as exc:
        raise OctopError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except Exception as exc:
        raise OctopError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
    background_tasks.add_task(_restart_service_task, runtime)
    return {"status": "restarting", "service_mode": mode}
