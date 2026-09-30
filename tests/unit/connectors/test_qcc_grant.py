"""Exercise fixed QCC MCP resources using the stored API Key."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from octop.config import OctopConfig
from octop.infra.connectors import qcc
from octop.infra.connectors.service import ConnectorService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo


def _service(pool: SqlitePool, repo: ConnectorRepo | None = None) -> ConnectorService:
    return ConnectorService(
        repo=repo or ConnectorRepo(pool),
        secret_repo=SecretRepo(pool),
        settings_repo=SettingsRepo(pool),
        config=OctopConfig(),
    )


@pytest.fixture
def grant(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,created_at) VALUES (1,'qcc-test','x','user',1)"
        )
    repo = ConnectorRepo(pool)
    repo.create(
        instance_id="grant", user_id=1, kind="qcc", display_name="QCC", mcp_server_name="qcc__grant"
    )
    svc = _service(pool, repo)
    svc.encrypt_and_store(instance_id="grant", payload={"api_key": "qcc-api-key"})
    return pool, repo, svc


@pytest.fixture
def mcp_http(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, dict[str, object]]] = []
    client_class = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) in qcc.RESOURCES.values()
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers["Authorization"], body))
        method = body["method"]
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/list":
            if body.get("params", {}).get("cursor") == "second":
                result = {"tools": [{"name": "detail", "inputSchema": {"type": "object"}}]}
            else:
                result = {
                    "tools": [{"name": "lookup", "inputSchema": {"type": "object"}}],
                    "nextCursor": "second",
                }
        else:
            assert method == "tools/call"
            assert body["params"]["name"] == "lookup"
            assert body["params"]["arguments"] == {"query": "example"}
            result = {"content": [{"type": "text", "text": request.url.path}], "isError": False}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return client_class(transport=httpx.MockTransport(handler), **kwargs)

    async def metadata(method, url, **kwargs):
        assert method == "GET"
        resource = url.split("/")[-2]
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"resource": qcc.RESOURCES[resource], "authorization_servers": [qcc.ISSUER]},
        )

    qcc.clear_metadata_cache()
    monkeypatch.setattr(qcc, "safe_request", metadata)
    monkeypatch.setattr(qcc.httpx, "AsyncClient", factory)
    return calls


@pytest.mark.asyncio
async def test_five_resources_list_pages_and_route_calls(grant, mcp_http):
    _, _, svc = grant
    listed = await svc.handle_qcc_request("grant", {"id": 1, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert len(names) == len(set(names)) == 10
    for resource in qcc.RESOURCES:
        assert f"{resource}__detail" in names
        result = await svc.handle_qcc_request(
            "grant",
            {
                "id": 2,
                "method": "tools/call",
                "params": {"name": f"{resource}__lookup", "arguments": {"query": "example"}},
            },
        )
        assert result["result"]["content"][0]["text"] == f"/mcp/{resource}/stream"
    assert {url for url, _, _ in mcp_http} == set(qcc.RESOURCES.values())
    assert {authorization for _, authorization, _ in mcp_http} == {"Bearer qcc-api-key"}


@pytest.mark.asyncio
async def test_internal_token_survives_api_key_rotation(grant):
    pool, _, svc = grant
    creds = svc.decrypt("grant")
    stable_token = creds["internal_token"]
    creds["api_key"] = "rotated-key"
    svc.encrypt_and_store(instance_id="grant", payload=creds)
    restarted = _service(SqlitePool(pool.path))
    restored = restarted.decrypt("grant")
    assert restored["internal_token"] == stable_token
    assert restored["api_key"] == "rotated-key"


@pytest.mark.asyncio
async def test_unknown_resource_never_receives_api_key(grant, monkeypatch):
    _, _, svc = grant
    request = AsyncMock()
    monkeypatch.setattr(qcc, "request_resource", request)
    result = await svc.handle_qcc_request(
        "grant", {"id": 1, "method": "tools/call", "params": {"name": "evil__lookup"}}
    )
    assert "error" in result
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_errors_are_redacted_and_not_retried(grant, monkeypatch):
    _, _, svc = grant
    request = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "credential-bearing error",
            request=httpx.Request("POST", qcc.RESOURCES["risk"]),
            response=httpx.Response(401),
        )
    )
    monkeypatch.setattr(qcc, "request_resource", request)
    result = await svc.handle_qcc_request(
        "grant", {"id": 1, "method": "tools/call", "params": {"name": "risk__lookup"}}
    )
    assert "error" in result
    assert "credential-bearing error" not in json.dumps(result)
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_tools_list_keeps_successful_resources_when_one_fails(grant, monkeypatch):
    _, _, svc = grant

    async def request(resource, *_args, **_kwargs):
        if resource == "risk":
            raise ValueError("credential-bearing failure")
        return {"tools": [{"name": "lookup"}]}

    monkeypatch.setattr(qcc, "request_resource", request)
    listed = await svc.handle_qcc_request("grant", {"id": 1, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == [f"{resource}__lookup" for resource in qcc.RESOURCES if resource != "risk"]


@pytest.mark.asyncio
async def test_probe_reports_partial_failures_without_upstream_messages(monkeypatch):
    async def request(resource, *_args, **_kwargs):
        if resource == "risk":
            raise ValueError("credential-bearing failure")
        return {"tools": [{"name": "lookup"}]}

    monkeypatch.setattr(qcc, "request_resource", request)
    result = await qcc.probe("synthetic-api-key")
    assert result["ok"] is True
    assert result["tool_count"] == 4
    assert result["servers"]["risk"] == {"ok": False}
    assert len(result["servers"]) == 5
    assert "credential-bearing failure" not in json.dumps(result)


def test_preferred_tool_filter_falls_back_for_unknown_catalogs():
    listed = [
        {"name": "company__get_company_profile"},
        {"name": "company__lookup"},
        {"name": "company__get_change_records"},
    ]
    assert [tool["name"] for tool in qcc.select_exposed_tools(listed)] == [
        "company__get_company_profile",
        "company__get_change_records",
    ]
    unknown = [{"name": "company__lookup"}, {"name": "company__detail"}]
    assert qcc.select_exposed_tools(unknown) == unknown


def test_bearer_token_prefers_api_key_and_accepts_legacy_names():
    assert qcc.bearer_token({"api_key": "k", "access_token": "legacy"}) == "k"
    assert qcc.bearer_token({"token": "t"}) == "t"
    assert qcc.bearer_token({}) == ""


@pytest.mark.asyncio
async def test_protected_resource_metadata_mismatch_blocks_transport(monkeypatch):
    qcc.clear_metadata_cache()
    response = httpx.Response(
        200,
        request=httpx.Request("GET", qcc.ISSUER),
        json={"resource": "https://example.com/mcp", "authorization_servers": [qcc.ISSUER]},
    )
    monkeypatch.setattr(qcc, "safe_request", AsyncMock(return_value=response))
    transport = AsyncMock()
    monkeypatch.setattr(qcc, "streamable_http_client", transport)
    with pytest.raises(ValueError, match="metadata mismatch"):
        await qcc.request_resource("risk", "secret", "tools/list", {})
    transport.assert_not_called()
