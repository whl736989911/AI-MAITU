"""Cleanup helpers for per-instance connector CLI config directories."""

from __future__ import annotations

import shutil
from typing import Any

from octop.infra.utils.paths import PathLayout

_CLI_KINDS = frozenset({"feishu-cli", "wecom-cli"})


def resolve_cli_config_key(creds: dict[str, Any]) -> str:
    """Return the CONFIG_DIR key for a connector CLI instance.

    Prefer ``cli_config_key``, then ``instance_id``. Never fall back to
    ``app_id`` / ``bot_id`` — those collide across users sharing one app/bot.
    """
    for field in ("cli_config_key", "instance_id"):
        val = str(creds.get(field) or "").strip()
        if val:
            return val
    raise ValueError("cli_config_key or instance_id is required for connector CLI isolation")


def cleanup_keys_for_creds(kind: str, creds: dict[str, Any]) -> set[str]:
    """Return CONFIG_DIR keys that may hold CLI state for these credentials."""
    keys: set[str] = set()
    if kind == "feishu-cli":
        for field in ("cli_config_key", "instance_id"):
            val = str(creds.get(field) or "").strip()
            if val:
                keys.add(val)
    elif kind == "wecom-cli":
        # bot_id: legacy dirs only (pre-isolation); do not use at runtime.
        for field in ("cli_config_key", "instance_id", "bot_id"):
            val = str(creds.get(field) or "").strip()
            if val:
                keys.add(val)
    return keys


def remove_connector_cli_dirs(kind: str, *keys: str, keep: set[str] | None = None) -> None:
    """Best-effort remove ``~/.octop/connector-cli/<kind>/<key>/`` trees."""
    if kind not in _CLI_KINDS:
        return
    layout = PathLayout.from_env()
    retain = {
        layout.connector_cli_instance_dir(kind, str(key).strip()).name
        for key in (keep or set())
        if str(key).strip()
    }
    for raw in keys:
        key = str(raw or "").strip()
        if not key:
            continue
        path = layout.connector_cli_instance_dir(kind, key)
        if path.name in retain:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def cleanup_creds_cli_dirs(
    kind: str,
    creds: dict[str, Any],
    *,
    keep: set[str] | None = None,
) -> None:
    keys = cleanup_keys_for_creds(kind, creds)
    remove_connector_cli_dirs(kind, *sorted(keys), keep=keep)
