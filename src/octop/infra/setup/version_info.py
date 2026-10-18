"""Installed version and install-mode metadata used by release status surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path

_PACKAGE_NAME = "octop"


def green_packages_dir() -> Path | None:
    """Return the portable install target directory, if configured."""
    raw = (os.environ.get("OCTOP_GREEN_PACKAGES") or "").strip()
    return Path(raw).expanduser() if raw else None


def get_local_version() -> str:
    try:
        from importlib.metadata import version

        return version(_PACKAGE_NAME)
    except Exception:
        return "0.0.0"


def get_editable_path() -> str | None:
    try:
        import importlib.metadata as metadata

        distribution = metadata.distribution(_PACKAGE_NAME)
        direct_url = distribution.read_text("direct_url.json")
        if direct_url:
            info = json.loads(direct_url)
            if info.get("dir_info", {}).get("editable", False):
                return info.get("url", "").replace("file://", "") or None
    except Exception:
        pass
    return None
