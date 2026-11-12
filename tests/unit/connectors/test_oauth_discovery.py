"""Unit tests for MCP OAuth discovery and unified oauth targets."""

from __future__ import annotations

import pytest

from octop.infra.connectors.custom_mcp import (
    build_oauth_storage,
    harness_spec_for_server,
    mark_oauth_reauth_required,
    merge_preserved_oauth,
    redact_server_for_api,
)
from octop.infra.connectors.oauth.discovery import (
    build_protected_resource_metadata_urls,
    clear_oauth_discovery_cache,
    discover_oauth_from_mcp_url,
    parse_www_authenticate_resource_metadata,
)
from octop.infra.connectors.oauth.registry import (
    oauth_state_kind_for_target,
    oauth_target_requires_https,
)


def test_build_protected_resource_metadata_urls_path_and_root():
    urls = build_protected_resource_metadata_urls("https://example.com/mcp")
    assert urls == [
        "https://example.com/.well-known/oauth-protected-resource/mcp",
        "https://example.com/.well-known/oauth-protected-resource",
    ]


def test_build_protected_resource_metadata_urls_prefers_www_auth_header():
    header_url = "https://example.com/.well-known/oauth-protected-resource/custom"
    urls = build_protected_resource_metadata_urls(
        "https://example.com/mcp",
        www_auth_resource_metadata=header_url,
    )
    assert urls[0] == header_url


def test_parse_www_authenticate_resource_metadata():
    raw = 'Bearer error="invalid_token", resource_metadata="https://mcp.example.com/prm"'
    assert parse_www_authenticate_resource_metadata(raw) == "https://mcp.example.com/prm"


def test_oauth_target_helpers():
    assert oauth_state_kind_for_target({"type": "catalog", "kind": "notion"}) == "notion"
    assert oauth_state_kind_for_target({"type": "custom_mcp", "server_name": "x"}) == ("custom-mcp")
    assert oauth_target_requires_https({"type": "catalog", "kind": "notion"}) is True
    assert oauth_target_requires_https({"type": "custom_mcp", "server_name": "x"}) is True
    assert oauth_target_requires_https({"type": "catalog", "kind": "tencent-docs"}) is False


def test_merge_preserved_oauth_and_redaction():
    existing = {
        "srv": {
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "oauth": {"access_token": "secret", "expires_at": 123},
        }
    }
    incoming = {
        "srv": {
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "oauth": {"access_token": "client-sent"},
        }
    }
    merged = merge_preserved_oauth(incoming, existing)
    assert merged["srv"]["oauth"]["access_token"] == "secret"
    preview = redact_server_for_api(merged["srv"])
    assert preview["oauth"] == {"configured": True, "expires_at": 123}
    assert "access_token" not in preview["oauth"]


def test_oauth_required_hint_redaction_and_merge():
    existing = {
        "srv": {
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "oauth": {"required": True},
        }
    }
    incoming = {"srv": {"transport": "streamable_http", "url": "https://example.com/mcp"}}
    merged = merge_preserved_oauth(incoming, existing)
    assert merged["srv"]["oauth"] == {"required": True}
    preview = redact_server_for_api(merged["srv"])
    assert preview["oauth"] == {"configured": False, "required": True}


def test_harness_spec_injects_oauth_bearer():
    spec = {
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "oauth": build_oauth_storage(
            {"access_token": "tok123", "refresh_token": "r", "expires_at": 1},
            issuer="https://example.com",
            resource="https://example.com/mcp",
        ),
    }
    harness = harness_spec_for_server(spec)
    assert harness["headers"]["Authorization"] == "Bearer tok123"


def test_oauth_state_kind_invalid():
    with pytest.raises(ValueError, match="unsupported oauth target"):
        oauth_state_kind_for_target({"type": "unknown"})


def test_mark_oauth_reauth_required_clears_tokens():
    spec = {
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "oauth": build_oauth_storage(
            {"access_token": "secret", "refresh_token": "r", "expires_at": 1},
            issuer="https://example.com",
            resource="https://example.com/mcp",
        ),
    }
    marked = mark_oauth_reauth_required(spec)
    assert marked["oauth"] == {"required": True}
    preview = redact_server_for_api(marked)
    assert preview["oauth"] == {"configured": False, "required": True}


@pytest.mark.asyncio
async def test_discover_oauth_from_mcp_url_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_oauth_discovery_cache()
    calls = 0
    cached_result = {
        "available": True,
        "issuer": "https://auth.example.com",
        "resource": "https://example.com/mcp",
        "metadata": {"registration_endpoint": "https://auth.example.com/reg"},
        "scopes_supported": None,
        "error": None,
    }

    async def fake_uncached(url: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert url == "https://example.com/mcp"
        return cached_result

    monkeypatch.setattr(
        "octop.infra.connectors.oauth.discovery._discover_oauth_from_mcp_url_uncached",
        fake_uncached,
    )

    first = await discover_oauth_from_mcp_url("https://example.com/mcp")
    second = await discover_oauth_from_mcp_url("https://example.com/mcp")
    assert first == cached_result
    assert second == cached_result
    assert calls == 1

    bypass = await discover_oauth_from_mcp_url("https://example.com/mcp", use_cache=False)
    assert bypass == cached_result
    assert calls == 2


@pytest.mark.asyncio
async def test_discovery_reports_pre_registered_client_requirement(monkeypatch):
    import octop.infra.connectors.oauth.discovery as discovery

    async def prm(_candidate):
        return {
            "resource": "https://mcp.example.com/mcp",
            "authorization_servers": ["https://auth.example.com"],
        }

    async def metadata(_issuer):
        return {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "scopes_supported": ["read"],
        }

    async def probe(_url):
        return None

    monkeypatch.setattr(discovery, "_probe_401_resource_metadata", probe)
    monkeypatch.setattr(discovery, "_fetch_prm_document", prm)
    monkeypatch.setattr(discovery, "fetch_authorization_metadata", metadata)
    found = await discovery._discover_oauth_from_mcp_url_uncached("https://mcp.example.com/mcp")
    assert found["available"] is False
    assert found["client_required"] is True
    assert found["metadata"]["token_endpoint"] == "https://auth.example.com/token"


@pytest.mark.asyncio
async def test_custom_oauth_uses_saved_client_without_dynamic_registration(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import octop.infra.connectors.oauth.registry as registry

    async def manual_metadata(client, *, issuer):
        assert issuer == "https://auth.example.com"
        assert client["client_id"] == "registered-client"
        return {
            "authorization_endpoint": client["authorization_endpoint"],
            "token_endpoint": client["token_endpoint"],
            "_manual_client_metadata": True,
        }

    async def fail_dynamic_registration(*_args, **_kwargs):
        raise AssertionError("dynamic registration must not be called")

    monkeypatch.setattr(registry, "manual_client_metadata", manual_metadata)
    monkeypatch.setattr(registry, "register_dynamic_client", fail_dynamic_registration)
    authorize_url, _verifier, ctx = await registry.start_custom_mcp_oauth(
        server_name="custom",
        mcp_url="https://mcp.example.com/mcp",
        redirect_uri="https://octop.example.com/api/connectors/oauth/callback",
        state="state",
        oauth_client={
            "client_id": "registered-client",
            "client_secret": "secret",
            "scope": "read",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
        },
    )
    params = parse_qs(urlsplit(authorize_url).query)
    assert params["client_id"] == ["registered-client"]
    assert params["scope"] == ["read"]
    assert ctx["token_endpoint"] == "https://auth.example.com/token"


def test_custom_oauth_storage_keeps_manual_token_endpoint():
    stored = build_oauth_storage(
        {
            "access_token": "token",
            "oauth_client_id": "client",
            "oauth_token_endpoint": "https://auth.example.com/token",
        },
        issuer="https://auth.example.com",
        resource=None,
    )
    assert stored["oauth_token_endpoint"] == "https://auth.example.com/token"


@pytest.mark.asyncio
async def test_manual_oauth_refresh_uses_saved_token_endpoint(monkeypatch):
    import octop.infra.connectors.oauth.registry as registry

    async def refresh(metadata, **kwargs):
        assert metadata == {
            "token_endpoint": "https://auth.example.com/token",
            "_manual_client_metadata": True,
        }
        assert kwargs["client_id"] == "client"
        return {"access_token": "next-token"}

    async def fail_discovery(_issuer):
        raise AssertionError("manual token endpoint must not be rediscovered")

    monkeypatch.setattr(registry, "refresh_access_token", refresh)
    monkeypatch.setattr(registry, "fetch_authorization_metadata", fail_discovery)
    result = await registry.refresh_custom_mcp_oauth(
        {
            "oauth_issuer": "https://auth.example.com",
            "oauth_token_endpoint": "https://auth.example.com/token",
            "oauth_client_id": "client",
            "refresh_token": "refresh",
        }
    )
    assert result["access_token"] == "next-token"


@pytest.mark.asyncio
async def test_oauth_token_exchange_requests_json_and_uses_pre_registered_client(monkeypatch):
    from unittest.mock import AsyncMock

    from octop.infra.connectors.oauth.mcp import exchange_authorization_code

    response = type(
        "Response",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"access_token": "access", "expires_in": 60},
        },
    )()
    request = AsyncMock(return_value=response)

    async def resolved(_url, *, field):
        assert field == "token_endpoint"

    monkeypatch.setattr("octop.infra.connectors.oauth.mcp.safe_request", request)
    monkeypatch.setattr("octop.infra.connectors.oauth.mcp.validate_https_url_resolved", resolved)
    result = await exchange_authorization_code(
        {
            "token_endpoint": "https://auth.example.com/token",
            "_manual_client_metadata": True,
        },
        issuer="https://auth.example.com",
        client_id="registered-client",
        client_secret="client-secret",
        code="authorization-code",
        redirect_uri="https://octop.example.com/callback",
        code_verifier="verifier",
    )
    assert result["access_token"] == "access"
    request.assert_awaited_once()
    assert request.await_args.kwargs["headers"]["Accept"] == "application/json"
    assert request.await_args.kwargs["data"]["client_id"] == "registered-client"
