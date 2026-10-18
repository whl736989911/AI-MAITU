from __future__ import annotations

import hashlib
import sqlite3

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_center import crypto, storage


def _private_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_legacy_license_ledger_import_preserves_bindings_revisions_and_signer(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")

    old_key = Ed25519PrivateKey.generate()
    old_public_pem = (
        old_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    (tmp_path / "release-signing-key.pem").write_bytes(_private_pem(old_key))
    legacy_db = sqlite3.connect(tmp_path / "licensing.sqlite3")
    legacy_db.executescript(
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
    now = 1_800_000_000
    bound_id = "a" * 32
    bound_key = Ed25519PrivateKey.generate()
    bound_public_pem = (
        bound_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    bound_fingerprint = crypto.fingerprint(bound_public_pem)
    bound_instance = bound_fingerprint[:32]
    revoked_code = "previously-used-activation-code"
    unbound_code = "unused-activation-code"
    legacy_db.execute(
        "INSERT INTO licenses VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            bound_id,
            "Bound customer",
            now + 86400 * 30,
            7,
            "revoked",
            1,
            now - 100,
            now - 10,
            bound_instance,
            bound_public_pem,
            bound_fingerprint,
        ),
    )
    legacy_db.execute(
        "INSERT INTO licenses VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "b" * 32,
            "Unbound customer",
            now + 86400 * 60,
            3,
            "active",
            0,
            now - 200,
            now - 50,
            None,
            None,
            None,
        ),
    )
    legacy_db.execute(
        "INSERT INTO activation_codes VALUES(?,?,?,?,?)",
        (
            hashlib.sha256(revoked_code.encode()).hexdigest(),
            bound_id,
            now + 3600,
            bound_instance,
            now - 20,
        ),
    )
    legacy_db.execute(
        "INSERT INTO activation_codes VALUES(?,?,?,?,?)",
        (hashlib.sha256(unbound_code.encode()).hexdigest(), "b" * 32, now + 7200, None, None),
    )
    legacy_db.execute(
        "INSERT INTO license_audit(at,actor,action,license_id,details) VALUES(?,?,?,?,?)",
        (now - 30, "legacy-admin", "revoke", bound_id, '{"version":7}'),
    )
    legacy_db.commit()
    legacy_db.close()

    storage.migrate()
    with storage.connect() as conn:
        license_row = conn.execute("SELECT * FROM licenses WHERE id=?", (bound_id,)).fetchone()
        assert license_row["revision"] == 7
        assert license_row["revoked"] == 1 and license_row["restored"] == 1
        assert license_row["expires_at"] == now + 86400 * 30
        instance = conn.execute("SELECT * FROM instances WHERE id=?", (bound_instance,)).fetchone()
        assert instance["public_key"] == bound_public_pem
        assert instance["fingerprint"] == bound_fingerprint
        imported = conn.execute("SELECT * FROM signing_keys WHERE kid='maitu-license-1'").fetchone()
        assert imported["state"] == "active"
        assert imported["public_pem"] == old_public_pem
        revoked = conn.execute(
            "SELECT * FROM activation_codes WHERE digest=?",
            (hashlib.sha256(revoked_code.encode()).hexdigest(),),
        ).fetchone()
        assert revoked["used_at"] == now - 20
        assert revoked["bound_instance_id"] == bound_instance
        assert revoked["revoked_at"] == now - 10
        unused = conn.execute(
            "SELECT * FROM activation_codes WHERE digest=?",
            (hashlib.sha256(unbound_code.encode()).hexdigest(),),
        ).fetchone()
        assert unused["used_at"] is None and unused["revoked_at"] is None
        assert unused["expires_at"] == now + 7200
        unbound_customer = conn.execute(
            "SELECT name,notes FROM customers WHERE id=?", (unused["customer_id"],)
        ).fetchone()
        assert unbound_customer["name"] == "Unbound customer"
        assert '"revision": 3' in unbound_customer["notes"]
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE actor='legacy:legacy-admin'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM center_imports WHERE source='legacy-licensing-center-v1'"
            ).fetchone()[0]
            == 1
        )

    signed = crypto.sign_license({"purpose": "migration-check", "aud": "instance-test"})
    claims = jwt.decode(
        signed,
        serialization.load_pem_public_key(old_public_pem.encode()),
        algorithms=["EdDSA"],
        audience="instance-test",
        options={"verify_exp": False},
    )
    assert claims["purpose"] == "migration-check"
    storage.migrate()
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM licenses").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM activation_codes").fetchone()[0] == 2
    assert (tmp_path / "licensing.sqlite3").is_file()
    assert (tmp_path / "release-signing-key.pem").is_file()
