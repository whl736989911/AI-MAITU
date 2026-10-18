"""Bundled plugin installation follows account permissions and stays idempotent."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def test_bundled_market_install_is_enabled_and_idempotent(env: Any) -> None:
    client, srv, auth = env
    response = await client.post("/api/plugins/market/fortune/install", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["installed"] is True
    assert response.json()["enabled"] is True
    config_after_install = srv.paths.config.read_bytes()

    repeated = await client.post("/api/plugins/market/fortune/install", headers=auth)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["installed"] is True
    assert repeated.json()["enabled"] is True
    assert srv.paths.config.read_bytes() == config_after_install


async def test_market_install_without_permission_cannot_mutate_config(env: Any) -> None:
    client, srv, admin_auth = env
    auth = await create_user(client, admin_auth, username="plugin_unprivileged", permissions=[])
    before = srv.paths.config.read_bytes()
    response = await client.post("/api/plugins/market/fortune/install", headers=auth)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert srv.paths.config.read_bytes() == before
