"""Managed-license fixtures: a real Ed25519 signer standing in for the license center.

Mints protocol-conformant JWTs (same claims/header rules the client core
verifies) so gate tests exercise the real ``LicenseManager`` — no fake
interface, no wiring mocks.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing import LicenseManager

_ISSUER = "maitu-license-center"
_AUDIENCE = "maitu-instance"
_KID = "maitu-license-1"
_DAY = 86400


class CenterSigner:
    """Test-double of the license center signing side (real Ed25519, real JWTs)."""

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()
        self.license_seq = 0

    def write_trusted_keys(self, path: Path, kid: str = _KID) -> Path:
        """Write the operator-style kid→PEM trust map the license client consumes."""
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        path.write_text(json.dumps({kid: pem.decode("ascii")}), encoding="utf-8")
        return path

    def mint(
        self,
        *,
        purpose: str,
        instance_id: str,
        key_fingerprint: str,
        license_id: str | None = None,
        license_version: int = 1,
        status: str = "active",
        restored: bool = False,
        issued_at: int | None = None,
        not_before: int | None = None,
        license_expires_at: int,
        valid_until: int,
        challenge: str | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "protocol": 1,
            "product": "maitu",
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "purpose": purpose,
            "instance_id": instance_id,
            "key_fingerprint": key_fingerprint,
            "license_id": license_id or uuid.uuid4().hex,
            "license_version": int(license_version),
            "status": status,
            "restored": bool(restored),
            "issued_at": int(now if issued_at is None else issued_at),
            "not_before": int(now if not_before is None else not_before),
            "license_expires_at": int(license_expires_at),
            "valid_until": int(valid_until),
        }
        if challenge is not None:
            claims["challenge"] = challenge
        return jwt.encode(claims, self._key, algorithm="EdDSA", headers={"kid": _KID})

    def offline(
        self,
        instance_id: str,
        key_fingerprint: str,
        *,
        days: float = 30,
        **kwargs: Any,
    ) -> str:
        now = int(time.time())
        expires = now + int(days * _DAY)
        return self.mint(
            purpose="offline-license",
            instance_id=instance_id,
            key_fingerprint=key_fingerprint,
            license_expires_at=kwargs.pop("license_expires_at", expires),
            valid_until=kwargs.pop("valid_until", expires),
            **kwargs,
        )

    def online(
        self,
        instance_id: str,
        key_fingerprint: str,
        *,
        days: float = 30,
        **kwargs: Any,
    ) -> str:
        now = int(time.time())
        expires = now + int(days * _DAY)
        return self.mint(
            purpose="online-license",
            instance_id=instance_id,
            key_fingerprint=key_fingerprint,
            challenge=kwargs.pop("challenge", uuid.uuid4().hex),
            license_expires_at=kwargs.pop("license_expires_at", expires),
            valid_until=kwargs.pop("valid_until", now + 3600),
            **kwargs,
        )


def provision_license(home: Path, *, status: str = "active") -> LicenseManager:
    """Create a real managed manager with a signed offline credential installed."""
    signer = CenterSigner()
    trusted_keys = signer.write_trusted_keys(home / "trust" / "trusted-keys.json")
    manager = LicenseManager(home / "license-state", trusted_keys)
    now = int(time.time())
    token = signer.offline(
        manager.instance_id,
        manager._identity,
        status=status,
        issued_at=now,
        not_before=now,
        license_expires_at=now + 30 * _DAY,
        valid_until=now + 30 * _DAY,
    )
    asyncio.run(manager.import_offline(token))
    return manager


@pytest.fixture
def license_center(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CenterSigner:
    """Configure a managed install and reset the licensing singleton around the test."""
    signer = CenterSigner()
    key_file = signer.write_trusted_keys(tmp_path / "trust" / "license-trusted-keys.json")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(key_file))
    monkeypatch.setenv("MAITU_LICENSE_STATE_DIR", str(tmp_path / "license-state"))
    monkeypatch.delenv("MAITU_LICENSE_CENTER_URL", raising=False)
    monkeypatch.delenv("MAITU_LICENSE_ALLOW_LOOPBACK", raising=False)

    from octop.infra import licensing

    previous = licensing.current_license()
    try:
        yield signer
    finally:
        licensing.install_license_manager(previous)


async def export_request(client: Any, auth: dict[str, str]) -> dict[str, Any]:
    """Fetch the instance offline-request export (admin, allowed while locked)."""
    response = await client.get("/api/license/offline-request", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()
