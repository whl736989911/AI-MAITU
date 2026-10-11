"""Public deployment bundles must not confuse transport trust with issuer identity."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID
from scripts.deployment.bundle import install_bundle, key_fingerprints, load_bundle, validate_bundle
from scripts.deployment.common import DeploymentError

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="OpenSSL is required by the deployment importer"
)


def _bundle() -> tuple[dict, ed25519.Ed25519PrivateKey]:
    key = ed25519.Ed25519PrivateKey.generate()
    return {
        "format": "maitu-license-connection",
        "version": 1,
        "server_url": "https://192.0.2.10/",
        "trusted_keys": {
            "issuer-a": key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        },
        "ca_certificate_pem": None,
    }, key


def _certificate(key: ed25519.Ed25519PrivateKey, *, ca: bool) -> str:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Isolated deployment test CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_import_keeps_issuer_key_identity_and_installs_only_public_material(tmp_path: Path) -> None:
    data, key = _bundle()
    data["ca_certificate_pem"] = _certificate(key, ca=True)
    incoming = tmp_path / "connection.json"
    incoming.write_text(json.dumps(data), encoding="utf-8")
    validated = load_bundle(incoming)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert key_fingerprints(validated) == {"issuer-a": hashlib.sha256(der).hexdigest()}
    assert validated["server_url"] == "https://192.0.2.10"
    trust_path, ca_path = install_bundle(validated, tmp_path / "trust")
    assert json.loads(trust_path.read_text(encoding="utf-8")) == data["trusted_keys"]
    assert ca_path is not None
    assert ca_path.read_text(encoding="utf-8") == data["ca_certificate_pem"]
    assert "PRIVATE KEY" not in trust_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10",
        "https://owner:password@example.invalid",
        "https://example.invalid/api/v1",
        "https://example.invalid?token=x",
    ],
)
def test_bundle_rejects_unsafe_or_non_root_center_address(url: str) -> None:
    data, _ = _bundle()
    data["server_url"] = url
    with pytest.raises(DeploymentError):
        validate_bundle(data)


def test_private_or_wrong_algorithm_keys_cannot_replace_issuer_trust() -> None:
    data, key = _bundle()
    data["trusted_keys"]["issuer-a"] = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with pytest.raises(DeploymentError):
        validate_bundle(data)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    data["trusted_keys"]["issuer-a"] = (
        wrong_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    with pytest.raises(DeploymentError):
        validate_bundle(data)


def test_server_certificate_is_not_accepted_as_a_ca_root() -> None:
    data, key = _bundle()
    data["ca_certificate_pem"] = _certificate(key, ca=False)
    with pytest.raises(DeploymentError):
        validate_bundle(data)


def test_ambiguous_duplicate_fields_are_rejected_before_install(tmp_path: Path) -> None:
    incoming = tmp_path / "duplicate.json"
    incoming.write_text(
        '{"format":"maitu-license-connection","version":1,"version":2}', encoding="utf-8"
    )
    with pytest.raises(DeploymentError, match="重复"):
        load_bundle(incoming)


def test_new_bundle_without_custom_ca_does_not_delete_operator_ca(tmp_path: Path) -> None:
    data, key = _bundle()
    directory = tmp_path / "trust"
    data["ca_certificate_pem"] = _certificate(key, ca=True)
    _, previous_ca = install_bundle(data, directory)
    assert previous_ca is not None
    previous = previous_ca.read_bytes()
    data["ca_certificate_pem"] = None
    _, effective_ca = install_bundle(data, directory)
    assert effective_ca is None
    assert previous_ca.read_bytes() == previous
