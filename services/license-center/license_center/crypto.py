from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from pathlib import Path

import jwt
import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .storage import DATA_DIR, now

KEY_DIR = (
    Path(os.environ.get("LICENSE_CENTER_KEY_DIR", str(DATA_DIR / "keys"))).expanduser().resolve()
)
ENC_KEY = (
    Path(os.environ.get("LICENSE_CENTER_ENCRYPTION_KEY", str(KEY_DIR / "encryption.key")))
    .expanduser()
    .resolve()
)


def _private_mode(path: Path) -> None:
    with suppress(OSError):
        path.chmod(0o600)


def encryption_box() -> Fernet:
    if not ENC_KEY.exists():
        ENC_KEY.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        with ENC_KEY.open("xb") as f:
            f.write(key)
        _private_mode(ENC_KEY)
    return Fernet(ENC_KEY.read_bytes().strip())


def encrypt_secret(secret: str) -> bytes:
    return encryption_box().encrypt(secret.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    return encryption_box().decrypt(ciphertext).decode("utf-8")


def fingerprint(public_key_pem: str) -> str:
    key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Ed25519 public key required")
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def generate_signing_key(kid: str) -> tuple[Path, str]:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    path = KEY_DIR / f"{kid}.ed25519.fernet"
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    with path.open("xb") as f:
        f.write(encryption_box().encrypt(private_pem))
    _private_mode(path)
    return path, public_pem


def sign_license(claims: dict[str, object]) -> str:
    from .storage import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT kid,private_path FROM signing_keys WHERE state='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError(
            "No active signing key; initialize a key before serving protocol requests"
        )
    filename = f"{row['kid']}.ed25519.fernet"
    if row["private_path"] != filename:
        raise ValueError(f"Invalid private key path metadata for signing key {row['kid']}")
    private_pem = encryption_box().decrypt((KEY_DIR / filename).read_bytes())
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return jwt.encode(
        claims, private_key, algorithm="EdDSA", headers={"typ": "JWT", "kid": row["kid"]}
    )


def verify_proof(token: str, expected: dict[str, object], public_pem: str) -> dict[str, object]:
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "EdDSA" or header.get("typ") != "maitu-instance-proof":
        raise ValueError("Invalid proof header")
    public_key = serialization.load_pem_public_key(public_pem.encode())
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("Ed25519 public key required")
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["EdDSA"],
        options={"verify_aud": False, "verify_exp": False, "verify_iat": False},
    )
    for name, value in expected.items():
        if claims.get(name) != value:
            raise ValueError(f"Proof claim mismatch: {name}")
    allowed_claims = {
        "protocol_version",
        "product",
        "aud",
        "instance_id",
        "public_key",
        "operation",
        "challenge",
    }
    if claims.get("operation") == "activate":
        allowed_claims.add("activation_code")
    if set(claims) != allowed_claims:
        raise ValueError("Unexpected proof claims")
    if (
        type(claims.get("protocol_version")) is not int
        or claims["protocol_version"] != 1
        or claims.get("product") != "maitu"
        or claims.get("aud") != "maitu-license-center"
    ):
        raise ValueError("Unsupported proof protocol")
    return claims


def new_totp_secret() -> str:
    return pyotp.random_base32()


def valid_totp(secret: str, code: str, last_counter: int = -1) -> tuple[bool, int]:
    totp = pyotp.TOTP(secret)
    counter = int(now() // totp.interval)
    for candidate in (counter - 1, counter, counter + 1):
        if candidate > last_counter and totp.verify(code, for_time=candidate * totp.interval):
            return True, candidate
    return False, counter


def _store_release_key(kid: str, key: Ed25519PrivateKey) -> tuple[Path, str]:
    if not kid or len(kid) > 128 or Path(kid).name != kid or kid in {".", ".."}:
        raise ValueError("Invalid release signing key ID")
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    path = KEY_DIR / f"{kid}.ed25519.fernet"
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    if path.exists():
        if encryption_box().decrypt(path.read_bytes()) != private_pem:
            raise ValueError("Signing key ID already belongs to a different private key")
        return path, public_pem
    with path.open("xb") as stream:
        stream.write(encryption_box().encrypt(private_pem))
    _private_mode(path)
    return path, public_pem


def generate_release_signing_key(kid: str) -> tuple[Path, str]:
    return _store_release_key(kid, Ed25519PrivateKey.generate())


def import_release_signing_key(kid: str, private_pem: bytes) -> tuple[Path, str]:
    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Legacy release key is not Ed25519")
    return _store_release_key(kid, key)


def import_license_signing_key(kid: str, private_pem: bytes) -> tuple[Path, str]:
    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Legacy license key is not Ed25519")
    return _store_release_key(kid, key)


def sign_release(claims: dict[str, object]) -> str:
    from .storage import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT kid,private_path FROM release_signing_keys "
            "WHERE state='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("No active release signing key is initialized")
    filename = f"{row['kid']}.ed25519.fernet"
    if row["private_path"] != filename:
        raise ValueError(f"Invalid release key path metadata for signing key {row['kid']}")
    private_pem = encryption_box().decrypt((KEY_DIR / filename).read_bytes())
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers={"typ": "JWT", "kid": row["kid"]},
    )
