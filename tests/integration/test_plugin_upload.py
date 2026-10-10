"""Customer deployments cannot upload arbitrary plugin code."""

from __future__ import annotations

from typing import Any


async def test_upload_is_rejected_without_plugin_or_config_side_effects(env: Any) -> None:
    client, srv, auth = env
    config_path = srv.paths.config
    plugins_dir = srv.plugin_manager.plugins_dir
    config_before = config_path.read_bytes()
    plugins_before = {path.name for path in plugins_dir.iterdir()}

    response = await client.post(
        "/api/plugins/upload",
        files={"file": ("custom.zip", b"not inspected", "application/zip")},
        headers=auth,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"
    assert config_path.read_bytes() == config_before
    assert {path.name for path in plugins_dir.iterdir()} == plugins_before
