"""Unit tests: /api/filesystem host browsing is admin-only.

The router browses the *host* filesystem (root_dir pickers), so every route in
it must be behind ``require_admin`` — a regular member must never be able to
list or probe host paths. Workspace-scoped browsing for members lives in
``routers/workspace.py`` instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from tests.support.app import octop_client
from tests.support.auth import TEST_PASSWORD, auth_header, bootstrap_admin, create_user

# (method, path, json body) for every route in ``routers/filesystem.py``.
HOST_FS_ROUTES: list[tuple[str, str, dict[str, str] | None]] = [
    ("GET", "/api/filesystem/defaults", None),
    ("GET", "/api/filesystem/dirs?path=/", None),
    ("POST", "/api/filesystem/probe", {"path": "/"}),
    ("POST", "/api/filesystem/ensure-bwrap", None),
    ("GET", "/api/filesystem/docker-status", None),
    ("POST", "/api/filesystem/ensure-docker", None),
    ("POST", "/api/filesystem/mkdir", {"path": "/", "base_name": "New Folder"}),
    ("POST", "/api/filesystem/rename", {"path": "/", "new_name": "workspace"}),
]


@pytest.fixture
async def fs_env(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str], dict[str, str]]]:
    """``(client, admin auth, regular-member auth)`` on a bootstrapped server."""
    async with octop_client(tmp_path) as (client, _srv):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        member_auth = await create_user(
            client, admin_auth, username="mallory", password=TEST_PASSWORD
        )
        yield client, admin_auth, member_auth


@pytest.mark.parametrize(("method", "path", "payload"), HOST_FS_ROUTES)
async def test_member_is_forbidden(
    fs_env: tuple[httpx.AsyncClient, dict[str, str], dict[str, str]],
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    client, _admin_auth, member_auth = fs_env
    r = await client.request(method, path, headers=member_auth, json=payload)
    assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_every_route_in_the_router_is_admin_gated() -> None:
    """Guard the invariant, not just today's routes: this router browses hosts.

    A future route added here without ``require_admin`` would otherwise ship
    silently — that is exactly how the original hole went unnoticed.
    """
    from octop.api.deps import require_admin
    from octop.api.routers import filesystem

    gate = require_admin()
    assert filesystem.router.routes, "filesystem router has no routes"
    for route in filesystem.router.routes:
        calls = [dep.call for dep in route.dependant.dependencies]
        assert any(getattr(call, "__code__", None) is gate.__code__ for call in calls), (
            f"{route.path} is not behind require_admin"
        )


async def test_admin_can_list_host_dirs(
    fs_env: tuple[httpx.AsyncClient, dict[str, str], dict[str, str]],
    tmp_path: Path,
) -> None:
    """Pair for the 403 above: the gate must not break the admin browse path."""
    client, admin_auth, _member_auth = fs_env
    (tmp_path / "alpha").mkdir()

    r = await client.get(
        f"/api/filesystem/dirs?path={tmp_path.as_posix()}",
        headers=admin_auth,
    )
    assert r.status_code == 200, r.text
    names = [entry["name"] for entry in r.json()["entries"]]
    assert "alpha" in names
