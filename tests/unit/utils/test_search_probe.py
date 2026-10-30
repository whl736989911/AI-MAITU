"""Unit tests for search-provider HTTP probes."""

from __future__ import annotations

import httpx
import pytest

from octop.infra.utils import search_probe


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    real = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs = dict(kwargs)
        kwargs["transport"] = handler
        return real(*args, **kwargs)

    monkeypatch.setattr(search_probe.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_unknown_provider() -> None:
    result = await search_probe.probe_search_provider("nope", {})
    assert result["success"] is False
    assert result["error_type"] == "invalid_config"
    assert result["provider_id"] == "nope"


@pytest.mark.asyncio
async def test_tavily_missing_key() -> None:
    result = await search_probe.probe_search_provider("tavily", {})
    assert result["success"] is False
    assert result["error_type"] == "invalid_config"
    assert "TAVILY_API_KEY" in (result.get("error") or "")


@pytest.mark.asyncio
async def test_google_missing_cse() -> None:
    result = await search_probe.probe_search_provider("google", {"GOOGLE_API_KEY": "gk"})
    assert result["success"] is False
    assert "GOOGLE_CSE_ID" in (result.get("error") or "")


@pytest.mark.asyncio
async def test_tavily_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com"
        assert request.headers.get("Authorization") == "Bearer tvly-test"
        return httpx.Response(200, json={"results": [{"url": "https://example.com"}]})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "tavily",
        {"TAVILY_API_KEY": "tvly-test"},
    )
    assert result["success"] is True
    assert result["result_count"] == 1
    assert result["provider_id"] == "tavily"


@pytest.mark.asyncio
async def test_tavily_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "tavily",
        {"TAVILY_API_KEY": "bad"},
    )
    assert result["success"] is False
    assert result["error_type"] == "auth_error"


@pytest.mark.asyncio
async def test_brave_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.search.brave.com"
        assert request.headers.get("X-Subscription-Token") == "brave-key"
        return httpx.Response(
            200,
            json={"web": {"results": [{"url": "https://example.com"}]}},
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "brave",
        {"BRAVE_API_KEY": "brave-key"},
    )
    assert result["success"] is True
    assert result["result_count"] == 1


@pytest.mark.asyncio
async def test_deepseek_probe_uses_request_key_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.deepseek.com"
        assert request.headers.get("Authorization") == "Bearer deepseek-key"
        assert request.read()
        return httpx.Response(200, json={"results": [{"title": "result"}]})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "deepseek", {"DEEPSEEK_API_KEY": " deepseek-key ", "TAVILY_API_KEY": "unrelated"}
    )
    assert result["success"] is True
    assert result["provider_id"] == "deepseek"
    assert result["result_count"] == 1


@pytest.mark.asyncio
async def test_yuanbao_probe_parses_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.wsa.cloud.tencent.com"
        assert request.headers.get("Authorization") == "Bearer tencent-key"
        return httpx.Response(
            200,
            json={"Response": {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}}},
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "yuanbao", {"TENCENTCLOUD_WSA_APIKEY": "tencent-key"}
    )
    assert result["success"] is False
    assert result["error_type"] == "rate_limit"


@pytest.mark.asyncio
async def test_http_rate_limit_has_distinct_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many requests")

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = await search_probe.probe_search_provider(
        "deepseek", {"DEEPSEEK_API_KEY": "deepseek-key"}
    )
    assert result["success"] is False
    assert result["error_type"] == "rate_limit"
