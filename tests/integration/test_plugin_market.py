"""Customer deployments cannot mutate plugin catalog state."""

from __future__ import annotations

from typing import Any


async def test_market_install_is_rejected_without_config_mutation(env: Any) -> None:
    client, srv, auth = env
    config_path = srv.paths.config
    before = config_path.read_bytes()

    response = await client.post("/api/plugins/market/fortune/install", headers=auth)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"
    assert config_path.read_bytes() == before
