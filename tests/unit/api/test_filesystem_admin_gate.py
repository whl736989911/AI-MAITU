"""Host filesystem API is denied before privileged operations for every role."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from tests.support.app import octop_client
from tests.support.auth import TEST_PASSWORD, auth_header, bootstrap_admin, create_user

HOST_FS_ROUTES: tuple[tuple[str, str, dict[str, str] | None], ...] = (
    ("GET", "/api/filesystem/defaults", None),
    ("GET", "/api/filesystem/dirs?path=/", None),
    ("POST", "/api/filesystem/probe", {"path": "/"}),
    ("POST", "/api/filesystem/ensure-bwrap", None),
    ("GET", "/api/filesystem/docker-status", None),
    ("POST", "/api/filesystem/ensure-docker", None),
    ("POST", "/api/filesystem/mkdir", {"path": "/", "base_name": "forbidden"}),
    ("POST", "/api/filesystem/rename", {"path": "/", "new_name": "forbidden"}),
)


@pytest.mark.asyncio
async def test_all_host_filesystem_routes_are_deployment_restricted_before_handlers(
    tmp_path: Path,
) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        member_auth = await create_user(
            client, admin_auth, username="fs-member", password=TEST_PASSWORD
        )
        from octop.api.routers import filesystem

        helper_names = (
            "assert_safe_host_path",
            "default_host_root_dir",
            "host_fs_tree_root",
            "host_home_dir",
            "host_path_text",
            "list_host_subdirs",
            "mkdir_host_subdir",
            "probe_host_root_dir",
            "rename_host_dir",
            "running_in_container",
            "ensure_bubblewrap",
            "docker_status",
            "ensure_docker",
        )
        sentinels = {
            name: Mock(side_effect=AssertionError(f"{name} was called")) for name in helper_names
        }
        with patch.multiple(filesystem, **sentinels):
            for auth in (admin_auth, member_auth):
                for method, path, payload in HOST_FS_ROUTES:
                    response = await client.request(method, path, headers=auth, json=payload)
                    assert response.status_code == 403, f"{method} {path}: {response.text}"
                    assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"

        for sentinel in sentinels.values():
            sentinel.assert_not_called()
