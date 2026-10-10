"""Unit tests for custom MCP validation and assembly."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.connectors.builder import mcp_server_name
from octop.infra.connectors.custom_mcp import (
    CUSTOM_MCP_KIND,
    extract_servers,
    harness_spec_for_server,
    normalize_server_spec,
    validate_servers_map,
)
from octop.infra.connectors.probe import probe_custom_mcp_server
from octop.infra.connectors.service import ConnectorService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.utils.ulid import new_ulid


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def svc(db: SqlitePool) -> ConnectorService:
    return ConnectorService(
        repo=ConnectorRepo(db),
        secret_repo=SecretRepo(db),
        settings_repo=SettingsRepo(db),
        config=OctopConfig(),
    )


def _ensure_user(db: SqlitePool) -> int:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES ('u1', 'x', 'user', 1)"
        )
        row = conn.execute("SELECT id FROM users WHERE username = 'u1'").fetchone()
    assert row is not None
    return int(row["id"])


def _ensure_second_user(db: SqlitePool) -> int:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES ('u2', 'x', 'user', 1)"
        )
        row = conn.execute("SELECT id FROM users WHERE username = 'u2'").fetchone()
    assert row is not None
    return int(row["id"])


def test_normalize_streamable_http_and_rejects_stdio():
    http = normalize_server_spec(
        "deepwiki",
        {
            "transport": "streamable_http",
            "url": "https://mcp.deepwiki.com/mcp",
            "headers": {"Authorization": "Bearer t"},
        },
    )
    assert http["transport"] == "streamable_http"
    assert http["url"] == "https://mcp.deepwiki.com/mcp"
    assert http["headers"]["Authorization"] == "Bearer t"
    assert http["enabled"] is True

    with pytest.raises(ValueError, match="stdio MCP is disabled"):
        normalize_server_spec(
            "local",
            {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "enabled": False,
            },
        )


def test_shared_custom_server_uses_collision_safe_name_for_viewer(
    svc: ConnectorService,
    db: SqlitePool,
) -> None:
    owner_id = _ensure_user(db)
    viewer_id = _ensure_second_user(db)
    svc.put_custom_servers(
        owner_id,
        {
            "linear": {
                "transport": "streamable_http",
                "url": "https://mcp.linear.app/mcp",
                "shared": True,
            },
            "private": {
                "transport": "streamable_http",
                "url": "https://private.example.com/mcp",
            },
        },
    )

    visible = svc.list_instances_for_api(viewer_id)
    assert [row["display_name"] for row in visible] == ["linear"]
    shared_name = str(visible[0]["mcp_server_name"])
    assert shared_name.startswith("custom__")
    assert shared_name.endswith("__linear")
    assert svc.list_active_mcp_server_names(viewer_id) == [shared_name]
    assert list(svc.custom_harness_configs(viewer_id)) == [shared_name]
    assert svc.list_default_open_mcp_server_names(viewer_id) == []


def test_rejects_http_scheme_and_private_host():
    with pytest.raises(ValueError, match="https"):
        normalize_server_spec(
            "bad",
            {"transport": "streamable_http", "url": "http://example.com/mcp"},
        )
    with pytest.raises(ValueError, match="private|not allowed|blocked"):
        normalize_server_spec(
            "bad",
            {"transport": "streamable_http", "url": "https://10.0.0.1/mcp"},
        )


def test_allows_loopback_http_and_https():
    for url in (
        "http://localhost:8080/mcp",
        "https://127.0.0.1/mcp",
        "http://[::1]/mcp",
    ):
        spec = normalize_server_spec(
            "local",
            {"transport": "streamable_http", "url": url},
        )
        assert spec["url"] == url


def test_validate_servers_map_reserved_and_duplicate():
    with pytest.raises(ValueError, match="conflicts"):
        validate_servers_map(
            {"taken": {"transport": "streamable_http", "url": "https://mcp.example.com/mcp"}},
            reserved_names={"taken"},
        )
    with pytest.raises(ValueError, match="invalid server name"):
        validate_servers_map(
            {"bad name": {"transport": "streamable_http", "url": "https://mcp.example.com/mcp"}}
        )


def test_display_name_normalized_and_used_in_list_api(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    servers = svc.put_custom_servers(
        uid,
        {
            "http-server-1": {
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "display_name": "  我的知识库  ",
            },
        },
    )
    assert servers["http-server-1"]["display_name"] == "我的知识库"
    listed = svc.list_instances_for_api(uid)
    assert len(listed) == 1
    assert listed[0]["mcp_server_name"] == "http-server-1"
    assert listed[0]["display_name"] == "我的知识库"


def test_harness_spec_streamable_http_adds_accept():
    spec = harness_spec_for_server(
        {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer x"},
            "enabled": True,
        }
    )
    assert spec["headers"]["Authorization"] == "Bearer x"
    assert spec["headers"]["Accept"] == "application/json, text/event-stream"
    assert "enabled" not in spec


def test_put_and_expand_custom_mcp(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    servers = svc.put_custom_servers(
        uid,
        {
            "deepwiki": {
                "transport": "streamable_http",
                "url": "https://mcp.deepwiki.com/mcp",
            },
            "optional": {
                "transport": "streamable_http",
                "url": "https://mcp.example.com/optional",
                "enabled": False,
            },
        },
    )
    assert set(servers) == {"deepwiki", "optional"}
    assert (
        extract_servers(svc.decrypt(svc._repo.get_by_user_kind(uid, CUSTOM_MCP_KIND).instance_id))
        == servers
    )

    listed = {row["mcp_server_name"]: row for row in svc.list_instances_for_api(uid)}
    assert listed["deepwiki"]["status"] == "active"
    assert listed["deepwiki"]["instance_id"] == "custom:deepwiki"
    assert listed["optional"]["status"] == "disabled"
    assert svc.list_active_mcp_server_names(uid) == ["deepwiki"]


def test_put_custom_mcp_rejects_stdio_before_persisting(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    with pytest.raises(ValueError, match="stdio MCP is disabled"):
        svc.put_custom_servers(
            uid,
            {
                "local": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "arbitrary-package"],
                },
            },
        )
    assert svc.get_custom_servers(uid) == {}


@pytest.mark.asyncio
async def test_custom_mcp_stdio_probe_rejects_process_before_execution(tmp_path: Path):
    marker = tmp_path / "stdio-process-ran"
    result = await probe_custom_mcp_server(
        {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"],
        }
    )
    assert result["ok"] is False
    assert "stdio MCP is disabled" in result["error"]
    assert not marker.exists()


def test_put_empty_servers_deletes_parent_row(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_custom_servers(
        uid,
        {
            "only": {
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
            },
        },
    )
    assert svc._repo.get_by_user_kind(uid, CUSTOM_MCP_KIND) is not None  # noqa: SLF001
    assert svc.put_custom_servers(uid, {}) == {}
    assert svc._repo.get_by_user_kind(uid, CUSTOM_MCP_KIND) is None  # noqa: SLF001
    assert svc.list_instances_for_api(uid) == []


def test_custom_mcp_name_conflicts_with_builtin(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    iid = new_ulid()
    name = mcp_server_name("tencent-docs", iid)
    svc._repo.create(  # noqa: SLF001
        instance_id=iid,
        user_id=uid,
        kind="tencent-docs",
        display_name="Docs",
        mcp_server_name=name,
    )
    with pytest.raises(ValueError, match="conflicts"):
        svc.put_custom_servers(
            uid,
            {
                name: {
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                }
            },
        )


def test_list_default_open_mcp_server_names(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    iid = new_ulid()
    svc._repo.create(
        instance_id=iid,
        user_id=uid,
        kind="tencent-docs",
        display_name="docs",
        mcp_server_name=mcp_server_name("tencent-docs", iid),
        config_json='{"default_open": true}',
    )
    from octop.infra.connectors.crypto import encrypt_credentials

    blob = encrypt_credentials(svc._secret_repo, {"token": "t", "instance_id": iid})
    svc._repo.upsert_credentials(instance_id=iid, blob=blob)

    svc.put_custom_servers(
        uid,
        {
            "always": {
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "default_open": True,
            },
            "opt": {
                "transport": "streamable_http",
                "url": "https://mcp.example.com/opt",
                "default_open": False,
            },
        },
    )
    names = svc.list_default_open_mcp_server_names(uid)
    assert mcp_server_name("tencent-docs", iid) in names
    assert "always" in names
    assert "opt" not in names


@pytest.mark.asyncio
async def test_ensure_fresh_custom_servers_marks_reauth_when_refresh_fails_expired(
    svc: ConnectorService,
    db: SqlitePool,
) -> None:
    import time
    from unittest.mock import AsyncMock, patch

    uid = _ensure_user(db)
    expired_at = int(time.time()) - 60
    svc.put_custom_servers(
        uid,
        {
            "srv": {
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
            }
        },
    )
    svc.apply_custom_server_oauth(
        uid,
        "srv",
        {
            "access_token": "old",
            "refresh_token": "refresh-me",
            "expires_at": expired_at,
            "oauth_client_id": "cid",
        },
        issuer="https://example.com",
        resource="https://example.com/mcp",
    )
    with patch(
        "octop.infra.connectors.service.refresh_custom_mcp_oauth",
        new_callable=AsyncMock,
        side_effect=ValueError("refresh failed"),
    ):
        await svc.ensure_fresh_custom_servers(uid)

    preview = svc.get_custom_servers_for_api(uid)
    assert preview["srv"]["oauth"] == {"configured": False, "required": True}
    stored = svc.get_custom_servers(uid)["srv"]["oauth"]
    assert stored == {"required": True}


@pytest.mark.asyncio
async def test_ensure_fresh_custom_servers_keeps_valid_token_when_refresh_fails(
    svc: ConnectorService,
    db: SqlitePool,
) -> None:
    import time
    from unittest.mock import AsyncMock, patch

    uid = _ensure_user(db)
    expires_at = int(time.time()) + 60
    svc.put_custom_servers(
        uid,
        {
            "srv": {
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
            }
        },
    )
    svc.apply_custom_server_oauth(
        uid,
        "srv",
        {
            "access_token": "still-valid",
            "refresh_token": "refresh-me",
            "expires_at": expires_at,
            "oauth_client_id": "cid",
        },
        issuer="https://example.com",
        resource="https://example.com/mcp",
    )
    with patch(
        "octop.infra.connectors.service.refresh_custom_mcp_oauth",
        new_callable=AsyncMock,
        side_effect=ValueError("refresh failed"),
    ):
        await svc.ensure_fresh_custom_servers(uid)

    preview = svc.get_custom_servers_for_api(uid)
    assert preview["srv"]["oauth"]["configured"] is True
    assert svc.get_custom_servers(uid)["srv"]["oauth"]["access_token"] == "still-valid"
