"""Small disk-space guards for operations with significant local writes."""

from __future__ import annotations

import shutil
from pathlib import Path

from octop.infra.errors import ErrorCode, OctopError


def available_bytes(path: str | Path) -> int | None:
    """Return free bytes for *path*, or ``None`` when it cannot be measured."""
    try:
        return shutil.disk_usage(path).free
    except (OSError, ValueError):
        return None


def require_headroom(
    path: str | Path,
    *,
    minimum_bytes: int,
    label: str = "",
) -> None:
    """Raise ``DISK_FULL`` when a measurable filesystem lacks required space."""
    available = available_bytes(path)
    if available is None or available >= minimum_bytes:
        return None

    path_text = str(path)
    shortfall = minimum_bytes - available
    purpose = f" for {label}" if label else ""
    message = f"Insufficient disk space at {path_text}{purpose}: short by {shortfall} bytes"
    raise OctopError(
        ErrorCode.DISK_FULL,
        message,
        details={
            "available_bytes": available,
            "required_bytes": minimum_bytes,
            "path": path_text,
        },
    )
