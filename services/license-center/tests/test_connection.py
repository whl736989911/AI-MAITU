from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from typer.testing import CliRunner

from license_center import cli, connection, crypto, releases, storage


def _ca_pem() -> str:
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, algorithm=None)
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _public_pem() -> str:
    key = Ed25519PrivateKey.generate()
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://center.example",
        "https://user:secret@center.example",
        "https://center.example/path",
        "https://center.example?x=1",
        "https://center.example#fragment",
        "https://center.example?",
        "https://center.example#",
        "https:///missing-host",
        "https://center.example:",
        "https://bad host",
    ],
)
def test_invalid_server_urls_are_rejected(url):
    with pytest.raises(ValueError):
        connection.validate_server_url(url)


def test_bundle_validates_public_key_and_ca_types():
    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = (
        rsa_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    with pytest.raises(ValueError):
        connection.create_bundle(
            "https://center.example", {"rsa": rsa_pem}, {"release": _public_pem()}
        )
    with pytest.raises(ValueError):
        connection.create_bundle(
            "https://center.example",
            {"license": _public_pem()},
            {"release": _public_pem()},
            "not a certificate",
        )
    bundle = connection.create_bundle(
        "https://center.example/",
        {"license": _public_pem()},
        {"release": _public_pem()},
        _ca_pem(),
    )
    assert bundle["server_url"] == "https://center.example"
    assert bundle["ca_certificate_pem"].startswith("-----BEGIN CERTIFICATE-----")

    ca = _ca_pem()
    oversized_ca = ca * (connection.MAX_BUNDLE_BYTES // len(ca) + 1)
    with pytest.raises(ValueError):
        connection.encode_bundle(
            connection.create_bundle(
                "https://center.example",
                {"license": _public_pem()},
                {"release": _public_pem()},
                oversized_ca,
            )
        )
    with pytest.raises(ValueError):
        connection.create_bundle(
            "https://center.example",
            {
                "private": Ed25519PrivateKey.generate()
                .private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                .decode("ascii")
            },
            {"release": _public_pem()},
        )
    assert bundle["format"] == "maitu-license-connection"
    assert bundle["version"] == 2
    assert bundle["license_trusted_keys"]["license"].startswith("-----BEGIN PUBLIC KEY-----")
    assert bundle["release_trusted_keys"]["release"].startswith("-----BEGIN PUBLIC KEY-----")


def test_export_connection_includes_retired_key_fingerprints_without_mutating_center(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(releases, "_LEGACY_DB", tmp_path / "legacy-releases.sqlite3")
    monkeypatch.setattr(releases, "_LEGACY_KEY", tmp_path / "legacy-release-key.pem")
    storage.migrate()

    private_path, public_pem = crypto.generate_signing_key("old")
    keys = {"old": (private_path, public_pem)}
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',?)",
            ("old", private_path.name, public_pem, 100),
        )
    with storage.transaction() as conn:
        conn.execute("INSERT INTO customers(id,name,created_at) VALUES('customer','Customer',1)")
        conn.execute(
            "INSERT INTO instances(id,customer_id,public_key,fingerprint) VALUES('instance','customer','opaque-public-key','instance-fingerprint')"
        )
        conn.execute(
            "INSERT INTO licenses(id,instance_id,expires_at,created_at) VALUES('license','instance',9999999999,1)"
        )

    runner = CliRunner()
    staged = runner.invoke(cli.app, ["stage-key"])
    assert staged.exit_code == 0, staged.output
    staged_key = json.loads(staged.output)
    rotated = runner.invoke(cli.app, ["activate-key", staged_key["kid"], "--confirm"])
    assert rotated.exit_code == 0, rotated.output
    keys[staged_key["kid"]] = (
        crypto.KEY_DIR / f"{staged_key['kid']}.ed25519.fernet",
        staged_key["public_pem"],
    )

    destination = tmp_path / "connection.json"
    result = CliRunner().invoke(
        cli.app,
        ["export-connection", str(destination), "--server-url", "https://center.example/"],
    )

    assert result.exit_code == 0, result.output
    bundle = json.loads(destination.read_text(encoding="utf-8"))
    assert set(bundle) == {
        "format",
        "version",
        "server_url",
        "license_trusted_keys",
        "release_trusted_keys",
        "ca_certificate_pem",
    }
    assert bundle["format"] == "maitu-license-connection"
    assert bundle["version"] == 2
    assert set(bundle["license_trusted_keys"]) == set(keys)
    assert set(bundle["release_trusted_keys"]) == {"maitu-release-1"}
    assert {
        kid: crypto.fingerprint(pem) for kid, pem in bundle["license_trusted_keys"].items()
    } == {kid: crypto.fingerprint(public_pem) for kid, (_, public_pem) in keys.items()}
    assert "PRIVATE KEY" not in destination.read_text(encoding="utf-8")
    assert all(path.exists() for path, _ in keys.values())
    with storage.connect() as conn:
        assert (
            conn.execute("SELECT state FROM signing_keys WHERE kid='old'").fetchone()[0]
            == "retired"
        )
        assert (
            conn.execute(
                "SELECT state FROM signing_keys WHERE kid=?", (staged_key["kid"],)
            ).fetchone()[0]
            == "active"
        )
        assert (
            conn.execute("SELECT fingerprint FROM instances WHERE id='instance'").fetchone()[0]
            == "instance-fingerprint"
        )
        assert (
            conn.execute("SELECT id FROM licenses WHERE instance_id='instance'").fetchone()[0]
            == "license"
        )


def test_export_does_not_replace_different_file_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(releases, "_LEGACY_DB", tmp_path / "legacy-releases.sqlite3")
    monkeypatch.setattr(releases, "_LEGACY_KEY", tmp_path / "legacy-release-key.pem")
    storage.migrate()
    private_path, public_pem = crypto.generate_signing_key("active")
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',1)",
            ("active", private_path.name, public_pem),
        )
    destination = tmp_path / "connection.json"
    destination.write_text("keep this\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app, ["export-connection", str(destination), "--server-url", "https://center.example"]
    )
    assert result.exit_code != 0
    assert destination.read_text(encoding="utf-8") == "keep this\n"


def test_export_cleans_temporary_file_after_interrupted_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(releases, "_LEGACY_DB", tmp_path / "legacy-releases.sqlite3")
    monkeypatch.setattr(releases, "_LEGACY_KEY", tmp_path / "legacy-release-key.pem")
    storage.migrate()
    private_path, public_pem = crypto.generate_signing_key("active")
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',1)",
            ("active", private_path.name, public_pem),
        )
    destination = tmp_path / "connection.json"
    destination.write_text("original\n", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("simulated interrupted atomic replace")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    result = CliRunner().invoke(
        cli.app,
        [
            "export-connection",
            str(destination),
            "--server-url",
            "https://center.example",
            "--confirm",
        ],
    )

    assert result.exit_code != 0
    assert destination.read_text(encoding="utf-8") == "original\n"
    assert not list(tmp_path.glob("connection.json.tmp-*"))


def test_export_without_license_signer_refuses_bundle_after_schema_initialization(
    tmp_path, monkeypatch
):
    database = tmp_path / "center.sqlite3"
    monkeypatch.setattr(storage, "DB_PATH", database)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(releases, "_LEGACY_DB", tmp_path / "legacy-releases.sqlite3")
    monkeypatch.setattr(releases, "_LEGACY_KEY", tmp_path / "legacy-release-key.pem")
    result = CliRunner().invoke(
        cli.app,
        [
            "export-connection",
            str(tmp_path / "connection.json"),
            "--server-url",
            "https://center.example",
        ],
    )
    assert result.exit_code != 0
    assert "trusted Ed25519 public keys are required" in result.output
    assert database.exists()
    assert not (tmp_path / "connection.json").exists()
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM signing_keys").fetchone()[0] == 0
