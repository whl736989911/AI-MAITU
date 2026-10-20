"""Authenticated release distribution and upgrade queue behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from octop.infra.setup import update_client as update_module


@pytest.fixture
def configured_release_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    client = update_module.update_client
    monkeypatch.setattr(client, "_loaded", False)
    monkeypatch.setattr(client, "_cache", {})
    monkeypatch.setattr(update_module, "get_local_version", lambda: "1.2.3")
    monkeypatch.setenv("MAITU_UPDATE_CENTER_URL", "https://updates.example.test")
    monkeypatch.setenv("MAITU_UPDATE_CLIENT_TOKEN", "integration-client-token")
    monkeypatch.setenv(
        "MAITU_UPDATE_TRUSTED_KEYS_FILE", str(tmp_path / "release-trusted-keys.json")
    )
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "updater-ready.json").write_text(
        '{"mode":"docker","ready":true,"updated_at":"' + datetime.now(UTC).isoformat() + '"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAITU_UPDATE_QUEUE_DIR", str(queue))
    claims = {
        "product": "maitu",
        "protocol": 1,
        "release_id": "a" * 32,
        "version": "1.3.0",
        "filename": "maitu-1.3.0.zip",
        "sha256": "b" * 64,
        "size": 123,
        "release_notes": "Integration release",
        "package_type": "source",
        "platform": "linux",
        "architecture": "amd64",
    }
    monkeypatch.setattr(
        client,
        "_fetch_manifest",
        lambda: {"claims": claims, "token": "verified", "has_update": True},
    )
    return client


async def test_authenticated_release_check_queues_exact_version_once(
    env_admin_client: Any,
    configured_release_client: Any,
) -> None:
    client, auth = env_admin_client
    checked = await client.post("/api/update/check", headers=auth)
    assert checked.status_code == 200
    assert checked.json()["has_update"] is True
    assert checked.json()["latest_version"] == "1.3.0"
    assert checked.json()["release_notes"] == "Integration release"

    stale = await client.post("/api/update/upgrade", json={"version": "9.9.9"}, headers=auth)
    assert stale.status_code == 503

    queued = await client.post("/api/update/upgrade", json={"version": "1.3.0"}, headers=auth)
    assert queued.status_code == 200
    request = queued.json()
    assert request["status"] == "queued"
    assert request["version"] == "1.3.0"
    assert request["request_id"]

    status = await client.get("/api/update/status", headers=auth)
    assert status.status_code == 200
    assert status.json()["upgrade_phase"] == "queued"
    assert status.json()["upgrade_target"] == "1.3.0"

    collision = await client.post("/api/update/upgrade", json={"version": "1.3.0"}, headers=auth)
    assert collision.status_code == 409


async def test_unconfigured_check_reports_missing_fields_without_a_completed_check(
    env_admin_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fields = (
        "MAITU_UPDATE_CENTER_URL",
        "MAITU_UPDATE_CLIENT_TOKEN",
        "MAITU_UPDATE_TRUSTED_KEYS_FILE",
    )
    for name in (*fields, "MAITU_UPDATE_QUEUE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.setattr(update_module.update_client, "_loaded", False)
    monkeypatch.setattr(update_module.update_client, "_cache", {})
    client, auth = env_admin_client
    response = await client.post("/api/update/check", headers={**auth, "Accept-Language": "zh"})
    assert response.status_code == 200
    status = response.json()
    assert status["missing_configuration"] == list(fields)
    assert status["center_configured"] is False
    assert status["checked_at"] is None
    assert status["has_update"] is False
    assert status["latest_version"] is None
    assert "尚未配置更新检查" in status["error"]
    assert status["install_available"] is False


async def test_non_admin_cannot_queue_upgrade(
    env_admin_alice: Any,
    configured_release_client: Any,
) -> None:
    client, _server, _admin_auth, alice_auth = env_admin_alice
    response = await client.post(
        "/api/update/upgrade", json={"version": "1.3.0"}, headers=alice_auth
    )
    assert response.status_code == 403
