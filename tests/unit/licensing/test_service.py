from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import LicenseService, current_license, set_current_license


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


_CHALLENGE_ONE = _b64(b"1" * 32)
_CHALLENGE_TWO = _b64(b"2" * 32)
_CHALLENGE_THREE = _b64(b"3" * 32)


def _signed(private: Ed25519PrivateKey, claims: dict[str, object]) -> str:
    header = _b64(
        json.dumps(
            {"typ": "maitu-license", "alg": "EdDSA", "kid": "test"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{_b64(private.sign(signing_input))}"


def _configured_service(tmp_path):
    signer = Ed25519PrivateKey.generate()
    public = (
        signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    service = LicenseService(tmp_path, trusted_keys={"test": public})
    identity = Ed25519PrivateKey.generate()
    service._private = identity
    service._instance_id = "instance-test"
    service._fingerprint = hashlib.sha256(
        identity.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()
    return service, signer


def _claims(service, *, challenge=_CHALLENGE_ONE, status="active", revision=1):
    now = int(time.time())
    return {
        "protocol_version": 1,
        "product": "maitu",
        "iss": "maitu-license-center",
        "aud": service._instance_id,
        "instance_id": service._instance_id,
        "instance_key_fingerprint": service._fingerprint,
        "challenge": challenge,
        "license_id": "license-one",
        "license_revision": revision,
        "status": status,
        "iat": now,
        "exp": now + (86400 if status == "active" else 300),
        "license_expires_at": now + 172800,
    }


@pytest.mark.asyncio
async def test_unconfigured_startup_preserves_identity_and_signed_recovery(tmp_path):
    service, signer = _configured_service(tmp_path)
    token = _signed(signer, _claims(service, revision=7))
    service._accept_token(token, expected_challenge=_CHALLENGE_ONE)
    await service._save_accepted()
    original = (tmp_path / "license" / "state.json").read_bytes()

    locked = LicenseService(tmp_path, server_url="", trusted_keys={}, ca_file="")
    status = locked.load_cached()
    assert not status["allowed"]
    assert status["reason"] == "configuration_required"
    assert locked.details()["instance_id"] == service.details()["instance_id"]
    assert locked.details()["license_revision"] == 7
    assert (tmp_path / "license" / "state.json").read_bytes() == original

    restored = LicenseService(
        tmp_path,
        trusted_keys={
            "test": signer.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        },
    )
    assert restored.load_cached()["allowed"]
    assert restored.details()["license_revision"] == 7


def test_signed_license_is_verified_and_replay_rejected(tmp_path):
    service, signer = _configured_service(tmp_path)
    token = _signed(signer, _claims(service))
    assert service._accept_token(token, expected_challenge=_CHALLENGE_ONE)["status"] == "active"
    assert service.allowed
    with pytest.raises(ValueError, match="replay"):
        service._accept_token(token, expected_challenge=_CHALLENGE_ONE)


def test_wrong_signer_and_revision_rollback_are_rejected(tmp_path):
    service, signer = _configured_service(tmp_path)
    claims = _claims(service)
    service._accept_token(_signed(signer, claims), expected_challenge=_CHALLENGE_ONE)
    with pytest.raises(ValueError):
        service._accept_token(
            _signed(Ed25519PrivateKey.generate(), _claims(service, challenge=_CHALLENGE_TWO)),
            expected_challenge=_CHALLENGE_TWO,
        )
    claims = _claims(service, challenge=_CHALLENGE_THREE, revision=0)
    with pytest.raises(ValueError, match="rollback"):
        service._accept_token(_signed(signer, claims), expected_challenge=_CHALLENGE_THREE)


def test_deny_response_locks_and_is_sticky(tmp_path):
    service, signer = _configured_service(tmp_path)
    service._accept_token(
        _signed(signer, _claims(service, status="revoked")), expected_challenge=_CHALLENGE_ONE
    )
    assert not service.allowed
    assert service.status()["reason"] == "revoked"
    with pytest.raises(ValueError, match="entitlement"):
        service._accept_token(
            _signed(signer, _claims(service, challenge=_CHALLENGE_TWO)),
            expected_challenge=_CHALLENGE_TWO,
        )
    with pytest.raises(OctopError) as denied:
        service.require_business_access()
    assert denied.value.code == ErrorCode.INSTANCE_LICENSE_LOCKED


def test_same_revision_can_transition_from_active_to_expired(tmp_path, monkeypatch):
    service, signer = _configured_service(tmp_path)
    now = int(time.time())
    active = _claims(service)
    active["license_expires_at"] = now + 5
    active["exp"] = now + 5
    service._accept_token(_signed(signer, active), expected_challenge=_CHALLENGE_ONE)

    monkeypatch.setattr(service, "_now", lambda: now + 5)
    expired = _claims(service, challenge=_CHALLENGE_TWO, status="expired")
    expired["iat"] = now + 5
    expired["exp"] = now + 305
    expired["license_expires_at"] = now + 5
    assert (
        service._accept_token(_signed(signer, expired), expected_challenge=_CHALLENGE_TWO)["status"]
        == "expired"
    )
    assert service.allowed is False


def test_deny_fence_survives_expired_cached_envelope(tmp_path, monkeypatch):
    original, signer = _configured_service(tmp_path)
    deny = _claims(original, status="revoked")
    original._accept_token(_signed(signer, deny), expected_challenge=_CHALLENGE_ONE)
    asyncio.run(original._persist())

    pem = (
        signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    restarted = LicenseService(tmp_path, trusted_keys={"test": pem})
    monkeypatch.setattr(restarted, "_now", lambda: deny["exp"])
    assert restarted.load_cached()["allowed"] is False
    assert restarted._revision == 1
    with pytest.raises(ValueError, match="entitlement"):
        restarted._accept_token(
            _signed(signer, _claims(restarted, challenge=_CHALLENGE_TWO)),
            expected_challenge=_CHALLENGE_TWO,
        )


def test_cached_license_survives_restart_and_expires_offline(tmp_path, monkeypatch):
    original, signer = _configured_service(tmp_path)
    token = _signed(signer, _claims(original))
    original._accept_token(token, expected_challenge=_CHALLENGE_ONE)
    asyncio.run(original._persist())

    pem = (
        signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    restarted = LicenseService(tmp_path, trusted_keys={"test": pem})
    cached = restarted.load_cached()
    assert cached["state"] == "CACHED_ACTIVE"
    assert cached["allowed"] is True

    monkeypatch.setattr(restarted, "_now", lambda: restarted._valid_until)
    assert restarted.allowed is False
    assert restarted.status()["reason"] == "cache_expired"


def test_server_url_rejects_non_loopback_http(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        LicenseService(tmp_path, server_url="http://license.example")
    assert (
        LicenseService(tmp_path, server_url="http://127.0.0.1:8080").server_url
        == "http://127.0.0.1:8080"
    )


def test_current_license_always_has_a_locked_service(tmp_path):
    previous = current_license()
    try:
        service = LicenseService(tmp_path)
        set_current_license(service)
        assert current_license() is service
        assert service.restricted
        assert not service.allowed
    finally:
        set_current_license(previous)


@pytest.mark.asyncio
async def test_nested_business_scope_remains_cancelable_after_inner_scope_exits(tmp_path):
    service, signer = _configured_service(tmp_path)
    service._accept_token(_signed(signer, _claims(service)), expected_challenge=_CHALLENGE_ONE)
    outer_running = asyncio.Event()

    async def business():
        async with service.business_task():
            async with service.business_task():
                await asyncio.sleep(0)
            outer_running.set()
            await asyncio.Event().wait()

    running = asyncio.create_task(business())
    await asyncio.wait_for(outer_running.wait(), timeout=2)
    service._accept_token(
        _signed(
            signer,
            _claims(service, status="revoked", revision=2, challenge=_CHALLENGE_TWO),
        ),
        expected_challenge=_CHALLENGE_TWO,
    )
    await service._notify()
    with pytest.raises(asyncio.CancelledError):
        await running


def test_signed_server_time_prevents_slow_host_clock_extending_deadline(tmp_path, monkeypatch):
    service, signer = _configured_service(tmp_path)
    claims = _claims(service)
    server_now = claims["iat"]
    claims["license_expires_at"] = claims["exp"] = server_now + 10
    monkeypatch.setattr(time, "time", lambda: server_now - 120)
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    service._accept_token(_signed(signer, claims), expected_challenge=_CHALLENGE_ONE, elapsed=0.25)
    assert service.allowed
    monkeypatch.setattr(time, "monotonic", lambda: 1008.0)
    assert not service.allowed
    assert service.status()["reason"] == "expired"
