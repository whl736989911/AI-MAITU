from __future__ import annotations

import time

import pyotp

from license_center import storage


def customer_and_activation(client, csrf, expires_at=None):
    customer_response = client.post(
        "/api/v1/admin/customers",
        headers=csrf,
        json={"name": "Protocol customer", "contact": "", "notes": ""},
    )
    assert customer_response.status_code == 201, customer_response.text
    customer = customer_response.json()
    activation_response = client.post(
        "/api/v1/admin/activation-codes",
        headers=csrf,
        json={
            "customer_id": customer["id"],
            "expires_at": expires_at or storage.now() + 30 * 86400,
        },
    )
    assert activation_response.status_code == 201, activation_response.text
    return customer, activation_response.json()


def active_license_public_pem(client):
    response = client.get("/api/v1/admin/keys")
    assert response.status_code == 200, response.text
    return next(key["public_pem"] for key in response.json() if key["state"] == "active")


def reauthenticate(client, csrf):
    totp = pyotp.TOTP(client.app.state.test_totp_secret)
    next_counter = int(time.time() // totp.interval) + 1
    response = client.post(
        "/api/v1/admin/reauth",
        headers=csrf,
        json={"totp": totp.at(next_counter * totp.interval)},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}
