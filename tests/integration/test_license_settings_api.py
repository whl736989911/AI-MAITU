"""License center trust settings API (setup + application settings)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing import (
    LicenseManager,
    current_license,
    install_license_manager,
)
from tests.support.auth import wizard_token


@pytest.fixture(autouse=True)
def _reset_license_singleton(tmp_path: Path):
    """Keep this file's deliberately managed state out of later app fixtures."""
    yield
    install_license_manager(LicenseManager(tmp_path / "reset-state", None))


def _keyring() -> str:
    key = Ed25519PrivateKey.generate().public_key()
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return json.dumps({"license-test-key": pem})


@pytest.mark.asyncio
async def test_admin_can_read_and_update_license_settings(env) -> None:
    client, _srv, auth = env
    initial = await client.get("/api/license/settings", headers=auth)
    assert initial.status_code == 200, initial.text
    assert initial.json()["source"] in {"none", "env", "files"}

    updated = await client.put(
        "/api/license/settings",
        headers=auth,
        json={
            "center_url": "https://license.example.test/",
            "trusted_keys_json": _keyring(),
            "ca_pem": None,
            "allow_loopback": False,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["center_url"] == "https://license.example.test"
    assert body["trusted_keys_configured"] is True
    assert body["source"] == "config"
    assert body["trusted_keys_json"]

    reloaded = await client.get("/api/license/settings", headers=auth)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["center_url"] == "https://license.example.test"

    cleared = await client.put(
        "/api/license/settings",
        headers=auth,
        json={
            "center_url": "https://license.example.test",
            "trusted_keys_json": "",
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["trusted_keys_configured"] is False
    assert current_license().managed is False


@pytest.mark.asyncio
async def test_license_config_error_status_fails_closed(env) -> None:
    client, srv, auth = env
    srv._license_config_error = True
    try:
        status = await client.get("/api/license/status")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["managed"] is True
        assert body["business_allowed"] is False
        assert body["state"] == "LOCKED"
        assert body["reason"] == "configuration"

        details = await client.get("/api/license/details", headers=auth)
        assert details.status_code == 200, details.text
        assert details.json()["business_allowed"] is False
    finally:
        srv._license_config_error = False


@pytest.mark.asyncio
async def test_license_settings_reject_invalid_ca(env) -> None:
    client, _srv, auth = env
    response = await client.put(
        "/api/license/settings",
        headers=auth,
        json={
            "center_url": "https://license.example.test",
            "trusted_keys_json": _keyring(),
            "ca_pem": "not a certificate",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SLASH_BAD_ARGS"


@pytest.mark.asyncio
async def test_setup_license_config_requires_wizard_token(patched_app_client) -> None:
    client, _srv, _home = patched_app_client
    response = await client.post(
        "/api/setup/license-config",
        json={"center_url": "https://license.example.test", "trusted_keys_json": _keyring()},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "SETUP_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_setup_license_config_persists_trust(patched_app_client) -> None:
    client, srv, home = patched_app_client
    token = await wizard_token(client, home)
    response = await client.post(
        "/api/setup/license-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "center_url": "https://license.example.test/",
            "trusted_keys_json": _keyring(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert srv.license_manager is not None
    assert (home / "license" / "trusted-keys.json").is_file()


@pytest.mark.asyncio
async def test_runtime_license_config_keeps_first_run_admin_creation_working(
    patched_app_client,
) -> None:
    client, srv, home = patched_app_client
    token = await wizard_token(client, home)
    headers = {"Authorization": f"Bearer {token}"}
    saved = await client.post(
        "/api/setup/license-config",
        headers=headers,
        json={
            "center_url": "https://license.example.test",
            "trusted_keys_json": _keyring(),
        },
    )
    assert saved.status_code == 200, saved.text
    created = await client.post(
        "/api/setup/initial-admin",
        headers=headers,
        json={"username": "admin", "password": "TestPass12"},
    )
    assert created.status_code == 201, created.text
    finished = await client.post(
        "/api/setup/finish",
        headers=headers,
        json={"provider_draft": None},
    )
    assert finished.status_code == 200, finished.text
    assert srv.license_manager is not None
    assert srv.license_manager.business_allowed is False
