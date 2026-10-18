"""Deployment lockdown regression for host filesystem mutation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_host_directory_creation_is_denied_for_admin_and_member(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="filesystem-member")
    target = tmp_path / "must-not-be-created"

    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    with patch("octop.api.routers.filesystem.mkdir_host_subdir") as mkdir:
        for auth in (admin_auth, member_auth):
            response = await client.post(
                "/api/filesystem/mkdir",
                headers=auth,
                json={"path": str(tmp_path), "base_name": target.name},
            )
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == (
                "INSTANCE_DEPLOYMENT_RESTRICTED" if auth == admin_auth else "FORBIDDEN"
            )
            assert not target.exists()

    mkdir.assert_not_called()


@pytest.mark.asyncio
async def test_native_host_mkdir_requires_admin_and_returns_created_directory(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="native-fs-member")
    monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    parent = tmp_path / "picker"
    parent.mkdir()
    payload = {"path": str(parent), "base_name": "child"}

    denied = await client.post("/api/filesystem/mkdir", headers=member_auth, json=payload)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert not (parent / "child").exists()

    created = await client.post("/api/filesystem/mkdir", headers=admin_auth, json=payload)
    assert created.status_code == 200, created.text
    assert Path(created.json()["path"]) == (parent / "child").resolve()
    assert created.json()["name"] == "child"
    assert (parent / "child").is_dir()
