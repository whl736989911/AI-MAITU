from __future__ import annotations

import sqlite3

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from license_center import api, cli, crypto, storage


def test_init_invalid_totp_can_be_retried_without_orphan_key(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr(cli, "new_totp_secret", lambda: secret)
    verify_totp = cli.valid_totp
    monkeypatch.setattr(
        cli,
        "valid_totp",
        lambda value, code: (False, -1) if code == "000000" else verify_totp(value, code),
    )
    storage.migrate()
    existing_private_path, existing_public_pem = crypto.generate_signing_key("existing-key")
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'retired',?)",
            ("existing-key", existing_private_path.name, existing_public_pem, storage.now()),
        )
    runner = CliRunner()
    password = "correct horse battery staple"

    failed = runner.invoke(
        cli.app,
        ["init"],
        input=f"owner\n{password}\n{password}\n000000\n",
    )

    assert failed.exit_code != 0
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0] == 0
        keys_after_failure = conn.execute(
            "SELECT kid,private_path,state FROM signing_keys"
        ).fetchall()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='admin.bootstrap_failed'"
            ).fetchone()[0]
            == 1
        )
    assert [(row["kid"], row["state"]) for row in keys_after_failure] == [
        ("existing-key", "retired")
    ]
    assert existing_private_path.exists()
    assert list((tmp_path / "keys").glob("*.ed25519.fernet")) == [existing_private_path]

    valid_code = pyotp.TOTP(secret).now()
    succeeded = runner.invoke(
        cli.app,
        ["init"],
        input=f"owner\n{password}\n{password}\n{valid_code}\n",
    )

    assert succeeded.exit_code == 0, succeeded.output
    with storage.connect() as conn:
        owners = conn.execute(
            "SELECT username,active FROM admin_users WHERE role='owner'"
        ).fetchall()
        keys = conn.execute(
            "SELECT kid,private_path,public_pem,state FROM signing_keys ORDER BY kid"
        ).fetchall()
    active_keys = [row for row in keys if row["state"] == "active"]
    assert [(row["username"], row["active"]) for row in owners] == [("owner", 1)]
    assert len(active_keys) == 1
    assert [(row["kid"], row["state"]) for row in keys if row["kid"] == "existing-key"] == [
        ("existing-key", "retired")
    ]
    assert active_keys[0]["kid"] != "existing-key"
    assert (tmp_path / "keys" / "encryption.key").exists()
    token = crypto.sign_license({"purpose": "first-active-signer"})
    assert (
        jwt.decode(token, active_keys[0]["public_pem"], algorithms=["EdDSA"])["purpose"]
        == "first-active-signer"
    )
    assert existing_private_path.exists()
    assert len(list((tmp_path / "keys").glob("*.ed25519.fernet"))) == 2


def test_init_after_legacy_import_preserves_active_signer_and_login(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(cli, "new_totp_secret", lambda: "JBSWY3DPEHPK3PXP")

    signer = Ed25519PrivateKey.generate()
    signer_pem = signer.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (tmp_path / "release-signing-key.pem").write_bytes(signer_pem)
    legacy = sqlite3.connect(tmp_path / "licensing.sqlite3")
    legacy.executescript(
        """
        CREATE TABLE licenses (
            id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, license_expires_at INTEGER NOT NULL,
            version INTEGER NOT NULL, status TEXT NOT NULL, restored INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, instance_id TEXT,
            public_key_pem TEXT, key_fingerprint TEXT
        );
        CREATE TABLE activation_codes (
            digest TEXT PRIMARY KEY, license_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
            instance_id TEXT, used_at INTEGER
        );
        CREATE TABLE license_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, license_id TEXT, details TEXT NOT NULL
        );
        """
    )
    legacy.commit()
    legacy.close()

    secret = "JBSWY3DPEHPK3PXP"
    password = "correct horse battery staple"
    current = storage.now()
    monkeypatch.setattr(crypto, "now", lambda: current)
    result = CliRunner().invoke(
        cli.app,
        ["init"],
        input=f"owner\n{password}\n{password}\n{pyotp.TOTP(secret).at(current)}\n",
    )

    assert result.exit_code == 0, result.output
    with storage.connect() as conn:
        keys = conn.execute(
            "SELECT kid,private_path,state FROM signing_keys ORDER BY kid"
        ).fetchall()
    active_keys = [row for row in keys if row["state"] == "active"]
    assert [(row["kid"], row["state"]) for row in keys] == [("maitu-license-1", "active")]
    assert len(active_keys) == 1
    old_key_path = tmp_path / "keys" / active_keys[0]["private_path"]
    assert old_key_path.is_file()
    token = crypto.sign_license({"purpose": "post-init", "aud": "instance-test"})
    claims = jwt.decode(
        token,
        serialization.load_pem_public_key(
            signer.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ),
        algorithms=["EdDSA"],
        audience="instance-test",
        options={"verify_exp": False},
    )
    assert claims["purpose"] == "post-init"

    with TestClient(api.app, base_url="https://127.0.0.1") as client:
        login = client.post(
            "/api/v1/admin/login",
            json={
                "username": "owner",
                "password": password,
                "totp": pyotp.TOTP(secret).at((current // 30 + 1) * 30),
            },
        )
    assert login.status_code == 200, login.text


@pytest.mark.parametrize("failure", ["invalid_code", "aborted_prompt"])
def test_recovery_failure_preserves_account_and_session(center, monkeypatch, failure):
    client, _ = center
    current = storage.now()
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr(crypto, "now", lambda: current)
    monkeypatch.setattr(cli, "new_totp_secret", lambda: secret)
    valid_codes = {pyotp.TOTP(secret).at(current + offset * 30) for offset in (-1, 0, 1)}
    invalid_code = next(
        str(value).zfill(6) for value in range(100) if str(value).zfill(6) not in valid_codes
    )
    with storage.connect() as conn:
        before = dict(
            conn.execute("SELECT * FROM admin_users WHERE username='test-owner'").fetchone()
        )
    result = CliRunner().invoke(
        cli.app,
        ["recover", "test-owner", "--confirm", "--password", "replacement test password"],
        input=invalid_code + "\n" if failure == "invalid_code" else "",
    )
    assert result.exit_code != 0
    with storage.connect() as conn:
        after = dict(
            conn.execute("SELECT * FROM admin_users WHERE username='test-owner'").fetchone()
        )
        assert after == before
        assert (
            conn.execute(
                "SELECT count(*) FROM admin_users WHERE role='owner' AND active=1"
            ).fetchone()[0]
            == 1
        )
    assert client.get("/api/v1/admin/session").status_code == 200


def test_recovery_changes_credentials_and_invalidates_previous_sessions(center, monkeypatch):
    client, _ = center
    current = storage.now()
    secret = "JBSWY3DPEHPK3PXP"
    password = "replacement test password"
    monkeypatch.setattr(crypto, "now", lambda: current)
    monkeypatch.setattr(cli, "new_totp_secret", lambda: secret)
    result = CliRunner().invoke(
        cli.app,
        ["recover", "test-owner", "--confirm", "--password", password],
        input=pyotp.TOTP(secret).at(current) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert client.get("/api/v1/admin/session").status_code == 401
    next_code = pyotp.TOTP(secret).at((current // 30 + 1) * 30)
    old_login = client.post(
        "/api/v1/admin/login",
        json={
            "username": "test-owner",
            "password": "correct horse battery staple",
            "totp": next_code,
        },
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/v1/admin/login",
        json={"username": "test-owner", "password": password, "totp": next_code},
    )
    assert new_login.status_code == 200, new_login.text
