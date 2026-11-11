"""The read-only center probe must match public deployment bundle identities."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from scripts.deployment.bundle import key_fingerprints
from scripts.deployment.center_probe import _SCRIPT


def _public_pem(key: ed25519.Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def test_probe_fingerprints_match_bundle_for_both_domains_and_all_key_states(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3] / "services" / "license-center"))
    from license_center import storage

    database = tmp_path / "center.sqlite3"
    license_keys = {
        "shared": _public_pem(ed25519.Ed25519PrivateKey.generate()),
        "license-staged": _public_pem(ed25519.Ed25519PrivateKey.generate()),
        "license-retired": _public_pem(ed25519.Ed25519PrivateKey.generate()),
    }
    release_keys = {
        "shared": _public_pem(ed25519.Ed25519PrivateKey.generate()),
        "release-staged": _public_pem(ed25519.Ed25519PrivateKey.generate()),
        "release-retired": _public_pem(ed25519.Ed25519PrivateKey.generate()),
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE admin_users(id INTEGER);"
            "CREATE TABLE signing_keys(kid TEXT, public_pem TEXT, private_path TEXT, state TEXT);"
            "CREATE TABLE release_signing_keys(kid TEXT, public_pem TEXT, private_path TEXT, state TEXT);"
        )
        connection.executemany(
            "INSERT INTO signing_keys VALUES(?,?,?,?)",
            [
                (kid, pem, f"must-not-read-license-{kid}.key", state)
                for (kid, pem), state in zip(
                    license_keys.items(), ("active", "staged", "retired"), strict=True
                )
            ],
        )
        connection.executemany(
            "INSERT INTO release_signing_keys VALUES(?,?,?,?)",
            [
                (kid, pem, f"must-not-read-release-{kid}.key", state)
                for (kid, pem), state in zip(
                    release_keys.items(), ("active", "staged", "retired"), strict=True
                )
            ],
        )

    monkeypatch.setattr(storage, "DB_PATH", database)
    certificate = tmp_path / "tls.crt"
    certificate.write_text("test certificate", encoding="utf-8")
    monkeypatch.setenv("LICENSE_CENTER_TLS_CERT", str(certificate))

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(_SCRIPT, "center_probe", "exec"), {})
    probe = json.loads(output.getvalue())
    assert "error_class" not in probe

    from license_center.connection import create_bundle

    bundle = create_bundle("https://example.test/", license_keys, release_keys)
    expected = key_fingerprints(bundle)
    assert probe["fingerprints"] == expected
    assert probe["fingerprints"]["license:shared"] != probe["fingerprints"]["release:shared"]
