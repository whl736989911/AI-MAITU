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
