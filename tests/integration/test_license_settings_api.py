"""License center trust settings API (setup + application settings)."""

from __future__ import annotations

import asyncio
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
from tests.support.auth import create_user, wizard_token


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


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["trusted_keys_json", "ca_pem", "center_url"])
async def test_existing_private_settings_are_not_exposed_to_enterprise_admin(
    env, field: str
) -> None:
    client, srv, auth = env
    enterprise = await create_user(
        client, auth, username="enterprise-viewer", role="enterprise_admin", permissions=[]
    )
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    data = json.loads(srv.paths.config.read_text(encoding="utf-8"))
    section = {"center_url": "https://license.example.test"}
    if field == "center_url":
        secret = "public-view-must-not-expose-password"
        section["center_url"] = f"https://owner:{secret}@license.example.test"
    else:
        secret = private_pem.splitlines()[1]
        material = srv.paths.root / "legacy-public-trust.txt"
        if field == "trusted_keys_json":
            material.write_text(json.dumps({"legacy": public_pem + private_pem}), encoding="utf-8")
            section["trusted_keys_file"] = str(material)
        else:
            material.write_text(private_pem, encoding="utf-8")
            section["ca_file"] = str(material)
    data["license"] = section
    srv.paths.config.write_text(json.dumps(data), encoding="utf-8")
    response = await client.get("/api/license/settings", headers=enterprise)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SLASH_BAD_ARGS"
    assert secret not in response.text


@pytest.mark.asyncio
async def test_saving_trust_does_not_block_public_license_status(env, monkeypatch) -> None:
    import threading

    from octop.infra.licensing import settings

    client, _srv, auth = env
    entered = threading.Event()
    release = threading.Event()
    stalled = threading.Event()
    write = settings._atomic_write

    def slow_write(path: Path, text: str) -> None:
        entered.set()
        if not release.wait(timeout=5):
            stalled.set()
        write(path, text)

    monkeypatch.setattr(settings, "_atomic_write", slow_write)
    saving = asyncio.create_task(
        client.put(
            "/api/license/settings",
            headers=auth,
            json={"center_url": "", "trusted_keys_json": _keyring()},
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        status = await client.get("/api/license/status")
        assert status.status_code == 200
        assert not stalled.is_set()
    finally:
        release.set()
        result = await saving
    assert result.status_code == 200, result.text


@pytest.mark.asyncio
async def test_clearing_override_does_not_disable_deployment_license(env, monkeypatch) -> None:
    client, srv, auth = env
    public_keys = _keyring()
    deployment_keys = srv.paths.root / "deployment-keys.json"
    deployment_keys.write_text(public_keys, encoding="utf-8")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(deployment_keys))
    response = await client.put(
        "/api/license/settings",
        headers=auth,
        json={"center_url": "", "trusted_keys_json": ""},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trusted_keys_configured"] is True
    assert current_license().managed is True
    assert current_license().snapshot()["business_allowed"] is False
    assert deployment_keys.read_text(encoding="utf-8") == public_keys
