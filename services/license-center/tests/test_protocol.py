from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_center import api, crypto, storage
from license_center.models import ActivationCreate, ChallengeRequest, LicenseAction, ProofRequest


def test_activation_race_binds_only_one_instance(center):
    activation, signing_pem = center
    attempts = []
    for instance_id in ("race-first", "race-second"):
        private_key = Ed25519PrivateKey.generate()
        public_pem = (
            private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        challenge = api._create_challenge(
            ChallengeRequest(
                instance_id=instance_id, operation="activate", public_key_pem=public_pem
            ),
            "127.0.0.1",
        )
        attempts.append(
            proof_request(
                private_key,
                challenge["challenge"],
                instance_id,
                public_pem,
                "activate",
                activation["code"],
            )
        )

    async def race():
        return await asyncio.gather(
            *(api._db(api._activate, body) for body in attempts), return_exceptions=True
        )

    results = asyncio.run(race())
    issued = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, api.HTTPException)]
    assert len(issued) == len(rejected) == 1
    assert rejected[0].status_code == 409
    assert rejected[0].detail["error"]["code"] == "activation_used"
    accepted = jwt.decode(
        issued[0]["token"],
        serialization.load_pem_public_key(signing_pem.encode()),
        algorithms=["EdDSA"],
        audience="maitu-instance",
        issuer="maitu-license-center",
    )
    with storage.connect() as conn:
        bound = conn.execute(
            "SELECT bound_instance_id FROM activation_codes WHERE id=?", (activation["id"],)
        ).fetchone()
        assert bound["bound_instance_id"] == accepted["instance_id"]
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM instances WHERE id IN ('race-first','race-second')"
            ).fetchone()[0]
            == 1
        )


@pytest.fixture()
def center(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.sqlite3")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(crypto, "ENC_KEY", tmp_path / "keys" / "encryption.key")
    storage.migrate()
    kid = "test-key"
    private_path, public_pem = crypto.generate_signing_key(kid)
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',?)",
            (kid, private_path.name, public_pem, storage.now()),
        )
        conn.execute(
            "INSERT INTO customers(id,name,created_at) VALUES('cus_test','Customer',?)",
            (storage.now(),),
        )
    activation = api._create_activation(
        ActivationCreate(customer_id="cus_test", expires_at=storage.now() + 86400 * 30), "test"
    )
    yield activation, public_pem


def test_signing_key_survives_relocated_backup(center, tmp_path, monkeypatch):
    _, public_pem = center
    original_key_dir = crypto.KEY_DIR
    original_private_path = original_key_dir / "test-key.ed25519.fernet"
    with storage.transaction() as conn:
        conn.execute(
            "UPDATE signing_keys SET private_path=? WHERE kid='test-key'",
            (str(original_private_path),),
        )

    restored_dir = tmp_path / "restored"
    restored_keys = restored_dir / "keys"
    restored_dir.mkdir()
    shutil.copytree(original_key_dir, restored_keys)
    restored_db = restored_dir / "center.sqlite3"
    source = sqlite3.connect(storage.DB_PATH)
    destination = sqlite3.connect(restored_db)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()

    original_key_dir.rename(tmp_path / "unavailable-original-keys")
    monkeypatch.setattr(storage, "DB_PATH", restored_db)
    monkeypatch.setattr(crypto, "KEY_DIR", restored_keys)
    monkeypatch.setattr(crypto, "ENC_KEY", restored_keys / "encryption.key")
    storage.migrate()

    with storage.connect() as conn:
        row = conn.execute("SELECT private_path FROM signing_keys WHERE kid='test-key'").fetchone()
    assert row["private_path"] == "test-key.ed25519.fernet"
    token = crypto.sign_license({"instance_id": "relocated-instance", "status": "active"})
    claims = jwt.decode(
        token,
        serialization.load_pem_public_key(public_pem.encode()),
        algorithms=["EdDSA"],
    )
    assert claims["instance_id"] == "relocated-instance"


def test_migration_rejects_traversal_private_key_metadata(center):
    with storage.transaction() as conn:
        conn.execute(
            "UPDATE signing_keys SET private_path='../test-key.ed25519.fernet' WHERE kid='test-key'"
        )
    with pytest.raises(ValueError, match="Invalid private key path metadata"):
        storage.migrate()


def proof_for(private_key, challenge, instance_id, public_pem, operation, activation_code=None):
    claims = {
        "protocol": 1,
        "purpose": "instance-proof",
        "iss": instance_id,
        "aud": "maitu-license-center",
        "instance_id": instance_id,
        "product": "maitu",
        "public_key": public_pem,
        "operation": operation,
        "challenge": challenge,
        "content_hash": hashlib.sha256(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "challenge": challenge,
                    "public_key_pem": public_pem,
                    **({"activation_code": activation_code} if activation_code else {}),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    }
    if activation_code:
        claims["activation_code"] = activation_code
    return jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers={"kid": "maitu-license-1", "typ": "JWT"},
    )


def proof_request(private_key, challenge, instance_id, public_pem, operation, activation_code=None):
    return ProofRequest(
        instance_id=instance_id,
        challenge=challenge,
        public_key_pem=public_pem,
        proof=proof_for(
            private_key, challenge, instance_id, public_pem, operation, activation_code
        ),
        activation_code=activation_code,
    )


def activate(center):
    activation, signing_pem = center
    private_key = Ed25519PrivateKey.generate()
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    instance_id = "instance-test"
    challenge = api._create_challenge(
        ChallengeRequest(instance_id=instance_id, operation="activate", public_key_pem=public_pem),
        "127.0.0.1",
    )
    proof = proof_for(
        private_key, challenge["challenge"], instance_id, public_pem, "activate", activation["code"]
    )
    response = api._activate(
        proof_request(
            private_key,
            challenge["challenge"],
            instance_id,
            public_pem,
            "activate",
            activation["code"],
        )
    )
    claims = jwt.decode(
        response["token"],
        serialization.load_pem_public_key(signing_pem.encode()),
        algorithms=["EdDSA"],
        audience="maitu-instance",
        issuer="maitu-license-center",
    )
    assert claims["status"] == "active"
    assert claims["license_version"] == 1
    assert claims["key_fingerprint"] == crypto.fingerprint(public_pem)
    return private_key, public_pem, instance_id, challenge, proof, claims


def test_resource_key_upgrade_preserves_bound_instance_and_revocation(
    center, tmp_path, monkeypatch
):
    """A v1 center upgrade cannot unbind a customer or erase a revocation."""
    _, signing_pem = center
    with storage.connect() as conn:
        key = dict(conn.execute("SELECT * FROM signing_keys WHERE state='active'").fetchone())
    private_key = Ed25519PrivateKey.generate()
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    instance_id = "legacy-instance"
    license_id = "legacy-license"
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "legacy-center.sqlite3")
    with storage.connect() as conn:
        conn.executescript((storage.MIGRATIONS / "001_initial.sql").read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations(version,applied_at) VALUES(1,?)", (storage.now(),)
        )
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,?,?)",
            (key["kid"], key["private_path"], key["public_pem"], key["state"], key["created_at"]),
        )
        conn.execute(
            "INSERT INTO customers(id,name,created_at) VALUES('cus_test','Customer',?)",
            (storage.now(),),
        )
        conn.execute(
            "INSERT INTO instances(id,customer_id,public_key,fingerprint) VALUES(?,?,?,?)",
            (instance_id, "cus_test", public_pem, crypto.fingerprint(public_pem)),
        )
        conn.execute(
            "INSERT INTO licenses(id,instance_id,expires_at,revoked,revision,created_at) "
            "VALUES(?,?,?,1,2,?)",
            (license_id, instance_id, storage.now() + 86400, storage.now()),
        )
    storage.migrate()
    storage.migrate()
    with storage.connect() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        binding = conn.execute(
            "SELECT customer_id FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        assert binding["customer_id"] == "cus_test"
    challenge = api._create_challenge(
        ChallengeRequest(instance_id=instance_id, operation="sync"),
        "127.0.0.1",
    )
    response = api._sync(
        proof_request(private_key, challenge["challenge"], instance_id, public_pem, "sync")
    )
    denied = jwt.decode(
        response["token"],
        serialization.load_pem_public_key(signing_pem.encode()),
        algorithms=["EdDSA"],
        audience="maitu-instance",
        issuer="maitu-license-center",
    )
    assert denied["status"] == "revoked"
    assert denied["license_version"] == 2
    assert denied["license_id"] == license_id


def test_activation_is_signed_and_challenge_is_one_time(center):
    private_key, public_pem, instance_id, challenge, _, claims = activate(center)
    with pytest.raises(api.HTTPException) as rejected:
        api._activate(
            proof_request(private_key, challenge["challenge"], instance_id, public_pem, "activate")
        )
    assert rejected.value.status_code == 409
    assert rejected.value.detail["error"]["code"] == "challenge_invalid"
    assert claims["valid_until"] <= claims["license_expires_at"]


def test_sync_revocation_restore_and_revision_fence(center):
    private_key, public_pem, instance_id, _, _, claims = activate(center)
    license_id = claims["license_id"]
    revoked = api._license_action(
        license_id, "revoke", LicenseAction(reason="contract test", version=1), "test"
    )
    assert revoked["revoked"] == 1 and revoked["revision"] == 2

    challenge = api._create_challenge(
        ChallengeRequest(instance_id=instance_id, operation="sync"),
        "127.0.0.1",
    )
    deny = api._sync(
        proof_request(private_key, challenge["challenge"], instance_id, public_pem, "sync")
    )["token"]
    with storage.connect() as conn:
        signing_pem = conn.execute(
            "SELECT public_pem FROM signing_keys WHERE state='active'"
        ).fetchone()[0]
    denied_claims = jwt.decode(
        deny,
        serialization.load_pem_public_key(signing_pem.encode()),
        algorithms=["EdDSA"],
        audience="maitu-instance",
        issuer="maitu-license-center",
    )
    assert denied_claims["status"] == "revoked"
    assert denied_claims["valid_until"] <= denied_claims["issued_at"] + 300

    restored = api._license_action(
        license_id, "restore", LicenseAction(reason="approved", version=2), "test"
    )
    assert restored["revoked"] == 0 and restored["revision"] == 3
    with pytest.raises(api.HTTPException) as rejected:
        api._license_action(license_id, "revoke", LicenseAction(reason="stale", version=1), "test")
    assert rejected.value.status_code == 409
    assert rejected.value.detail["error"]["code"] == "version_conflict"


def test_activation_retry_same_instance_is_idempotent(center):
    activation, _ = center
    private_key = Ed25519PrivateKey.generate()
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    instance_id = "retry-instance"
    first_challenge = api._create_challenge(
        ChallengeRequest(instance_id=instance_id, operation="activate", public_key_pem=public_pem),
        "127.0.0.1",
    )
    first = api._activate(
        proof_request(
            private_key,
            first_challenge["challenge"],
            instance_id,
            public_pem,
            "activate",
            activation["code"],
        )
    )
    second_challenge = api._create_challenge(
        ChallengeRequest(instance_id=instance_id, operation="activate", public_key_pem=public_pem),
        "127.0.0.1",
    )
    second = api._activate(
        proof_request(
            private_key,
            second_challenge["challenge"],
            instance_id,
            public_pem,
            "activate",
            activation["code"],
        )
    )
    with storage.connect() as conn:
        count = conn.execute(
            "SELECT count(*) FROM licenses WHERE instance_id=?", (instance_id,)
        ).fetchone()[0]
    assert count == 1
    assert (
        jwt.decode(first["token"], options={"verify_signature": False})["license_id"]
        == jwt.decode(second["token"], options={"verify_signature": False})["license_id"]
    )


def test_expiry_creates_one_denial_revision(center):
    private_key, public_pem, instance_id, _, _, claims = activate(center)
    with storage.transaction() as conn:
        conn.execute(
            "UPDATE licenses SET expires_at=? WHERE id=?", (storage.now() - 1, claims["license_id"])
        )

    for expected_revision in (2, 2):
        challenge = api._create_challenge(
            ChallengeRequest(instance_id=instance_id, operation="sync"),
            "127.0.0.1",
        )
        denied = api._sync(
            proof_request(private_key, challenge["challenge"], instance_id, public_pem, "sync")
        )["token"]
        with storage.connect() as conn:
            public_pem_signer = conn.execute(
                "SELECT public_pem FROM signing_keys WHERE state='active'"
            ).fetchone()[0]
            license_row = conn.execute(
                "SELECT revision,expired FROM licenses WHERE id=?", (claims["license_id"],)
            ).fetchone()
        denied_claims = jwt.decode(
            denied,
            serialization.load_pem_public_key(public_pem_signer.encode()),
            algorithms=["EdDSA"],
            audience="maitu-instance",
            issuer="maitu-license-center",
        )
        assert denied_claims["status"] == "expired"
        assert denied_claims["license_version"] == expected_revision
        assert license_row["expired"] == 1


def test_http_management_keeps_public_contract_with_integer_resource_keys(center):
    from fastapi.testclient import TestClient

    _, _, instance_id, _, _, claims = activate(center)
    secret = pyotp.random_base32()
    password = "correct horse battery staple"
    with storage.transaction() as conn:
        conn.execute(
            "INSERT INTO admin_users(username,password_hash,role,totp_ciphertext,active,created_at) VALUES(?,?,?,?,1,?)",
            (
                "http-owner",
                api.PASSWORDS.hash(password),
                "owner",
                crypto.encrypt_secret(secret),
                storage.now(),
            ),
        )
    with TestClient(api.app, base_url="https://127.0.0.1") as client:
        login = client.post(
            "/api/v1/admin/login",
            json={"username": "http-owner", "password": password, "totp": pyotp.TOTP(secret).now()},
        )
        assert login.status_code == 200, login.text
        customers = client.get("/api/v1/admin/customers")
        assert customers.status_code == 200, customers.text
        assert customers.json()[0]["id"] == "cus_test"
        extended = client.post(
            f"/api/v1/admin/licenses/{claims['license_id']}/extend",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
            json={
                "reason": "Approved extension",
                "version": 1,
                "expires_at": claims["license_expires_at"] + 86400,
            },
        )
        assert extended.status_code == 200, extended.text
        assert extended.json()["id"] == claims["license_id"]
        assert extended.json()["instance_id"] == instance_id
        assert extended.json()["revision"] == 2
        assert extended.json()["expires_at"] == claims["license_expires_at"] + 86400
        assert "pk" not in extended.json()


def test_admin_session_uses_user_id_and_rejects_totp_replay(center):
    secret = pyotp.random_base32()
    password = "correct horse battery staple"
    with storage.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO admin_users(username,password_hash,role,totp_ciphertext,active,created_at) VALUES(?,?,?,?,1,?)",
            (
                "owner",
                api.PASSWORDS.hash(password),
                "owner",
                crypto.encrypt_secret(secret),
                storage.now(),
            ),
        )
        user_id = cursor.lastrowid

    code = pyotp.TOTP(secret).now()
    token, csrf, user = api._login("owner", password, code, "127.0.0.2")
    session_user = api._get_user(token)
    assert user["id"] == user_id
    assert session_user["id"] == user_id
    assert session_user["user_id"] == user_id
    assert crypto.decrypt_secret(session_user["csrf_ciphertext"]) == csrf
    api._csrf_check(session_user, csrf)
    with pytest.raises(api.HTTPException) as rejected:
        api._csrf_check(session_user, "wrong-token")
    assert rejected.value.status_code == 403
    assert rejected.value.detail["error"]["code"] == "csrf_failed"
    with pytest.raises(api.HTTPException) as replayed:
        api._login("owner", password, code, "127.0.0.2")
    assert replayed.value.status_code == 401


@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1", "[::1]"])
def test_remote_peer_cannot_spoof_loopback_https_exception(center, hostname):
    from fastapi.testclient import TestClient

    with TestClient(
        api.app,
        base_url=f"http://{hostname}",
        client=("198.51.100.25", 51000),
    ) as client:
        response = client.get("/health")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "https_required"
