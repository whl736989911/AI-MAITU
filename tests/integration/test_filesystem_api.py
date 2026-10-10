"""Deployment lockdown regression for host filesystem mutation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_host_directory_creation_is_denied_for_admin_and_member(
    env: Any, tmp_path: Path
) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="filesystem-member")
    target = tmp_path / "must-not-be-created"

    with patch("octop.api.routers.filesystem.mkdir_host_subdir") as mkdir:
        for auth in (admin_auth, member_auth):
            response = await client.post(
                "/api/filesystem/mkdir",
                headers=auth,
                json={"path": str(tmp_path), "base_name": target.name},
            )
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"
            assert not target.exists()

    mkdir.assert_not_called()
