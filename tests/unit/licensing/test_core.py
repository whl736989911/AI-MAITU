from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing import LicenseManager


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    now = [1_800_000_000]
    monkeypatch.setattr("octop.infra.licensing.service.time.time", lambda: now[0])
    return now


@pytest.fixture
def license_keys(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    import json

    center = Ed25519PrivateKey.generate()
    pem = (
        center.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8")
    )
    path = tmp_path / "license-trusted-keys.json"
    path.write_text(
        json.dumps({"maitu-license-1": pem, "maitu-license-next": pem}),
        encoding="utf-8",
    )
    return center, path


def signed(
    manager: LicenseManager,
    center: Ed25519PrivateKey,
    *,
    version: int = 1,
    purpose: str = "offline-license",
    status: str = "active",
    issued: int = 1_800_000_000,
    valid_until: int | None = None,
    expires: int = 1_800_200_000,
    restored: bool = False,
    license_id: str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    kid: str = "maitu-license-1",
) -> str:
    claims = {
        "iss": "maitu-license-center",
        "aud": "maitu-instance",
        "protocol": 1,
        "product": "maitu",
        "instance_id": manager.instance_id,
        "key_fingerprint": manager._identity,
        "license_id": license_id,
        "license_version": version,
        "status": status,
        "restored": restored,
        "issued_at": issued,
        "not_before": issued,
        "license_expires_at": expires,
        "valid_until": (expires if status == "active" else issued + 300)
        if valid_until is None
        else valid_until,
        "purpose": purpose,
    }
    if purpose == "online-license":
        claims["challenge"] = "fresh-challenge"
    return jwt.encode(claims, center, algorithm="EdDSA", headers={"kid": kid, "typ": "JWT"})


@pytest.mark.asyncio
async def test_license_trust_keyring_selects_credentials_by_kid(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, trusted_keys = license_keys
    manager = LicenseManager(tmp_path / "state", trusted_keys)
    with pytest.raises(ValueError, match="unknown license signing key"):
        await manager.import_offline(signed(manager, center, kid="untrusted"))
    await manager.import_offline(signed(manager, center, kid="maitu-license-next"))
    assert manager.business_allowed


@pytest.mark.asyncio
async def test_offline_grant_survives_expired_online_cache(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    await manager.import_offline(signed(manager, center))
    online = signed(manager, center, purpose="online-license", valid_until=clock[0] + 10_000)
    claims = manager._credential_valid(online, "online-license", clock[0])
    assert claims is not None
    manager._transition(claims, online, "online")
    await manager._persist()
    clock[0] += 10_001
    assert manager.business_allowed
    assert manager.snapshot()["credential_type"] == "offline"
    assert manager.snapshot()["license_expires_at"] == 1_800_200_000


@pytest.mark.asyncio
async def test_higher_online_revocation_blocks_offline_and_old_grants(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    await manager.import_offline(signed(manager, center, version=1))
    denial = signed(manager, center, version=2, status="revoked", purpose="online-license")
    claims = manager._credential_valid(denial, "online-license", clock[0])
    assert claims is not None
    manager._transition(claims, denial, "online")
    await manager._persist()
    assert not manager.business_allowed
    with pytest.raises(ValueError, match="version rollback"):
        await manager.import_offline(signed(manager, center, version=1))
    with pytest.raises(ValueError, match="restored"):
        await manager.import_offline(signed(manager, center, version=3, purpose="offline-license"))
    await manager.import_offline(
        signed(
            manager,
            center,
            version=3,
            purpose="offline-license",
            restored=True,
            expires=1_800_300_000,
        )
    )
    assert manager.business_allowed


@pytest.mark.asyncio
async def test_equal_version_canonical_conflict_rejected(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    await manager.import_offline(signed(manager, center))
    with pytest.raises(ValueError, match="canonical entitlement conflict"):
        await manager.import_offline(
            signed(manager, center, license_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        )
    assert manager.business_allowed


@pytest.mark.asyncio
async def test_reminder_is_atomic_per_actual_expiry_and_server_day(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    expiry = clock[0] + 2 * 86400
    await manager.import_offline(signed(manager, center, expires=expiry, valid_until=expiry))
    second_manager = LicenseManager(tmp_path / "state", public_path)
    first, second = await asyncio.gather(
        manager.claim_reminder(7, "UTC"), second_manager.claim_reminder(7, "UTC")
    )
    assert {first["show"], second["show"]} == {True, False}
    assert first["expires_at"] == expiry
    clock[0] += 86400
    assert (await manager.claim_reminder(7, "UTC"))["show"] is True


@pytest.mark.asyncio
async def test_malformed_token_does_not_replace_valid_grant(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    await manager.import_offline(signed(manager, center))
    with pytest.raises(ValueError):
        await manager.import_offline("not-a-jwt")
    assert manager.business_allowed
    assert manager.snapshot()["license_version"] == 1


@pytest.mark.asyncio
async def test_lock_occurs_at_actual_expiry(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    expiry = clock[0] + 600
    await manager.import_offline(signed(manager, center, expires=expiry, valid_until=expiry))
    clock[0] = expiry
    assert manager.business_allowed is False
    assert manager.snapshot()["reason"] == "expired"


@pytest.mark.asyncio
async def test_restart_with_wall_clock_before_persisted_floor_locks(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    state_dir = tmp_path / "state"
    manager = LicenseManager(state_dir, public_path)
    await manager.import_offline(signed(manager, center))
    clock[0] -= 1_000
    restarted = LicenseManager(state_dir, public_path)
    assert restarted.business_allowed is False
    assert restarted.snapshot()["reason"] == "time_anomaly"


@pytest.mark.asyncio
async def test_signed_expired_response_accepts_unchanged_revision(
    tmp_path: Path, clock: list[int], license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    expiry = clock[0] + 600
    await manager.import_offline(signed(manager, center, expires=expiry))
    clock[0] = expiry + 1
    denial = signed(
        manager, center, purpose="online-license", status="expired", issued=clock[0], expires=expiry
    )
    claims = manager._credential_valid(denial, "online-license", clock[0])
    assert claims is not None
    manager._transition(claims, denial, "online")
    await manager._persist()
    restarted = LicenseManager(tmp_path / "state", public_path)
    assert restarted.snapshot()["reason"] == "expired"
    with pytest.raises(ValueError, match="canonical entitlement conflict"):
        await restarted.import_offline(
            signed(restarted, center, issued=clock[0], expires=expiry + 7200)
        )
    await restarted.import_offline(
        signed(restarted, center, version=2, issued=clock[0], expires=expiry + 7200)
    )
    assert restarted.business_allowed


@pytest.mark.asyncio
async def test_online_calibration_recovers_wall_rollback_and_rejects_stale_challenge(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center, public_path = license_keys
    real_now = clock[0]
    state_dir = tmp_path / "state"
    manager = LicenseManager(state_dir, public_path)
    await manager.import_offline(signed(manager, center))
    clock[0] -= 1000
    restarted = LicenseManager(state_dir, public_path, "https://license.example.test")
    assert restarted.snapshot()["reason"] == "time_anomaly"
    challenge = ["fresh-challenge"]
    token = signed(
        restarted, center, purpose="online-license", issued=real_now, valid_until=real_now + 3600
    )

    async def request(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("/challenges"):
            return {"challenge": challenge[0]}
        return {"token": token}

    monkeypatch.setattr(restarted, "_request", request)
    await restarted.refresh()
    assert restarted.business_allowed
    challenge[0] = "different-fresh-challenge"
    with pytest.raises(ValueError, match="fresh challenge"):
        await restarted.refresh()
    assert restarted.business_allowed


def test_identity_request_has_stable_public_key_fingerprint(
    tmp_path: Path, license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    _, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    request = manager.export_request()
    public_key = serialization.load_pem_public_key(request["public_key_pem"].encode())
    assert request["instance_id"] == manager.instance_id
    assert request["key_fingerprint"] == manager._identity
    assert public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    proof = jwt.decode(
        request["proof"],
        public_key,
        algorithms=["EdDSA"],
        audience="maitu-license-center",
        issuer=request["instance_id"],
    )
    assert proof["purpose"] == "offline-request"
    assert proof["instance_id"] == manager.instance_id
    assert proof["key_fingerprint"] == manager._identity
    assert LicenseManager(tmp_path / "state", public_path).instance_id == manager.instance_id


@pytest.mark.asyncio
async def test_online_proof_binds_canonical_request_to_challenge(
    tmp_path: Path, license_keys: tuple[Ed25519PrivateKey, Path]
) -> None:
    _, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    body = {
        "instance_id": manager.instance_id,
        "activation_code": "valid-code",
        "challenge": "nonce",
    }
    proof = await manager._proof("activate", "nonce", body)
    public_key = manager._private_key.public_key()
    claims = jwt.decode(
        proof,
        public_key,
        algorithms=["EdDSA"],
        audience="maitu-license-center",
        issuer=manager.instance_id,
    )
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    assert claims["purpose"] == "instance-proof"
    assert claims["operation"] == "activate"
    assert claims["challenge"] == "nonce"
    assert claims["content_hash"] == hashlib.sha256(canonical).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["expiry", "revocation"])
async def test_license_lock_stops_running_work_even_when_persistence_fails(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    expiry = clock[0] + 60
    await manager.import_offline(signed(manager, center, expires=expiry))
    running_work = asyncio.create_task(asyncio.Event().wait())

    async def on_change(allowed: bool) -> None:
        if not allowed:
            running_work.cancel()
            await asyncio.gather(running_work, return_exceptions=True)

    await manager.start(on_change=on_change)

    def disk_full(*args: Any) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("octop.infra.licensing.service._atomic_write", disk_full)
    try:
        if trigger == "expiry":
            clock[0] = expiry
            with pytest.raises(OSError):
                await manager._notify_if_changed()
        else:
            manager.center_url = "https://license.example.test"
            token = signed(
                manager,
                center,
                purpose="online-license",
                status="revoked",
                version=2,
                expires=expiry,
            )

            async def request(path: str, body: dict[str, Any]) -> dict[str, Any]:
                return (
                    {"challenge": "fresh-challenge"}
                    if path.endswith("/challenges")
                    else {"token": token}
                )

            monkeypatch.setattr(manager, "_request", request)
            with pytest.raises(OSError):
                await manager.refresh()
        assert manager.business_allowed is False
        assert running_work.cancelled()
    finally:
        await manager.stop()
        running_work.cancel()
        await asyncio.gather(running_work, return_exceptions=True)


@pytest.mark.asyncio
async def test_deadline_monitor_survives_failed_lock_checkpoint(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center, public_path = license_keys
    manager = LicenseManager(tmp_path / "state", public_path)
    expiry = clock[0] + 60
    await manager.import_offline(signed(manager, center, expires=expiry))
    stopped = asyncio.Event()

    async def on_change(allowed: bool) -> None:
        if not allowed:
            stopped.set()

    def disk_full(*args: Any) -> None:
        raise OSError("No space left on device")

    await manager.start(on_change=on_change)
    monkeypatch.setattr("octop.infra.licensing.service._atomic_write", disk_full)
    try:
        clock[0] = expiry
        await asyncio.wait_for(stopped.wait(), timeout=5)
        await asyncio.sleep(0)
        assert manager.snapshot()["reason"] == "expired"
        assert not manager._tasks[0].done()
    finally:
        await manager.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["online", "offline"])
@pytest.mark.parametrize("fail_write", [False, True])
async def test_restored_grant_requires_completed_durable_write(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    fail_write: bool,
) -> None:
    import threading

    from octop.infra.licensing import service

    center, public_path = license_keys
    state_dir = tmp_path / "state"
    manager = LicenseManager(state_dir, public_path)
    await manager.import_offline(signed(manager, center, status="revoked"))
    original_write = service._atomic_write
    started = threading.Event()
    release = threading.Event()

    def blocked_write(path: Path, data: bytes) -> None:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test checkpoint release was not received")
        if fail_write:
            raise OSError("No space left on device")
        original_write(path, data)

    monkeypatch.setattr(service, "_atomic_write", blocked_write)
    token = signed(
        manager,
        center,
        version=2,
        restored=True,
        purpose=f"{entry}-license",
        valid_until=clock[0] + 3600,
    )
    if entry == "online":
        manager.center_url = "https://license.example.test"

        async def request(path: str, body: dict[str, Any]) -> dict[str, Any]:
            return (
                {"challenge": "fresh-challenge"}
                if path.endswith("/challenges")
                else {"token": token}
            )

        monkeypatch.setattr(manager, "_request", request)
    task = asyncio.create_task(
        manager.refresh() if entry == "online" else manager.import_offline(token)
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert manager.business_allowed is False
        release.set()
        if fail_write:
            with pytest.raises(OSError):
                await task
            assert manager.snapshot()["reason"] == "revoked"
        else:
            assert (await task)["business_allowed"] is True
        restarted = LicenseManager(state_dir, public_path)
        try:
            assert restarted.business_allowed is not fail_write
        finally:
            await restarted.stop()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_trusted_time_checkpoints_continue_after_temporary_write_failure(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.licensing import service

    center, public_path = license_keys
    state_dir = tmp_path / "state"
    manager = LicenseManager(state_dir, public_path)
    await manager.import_offline(signed(manager, center))
    original_sleep = asyncio.sleep
    original_write = service._atomic_write
    ticks: asyncio.Queue[None] = asyncio.Queue()
    failed = asyncio.Event()
    saved = asyncio.Event()
    loop = asyncio.get_running_loop()
    attempts = 0

    async def sleep(delay: float) -> None:
        if delay == 60:
            await ticks.get()
        else:
            await original_sleep(delay)

    def write_after_one_failure(path: Path, data: bytes) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            loop.call_soon_threadsafe(failed.set)
            raise OSError("No space left on device")
        original_write(path, data)
        loop.call_soon_threadsafe(saved.set)

    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    monkeypatch.setattr(service, "_atomic_write", write_after_one_failure)
    task = asyncio.create_task(manager._time_loop())
    try:
        clock[0] += 60
        ticks.put_nowait(None)
        await asyncio.wait_for(failed.wait(), timeout=5)
        clock[0] += 60
        ticks.put_nowait(None)
        await asyncio.wait_for(saved.wait(), timeout=5)
        assert not task.done()
        restarted = LicenseManager(state_dir, public_path)
        try:
            assert restarted.business_allowed is True
            assert restarted._state["trusted_time"] == clock[0]
        finally:
            await restarted.stop()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_clock_calibration_survives_snapshot_during_pending_checkpoint(
    tmp_path: Path,
    clock: list[int],
    license_keys: tuple[Ed25519PrivateKey, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from octop.infra.licensing import service

    center, public_path = license_keys
    real_now = clock[0]
    state_dir = tmp_path / "state"
    clock[0] += 10000
    original = LicenseManager(state_dir, public_path)
    await original.import_offline(signed(original, center))
    clock[0] = real_now
    manager = LicenseManager(state_dir, public_path, "https://license.example.test")
    token = signed(
        manager,
        center,
        purpose="online-license",
        version=2,
        issued=real_now,
        expires=real_now + 3600,
        valid_until=real_now + 3600,
    )

    async def request(path: str, body: dict[str, Any]) -> dict[str, Any]:
        return (
            {"challenge": "fresh-challenge"} if path.endswith("/challenges") else {"token": token}
        )

    started = threading.Event()
    release = threading.Event()
    original_write = service._atomic_write

    def blocked_write(path: Path, data: bytes) -> None:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test checkpoint release was not received")
        original_write(path, data)

    monkeypatch.setattr(manager, "_request", request)
    monkeypatch.setattr(service, "_atomic_write", blocked_write)
    task = asyncio.create_task(manager.refresh())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert manager.snapshot()["reason"] == "time_anomaly"
        release.set()
        result = await task
        assert result["business_allowed"] is True
        assert result["credential_type"] == "online"
        assert result["license_version"] == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await manager.stop()
        await original.stop()
