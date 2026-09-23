"""Integration tests for /api/update."""

from __future__ import annotations

from typing import Any


async def test_update_status_exposes_installed_version_without_upstream_release(
    env_admin_client: Any,
) -> None:
    c, auth = env_admin_client
    response = await c.get("/api/update/status", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["current_version"]
    assert body["latest_version"] is None
    assert body["has_update"] is False
    assert body["service_mode"] in (None, "systemd", "launchd")
    assert body["source"] is None


async def test_update_check_does_not_offer_upstream_package(env_admin_client: Any) -> None:
    c, auth = env_admin_client
    response = await c.post("/api/update/check", headers=auth)
    assert response.status_code == 200
    assert response.json()["has_update"] is False
    assert response.json()["latest_version"] is None


async def test_update_upgrade_rejects_upstream_package_install(env_admin_client: Any) -> None:
    c, auth = env_admin_client
    response = await c.post("/api/update/upgrade", json={"version": "9.9.9"}, headers=auth)
    assert response.status_code == 403
    assert (
        response.json()["error"]["details"]["upgrade_url"]
        == "https://github.com/whl736989911/AI-MAITU"
    )
