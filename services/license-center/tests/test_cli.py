from __future__ import annotations

import pyotp
from typer.testing import CliRunner

from license_center import cli, crypto, storage


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
    assert "TOTP verification failed" in failed.output
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
            "SELECT kid,private_path,state FROM signing_keys ORDER BY kid"
        ).fetchall()
    active_keys = [row for row in keys if row["state"] == "active"]
    assert [(row["username"], row["active"]) for row in owners] == [("owner", 1)]
    assert len(active_keys) == 1
    assert {row["kid"]: row["state"] for row in keys} == {
        "existing-key": "retired",
        active_keys[0]["kid"]: "active",
    }
    assert (tmp_path / "keys" / "encryption.key").exists()
    private_key = tmp_path / "keys" / active_keys[0]["private_path"]
    assert private_key.exists()
    assert existing_private_path.exists()
    assert len(list((tmp_path / "keys").glob("*.ed25519.fernet"))) == 2
