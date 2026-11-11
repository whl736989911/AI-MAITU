from __future__ import annotations

import jwt
import pytest
from cryptography.fernet import Fernet
from helpers import active_license_public_pem

from license_center import crypto, storage


@pytest.mark.parametrize("fault", ["missing", "wrong"])
def test_incomplete_key_restore_is_unhealthy_until_original_key_returns(center, fault):
    client, _ = center
    public_pem = active_license_public_pem(client)
    original_key = crypto.ENC_KEY.read_bytes()
    with storage.connect() as conn:
        before = [dict(row) for row in conn.execute("SELECT * FROM admin_users")]
    if fault == "missing":
        crypto.ENC_KEY.unlink()
    else:
        crypto.ENC_KEY.write_bytes(Fernet.generate_key())
    broken_key = crypto.ENC_KEY.read_bytes() if crypto.ENC_KEY.exists() else None

    assert client.get("/health").status_code == 503
    with pytest.raises(RuntimeError):
        storage.migrate()
    with storage.connect() as conn:
        assert [dict(row) for row in conn.execute("SELECT * FROM admin_users")] == before
    assert (crypto.ENC_KEY.read_bytes() if crypto.ENC_KEY.exists() else None) == broken_key

    crypto.ENC_KEY.write_bytes(original_key)
    storage.migrate()
    assert client.get("/health").status_code == 200
    token = crypto.sign_license({"value": "original signing identity"})
    assert (
        jwt.decode(token, public_pem, algorithms=["EdDSA"])["value"] == "original signing identity"
    )
    assert crypto.decrypt_secret(before[0]["totp_ciphertext"]) == client.app.state.test_totp_secret


@pytest.mark.parametrize("operation", ["encrypt", "decrypt", "license-key", "release-key"])
def test_missing_master_key_cannot_be_silently_recreated(center, operation):
    with storage.connect() as conn:
        ciphertext = conn.execute("SELECT totp_ciphertext FROM admin_users").fetchone()[0]
    crypto.ENC_KEY.unlink()
    with pytest.raises(RuntimeError):
        if operation == "encrypt":
            crypto.encrypt_secret("replacement-factor")
        elif operation == "decrypt":
            crypto.decrypt_secret(ciphertext)
        elif operation == "license-key":
            crypto.generate_signing_key("must-not-create")
        else:
            crypto.generate_release_signing_key("must-not-create")
    assert not crypto.ENC_KEY.exists()
    assert not (crypto.KEY_DIR / "must-not-create.ed25519.fernet").exists()
