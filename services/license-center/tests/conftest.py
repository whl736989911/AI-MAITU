from __future__ import annotations

import os

import pyotp
import pytest
from fastapi.testclient import TestClient

os.environ["MAITU_CENTER_CLIENT_TOKEN"] = "client-token-that-is-long-enough-for-safe-testing-01"

from license_center import api, crypto, releases, storage


@pytest.fixture
def center(tmp_path, monkeypatch):
    database = tmp_path / "center.sqlite3"
    monkeypatch.setattr(storage, "DB_PATH", database)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    monkeypatch.setattr(releases, "_LEGACY_DB", tmp_path / "legacy-releases.sqlite3")
    monkeypatch.setattr(releases, "_LEGACY_KEY", tmp_path / "legacy-release-key.pem")
    monkeypatch.setattr(releases, "_RELEASES_DIR", tmp_path / "releases")
    storage.migrate()
    private_path, public_pem = crypto.generate_signing_key("test-license-key")
    secret = pyotp.random_base32()
    password = "correct horse battery staple"
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',?)",
            ("test-license-key", private_path.name, public_pem, storage.now()),
        )
        conn.execute(
            "INSERT INTO admin_users(username,password_hash,role,totp_ciphertext,active,created_at) VALUES(?,?,?,?,1,?)",
            (
                "test-owner",
                api.PASSWORDS.hash(password),
                "owner",
                crypto.encrypt_secret(secret),
                storage.now(),
            ),
        )
    with TestClient(api.app, base_url="https://127.0.0.1") as client:
        login = client.post(
            "/api/v1/admin/login",
            json={"username": "test-owner", "password": password, "totp": pyotp.TOTP(secret).now()},
        )
        assert login.status_code == 200, login.text
        client.app.state.test_totp_secret = secret
        yield client, {"X-CSRF-Token": login.json()["csrf_token"]}
