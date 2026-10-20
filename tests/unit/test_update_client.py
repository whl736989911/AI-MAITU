"""Trust, freshness, and queue collision behavior for release updates."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.setup import update_client as client_module
from octop.infra.setup.update_client import UpdateClient, UpdateClientError, UpdateQueueConflict


@pytest.fixture
def signed_center(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[UpdateClient, dict[str, Any], Any]:
    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "release-trusted-keys.json"
    pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    key_path.write_text(json.dumps({"maitu-release-1": pem}), encoding="utf-8")
    monkeypatch.setenv("MAITU_UPDATE_CENTER_URL", "http://127.0.0.1:8088")
    monkeypatch.setenv("MAITU_UPDATE_ALLOW_LOOPBACK", "true")
    monkeypatch.setenv("MAITU_UPDATE_CLIENT_TOKEN", "test-client-token")
    monkeypatch.setenv("MAITU_UPDATE_TRUSTED_KEYS_FILE", str(key_path))
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "updater-ready.json").write_text(
        json.dumps(
            {
                "mode": "docker",
                "ready": True,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAITU_UPDATE_QUEUE_DIR", str(queue))
    monkeypatch.setenv("MAITU_UPDATE_CHECK_INTERVAL", "3600")
    monkeypatch.setattr(client_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(client_module, "get_local_version", lambda: "1.2.3")

    def sign(claims: dict[str, Any]) -> str:
        return jwt.encode(
            claims, private_key, algorithm="EdDSA", headers={"kid": "maitu-release-1"}
        )

    claims: dict[str, Any] = {
        "iss": "maitu-license-center",
        "aud": "maitu-updater",
        "product": "maitu",
        "protocol": 1,
        "release_id": "a" * 32,
        "version": "1.3.0",
        "filename": "maitu-1.3.0.zip",
        "sha256": "b" * 64,
        "size": 123,
        "release_notes": "Trusted test release",
        "package_type": "source",
        "platform": "linux",
        "architecture": "amd64",
        "iat": int(datetime.now(UTC).timestamp()),
    }
    response = {"manifest": sign(claims)}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps(response).encode()

    class FakeOpener:
        def open(self, *args: Any, **kwargs: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(client_module.urllib.request, "build_opener", lambda *args: FakeOpener())
    return UpdateClient(), response, sign


def test_signed_manifest_and_host_state(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, _, _ = signed_center
    status = client.check(force=True)
    assert status["latest_version"] == "1.3.0"
    assert status["has_update"] is True
    assert status["release_notes"] == "Trusted test release"
    request = client.queue_upgrade("1.3.0")
    assert request["status"] == "queued"
    assert client.status_payload()["upgrade_phase"] == "queued"
    assert client.queue_dir is not None
    client.queue_dir.joinpath("upgrade-state.json").write_text(
        json.dumps(
            {
                "phase": "building",
                "target_version": "1.3.0",
                "error": None,
                "request_id": request["request_id"],
                "updated_at": "now",
            }
        )
    )
    assert client.status_payload()["upgrade_phase"] == "building"
    assert client.status_payload()["upgrade_target"] == "1.3.0"


def test_rejects_bad_signature_and_incompatible_manifest(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, response, sign = signed_center
    trusted_token = response["manifest"]
    response["manifest"] = trusted_token[:-3] + "abc"
    rejected_signature = client.check(force=True)
    assert rejected_signature["error"] is not None
    assert rejected_signature["latest_version"] is None

    incompatible = jwt.decode(trusted_token, options={"verify_signature": False})
    incompatible["architecture"] = "arm64"
    response["manifest"] = sign(incompatible)
    rejected_architecture = client.check(force=True)
    assert rejected_architecture["error"] is not None
    assert rejected_architecture["latest_version"] is None


def test_rejects_downgrade_and_stale_upgrade_target(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, response, sign = signed_center
    trusted_token = response["manifest"]
    downgraded = jwt.decode(trusted_token, options={"verify_signature": False})
    downgraded["version"] = "1.2.2"
    downgraded["filename"] = "maitu-1.2.2.zip"
    response["manifest"] = sign(downgraded)
    downgrade_status = client.check(force=True)
    assert downgrade_status["has_update"] is False
    assert downgrade_status["error"] is not None

    response["manifest"] = trusted_token
    assert client.check(force=True)["has_update"] is True
    with pytest.raises(UpdateClientError):
        client.queue_upgrade("9.9.9")


def test_queue_collision_does_not_overwrite_pending_request(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, _, _ = signed_center
    client.check(force=True)
    client.queue_upgrade("1.3.0")
    assert client.queue_dir is not None
    queue_file = client.queue_dir / "upgrade-request.json"
    before = queue_file.read_bytes()
    with pytest.raises(UpdateQueueConflict):
        client.queue_upgrade("1.3.0")
    assert queue_file.read_bytes() == before


def test_parallel_upgrade_requests_have_one_winner(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, _, _ = signed_center

    def queue() -> str:
        try:
            client.queue_upgrade("1.3.0")
            return "queued"
        except UpdateQueueConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: queue(), range(2)))
    assert outcomes.count("queued") == 1
    assert outcomes.count("conflict") == 1
    assert client.queue_dir is not None
    request = json.loads((client.queue_dir / "upgrade-request.json").read_text())
    assert request["version"] == "1.3.0"


def test_parallel_auto_checks_are_single_flight(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, _, _ = signed_center
    calls = 0
    count_lock = threading.Lock()
    original = client._fetch_manifest

    def counted() -> dict[str, Any] | None:
        nonlocal calls
        with count_lock:
            calls += 1
        return original()

    client._fetch_manifest = counted
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.check(), range(8)))
    assert calls == 1
    assert all(result["latest_version"] == "1.3.0" for result in results)


def test_rejects_non_loopback_http_before_network(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center
    monkeypatch.setenv("MAITU_UPDATE_CENTER_URL", "http://updates.example.test")
    calls = 0

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("non-loopback HTTP must not reach the network")

    monkeypatch.setattr(client_module.urllib.request, "build_opener", unexpected)
    status = client.check(force=True)
    assert status["error"] is not None
    assert calls == 0


@pytest.mark.parametrize("partial", [False, True])
def test_missing_configuration_is_not_a_successful_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, partial: bool
) -> None:
    fields = (
        "MAITU_UPDATE_CENTER_URL",
        "MAITU_UPDATE_CLIENT_TOKEN",
        "MAITU_UPDATE_TRUSTED_KEYS_FILE",
    )
    for name in fields:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.delenv("MAITU_UPDATE_QUEUE_DIR", raising=False)
    if partial:
        monkeypatch.setenv(fields[0], "https://updates.example.test")

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("incomplete configuration must not contact the center")

    monkeypatch.setattr(client_module.urllib.request, "build_opener", unexpected)
    client = UpdateClient()
    status = client.check(force=True)
    assert status["center_configured"] is False
    assert status["missing_configuration"] == list(fields[1:] if partial else fields)
    assert status["error"]
    assert status["checked_at"] is None
    assert status["latest_version"] is None
    assert status["has_update"] is False
    assert not (tmp_path / "update-cache.json").exists()


def test_removing_configuration_discards_cached_release(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center
    assert client.check(force=True)["has_update"] is True
    monkeypatch.delenv("MAITU_UPDATE_CLIENT_TOKEN")
    # A fresh successful cache must not mask missing configuration.
    status = client.check()
    assert status["missing_configuration"] == ["MAITU_UPDATE_CLIENT_TOKEN"]
    assert status["error"]
    assert status["checked_at"] is None
    assert status["latest_version"] is None
    assert status["has_update"] is False


def test_missing_configuration_invalidates_disk_cache_before_restart(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center
    assert client.check(force=True)["has_update"] is True
    monkeypatch.delenv("MAITU_UPDATE_CLIENT_TOKEN")
    assert client.check()["checked_at"] is None
    monkeypatch.setenv("MAITU_UPDATE_CLIENT_TOKEN", "replacement-test-token")
    restarted = UpdateClient()
    calls = 0

    def unavailable() -> None:
        nonlocal calls
        calls += 1
        raise UpdateClientError("unavailable after restart")

    monkeypatch.setattr(restarted, "_fetch_manifest", unavailable)
    status = restarted.check()
    assert calls == 1
    assert status["latest_version"] is None
    assert status["has_update"] is False
    assert status["error"] == "unavailable after restart"


def test_configured_center_without_a_release_is_a_completed_check(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
) -> None:
    client, response, _ = signed_center
    response["manifest"] = None
    status = client.check(force=True)
    assert status["center_configured"] is True
    assert status["missing_configuration"] == []
    assert status["checked_at"]
    assert status["error"] is None
    assert status["latest_version"] is None
    assert status["has_update"] is False


def test_same_version_is_not_an_upgrade(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center
    monkeypatch.setattr(client_module, "get_local_version", lambda: "1.3.0")
    status = client.check(force=True)
    assert status["latest_version"] == "1.3.0"
    assert status["has_update"] is False
    assert status["error"] is None


def test_release_check_does_not_require_a_host_installer(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _, _ = signed_center
    monkeypatch.delenv("MAITU_UPDATE_QUEUE_DIR")
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    status = client.check(force=True)
    assert status["has_update"] is True
    assert status["install_available"] is False
    with pytest.raises(UpdateClientError, match="Host updater is not configured"):
        client.queue_upgrade(status["latest_version"])


def test_install_requires_a_live_host_updater_heartbeat(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center
    assert client.check(force=True)["install_available"] is True
    assert client.queue_dir is not None
    heartbeat = client.queue_dir / "updater-ready.json"
    heartbeat.unlink()
    status = client.check(force=True)
    assert status["has_update"] is True
    assert status["install_available"] is False
    with pytest.raises(UpdateClientError, match="Host updater is not configured"):
        client.queue_upgrade(status["latest_version"])
    heartbeat.write_text(
        json.dumps(
            {
                "mode": "native",
                "ready": True,
                "updated_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    assert client.check(force=True)["install_available"] is False
    heartbeat.write_text(
        json.dumps({"mode": "native", "ready": True, "updated_at": "not-a-time"}),
        encoding="utf-8",
    )
    assert client.check(force=True)["install_available"] is False


def test_center_failure_is_reported_without_claiming_latest(
    signed_center: tuple[UpdateClient, dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signed_center

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise OSError("center unavailable")

    monkeypatch.setattr(client_module.urllib.request, "build_opener", unavailable)
    status = client.check(force=True)
    assert status["latest_version"] is None
    assert status["error"] is not None
