"""Manual source-upgrade guidance and service restart API."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from octop.api.deps import current_user, require_permission
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup.self_update import get_editable_path, get_local_version, green_packages_dir
from octop.infra.setup.service import (
    ServiceRuntime,
    build_runtime,
    detect_service_mode,
    is_service_installed,
    restart_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["update"])

UPGRADE_URL = "https://github.com/whl736989911/AI-MAITU"


class ManualUpdateStatus(BaseModel):
    current_version: str
    latest_version: None = None
    has_update: Literal[False] = False
    error: None = None
    source: None = None
    release_notes: None = None
    is_editable: bool
    service_mode: str | None
    desktop: bool


def _is_desktop_process() -> bool:
    if green_packages_dir() is not None:
        return True
    raw = (os.environ.get("OCTOP_DESKTOP") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _manual_status() -> dict[str, Any]:
    return {
        "current_version": get_local_version(),
        "latest_version": None,
        "has_update": False,
        "error": None,
        "source": None,
        "release_notes": None,
        "is_editable": get_editable_path() is not None,
        "service_mode": detect_service_mode(),
        "desktop": _is_desktop_process(),
    }


@router.get(
    "/status",
    response_model=ManualUpdateStatus,
    summary="Show manual upgrade information",
    description="Returns the installed version without querying a package index.",
)
async def update_status(_: Any = Depends(current_user)) -> dict[str, Any]:
    """Return current version and manual-only upgrade status."""
    return await asyncio.to_thread(_manual_status)


@router.post(
    "/check",
    response_model=ManualUpdateStatus,
    summary="Show manual upgrade information",
    description="Reports the installed version; automatic update checks are disabled.",
)
async def check_for_updates(_: Any = Depends(require_permission("update"))) -> dict[str, Any]:
    """Return current version without checking an external package index."""
    return await asyncio.to_thread(_manual_status)


@router.post(
    "/upgrade",
    summary="Reject automatic upgrades",
    description="Upgrades must be performed manually from the project GitHub source.",
)
async def trigger_upgrade(
    _: Any = Depends(require_permission("update")),
) -> dict[str, Any]:
    """Reject package installation and direct the caller to the project source."""
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"Automatic upgrades are disabled. Upgrade manually from {UPGRADE_URL}.",
        details={"upgrade_url": UPGRADE_URL},
    )


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
