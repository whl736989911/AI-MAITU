from __future__ import annotations

import pyotp
import pytest

from license_center import crypto, storage


def _pending_admin(client, csrf):
    response = client.post(
        "/api/v1/admin/users",
        headers=csrf,
        json={
            "username": "factor-target",
            "password": "a secure test password",
            "role": "operator",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _invalid_code(secret, current):
    allowed = {pyotp.TOTP(secret).at(current + offset * 30) for offset in (-1, 0, 1)}
    return next(str(value).zfill(6) for value in range(100) if str(value).zfill(6) not in allowed)


@pytest.mark.parametrize("operation", ["reauth", "enroll", "enable"])
def test_factor_checks_limit_attempts_without_changing_account(center, monkeypatch, operation):
    client, csrf = center
    current = storage.now()
    monkeypatch.setattr(crypto, "now", lambda: current)
    if operation == "reauth":
        secret = client.app.state.test_totp_secret
        with storage.transaction() as conn:
            user_id = conn.execute(
                "SELECT id FROM admin_users WHERE username='test-owner'"
            ).fetchone()[0]
            conn.execute("UPDATE admin_sessions SET recent_mfa_at=0")
        path = "/api/v1/admin/reauth"
    else:
        target = _pending_admin(client, csrf)
        secret, user_id = target["totp_secret"], target["id"]
        path = f"/api/v1/admin/users/{user_id}" + ("/enroll" if operation == "enroll" else "")
    with storage.connect() as conn:
        before = dict(
            conn.execute(
                "SELECT active,last_totp_counter FROM admin_users WHERE id=?", (user_id,)
            ).fetchone()
        )
    payload = {"totp": _invalid_code(secret, current)}
    if operation == "enable":
        payload.update(role="operator", active=True)
    call = client.put if operation == "enable" else client.post
    for _ in range(8):
        response = call(path, headers=csrf, json=payload)
        assert response.status_code == 401, response.text
    response = call(path, headers=csrf, json=payload)
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "rate_limited"
    with storage.connect() as conn:
        after = dict(
            conn.execute(
                "SELECT active,last_totp_counter FROM admin_users WHERE id=?", (user_id,)
            ).fetchone()
        )
        assert after == before
        if operation == "reauth":
            assert conn.execute("SELECT recent_mfa_at FROM admin_sessions").fetchone()[0] == 0


def test_initial_enrollment_requires_recent_owner_mfa(center, monkeypatch):
    client, csrf = center
    target = _pending_admin(client, csrf)
    current = storage.now()
    monkeypatch.setattr(crypto, "now", lambda: current)
    with storage.transaction() as conn:
        conn.execute("UPDATE admin_sessions SET recent_mfa_at=0")
    response = client.post(
        f"/api/v1/admin/users/{target['id']}/enroll",
        headers=csrf,
        json={"totp": pyotp.TOTP(target["totp_secret"]).at(current)},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "reauth_required"
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT active,last_totp_counter FROM admin_users WHERE id=?", (target["id"],)
        ).fetchone()
    assert not row["active"]
    assert row["last_totp_counter"] == -1


def test_disabled_enrolled_account_cannot_use_initial_enrollment_again(center, monkeypatch):
    client, csrf = center
    target = _pending_admin(client, csrf)
    current = storage.now()
    monkeypatch.setattr(crypto, "now", lambda: current)
    enrolled = client.post(
        f"/api/v1/admin/users/{target['id']}/enroll",
        headers=csrf,
        json={"totp": pyotp.TOTP(target["totp_secret"]).at(current)},
    )
    assert enrolled.status_code == 200, enrolled.text
    disabled = client.put(
        f"/api/v1/admin/users/{target['id']}",
        headers=csrf,
        json={"role": "operator", "active": False},
    )
    assert disabled.status_code == 200, disabled.text
    response = client.post(
        f"/api/v1/admin/users/{target['id']}/enroll",
        headers=csrf,
        json={"totp": pyotp.TOTP(target["totp_secret"]).at((current // 30 + 1) * 30)},
    )
    assert response.status_code == 409, response.text
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT active,last_totp_counter FROM admin_users WHERE id=?", (target["id"],)
        ).fetchone()
    assert not row["active"]
    assert row["last_totp_counter"] == current // 30
