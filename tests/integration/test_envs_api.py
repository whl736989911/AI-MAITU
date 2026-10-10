"""Deployment lockdown regressions for host environment configuration."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_environment_routes_are_denied_without_process_or_file_mutation(env: Any) -> None:
    client, server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="env-member")
    env_file = server.paths.root / "env"
    process_key = "OCTOP_TEST_RESTRICTED_ENV_WRITE"
    previous_value = os.environ.get(process_key)
    previous_file = env_file.read_bytes() if env_file.exists() else None
    try:
        for auth in (admin_auth, member_auth):
            for method, path, payload in (
                ("GET", "/api/envs", None),
                ("PUT", "/api/envs", {process_key: "must-not-apply"}),
                ("DELETE", f"/api/envs/{process_key}", None),
            ):
                response = await client.request(method, path, headers=auth, json=payload)
                assert response.status_code == 403, response.text
                assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"
                assert os.environ.get(process_key) == previous_value
                assert (env_file.read_bytes() if env_file.exists() else None) == previous_file
    finally:
        if previous_value is not None:
            os.environ[process_key] = previous_value
