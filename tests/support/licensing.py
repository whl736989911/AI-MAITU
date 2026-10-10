"""Issue real test-only Ed25519 credentials; production has no authorization bypass."""

from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing import LicenseService


def public_key_pem(signer: Ed25519PrivateKey) -> str:
    return (
        signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def sign_license(signer: Ed25519PrivateKey, claims: dict[str, Any], *, kid: str = "test") -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = encode({"typ": "maitu-license", "alg": "EdDSA", "kid": kid})
    payload = encode(claims)
    signature = (
        base64.urlsafe_b64encode(signer.sign(f"{header}.{payload}".encode("ascii")))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}.{signature}"


def provision_license(
    home: Path,
    *,
    signer: Ed25519PrivateKey | None = None,
    deadline: int | None = None,
    revision: int = 1,
    status: str = "active",
    server_url: str | None = None,
) -> LicenseService:
    """Create protected identity/cache, then validate it through the real client."""
    signer = signer or Ed25519PrivateKey.generate()
    service = LicenseService(
        home, trusted_keys={"test": public_key_pem(signer)}, server_url=server_url
    )
    service.load_cached()
    service._initialize_identity_sync()
    now = int(time.time())
    deadline = deadline if deadline is not None else now + 172800
    challenge = secrets.token_urlsafe(32)
    details = service.details()
    claims = {
        "protocol_version": 1,
        "product": "maitu",
        "iss": "maitu-license-center",
        "aud": details["instance_id"],
        "instance_id": details["instance_id"],
        "instance_key_fingerprint": details["public_key_fingerprint"],
        "challenge": challenge,
        "license_id": "test-license",
        "license_revision": revision,
        "status": status,
        "iat": now,
        "exp": min(now + 86400, deadline) if status == "active" else now + 300,
        "license_expires_at": deadline,
    }
    token = sign_license(signer, claims)
    state_path = home / "license" / "state.json"
    state = json.loads(state_path.read_text("utf-8"))
    state.update(
        token=token,
        last_wall=now,
        fence={
            "revision": revision,
            "entitlement": [status, deadline, details["public_key_fingerprint"]],
            "challenges": [challenge],
        },
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    service.load_cached()
    return service
