"""Integration tests for the shipped-only plugin installation policy."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from octop.infra.agents.plugins.bundled import default_bundled_plugins_root
from tests.support.auth import create_user

_FIXTURE = default_bundled_plugins_root() / "weather"


def _echo_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path in _FIXTURE.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=f"weather/{path.relative_to(_FIXTURE).as_posix()}")
    return buf.getvalue()


def _disk_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


async def test_upload_plugin_zip_is_rejected_without_disk_or_config_changes(env: Any) -> None:
    client, srv, auth = env
    manager = srv.plugin_manager
    assert manager is not None
    plugins_before = _disk_snapshot(manager.plugins_dir)
    config_path = srv.paths.config
    config_before = config_path.read_bytes() if config_path.is_file() else None

    response = await client.post(
        "/api/plugins/upload",
        files={"file": ("echo-tool.zip", _echo_zip(), "application/zip")},
        data={"force": "true"},
        headers=auth,
    )

    assert response.status_code == 403, response.text
    assert _disk_snapshot(manager.plugins_dir) == plugins_before
    assert (config_path.read_bytes() if config_path.is_file() else None) == config_before


async def test_upload_plugin_rejects_non_zip(env: Any) -> None:
    client, _srv, auth = env
    response = await client.post(
        "/api/plugins/upload",
        files={"file": ("not-a-plugin.zip", b"<!DOCTYPE html>blob page", "application/zip")},
        headers=auth,
    )
    assert response.status_code == 403, response.text


async def test_upload_over_limit_is_rejected_before_installing(env: Any, monkeypatch: Any) -> None:
    client, srv, auth = env
    monkeypatch.setattr(
        srv, "services", replace(srv.services, config=replace(srv.services.config, max_upload_mb=1))
    )
    manager = srv.plugin_manager
    assert manager is not None
    before = _disk_snapshot(manager.plugins_dir)

    response = await client.post(
        "/api/plugins/upload",
        files={"file": ("oversized.zip", b"x" * (1024 * 1024 + 1), "application/zip")},
        headers=auth,
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "PLUGIN_INVALID_ARCHIVE"
    assert response.json()["error"]["details"]["max_mb"] == 1
    assert _disk_snapshot(manager.plugins_dir) == before


async def test_upload_plugin_requires_the_plugins_key(env_admin_alice: Any) -> None:
    client, _srv, _admin_auth, alice_auth = env_admin_alice
    response = await client.post(
        "/api/plugins/upload",
        files={"file": ("echo-tool.zip", _echo_zip(), "application/zip")},
        headers=alice_auth,
    )
    assert response.status_code == 403, response.text


async def test_list_plugins_follows_the_plugins_key(env: Any) -> None:
    client, _srv, admin_auth = env
    without_key = await create_user(client, admin_auth, username="plugin_reader")
    refused = await client.get("/api/plugins", headers=without_key)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["permission"] == "plugins"

    with_key = await create_user(
        client, admin_auth, username="plugin_operator", permissions=["plugins"]
    )
    allowed = await client.get("/api/plugins", headers=with_key)
    assert allowed.status_code == 200, allowed.text
    assert isinstance(allowed.json(), list)
