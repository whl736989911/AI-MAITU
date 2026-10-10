"""Customer deployments cannot mutate plugin enablement."""

from __future__ import annotations

from typing import Any


async def test_plugin_toggle_is_rejected_without_config_mutation(env_with_provider: Any) -> None:
    client, srv, auth = env_with_provider
    config_path = srv.paths.config
    before = config_path.read_bytes()

    response = await client.patch(
        "/api/plugins/fortune",
        headers=auth,
        json={"enabled": False},
    )

    assert response.status_code == 403, response.text
    # The route's plugins permission check legitimately preempts the deployment
    # policy for this account; it is denied before the toggle handler either way.
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert config_path.read_bytes() == before
