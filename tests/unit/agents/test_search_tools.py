"""Behavioral coverage for selected provider search adapters."""

from __future__ import annotations

import httpx
import pytest

from octop.infra.agents import search_tools


def test_provider_precedence_matches_search_settings_order() -> None:
    assert (
        search_tools.configured_search_provider(
            {"TAVILY_API_KEY": "tvly", "DEEPSEEK_API_KEY": "deepseek"}
        )
        == "tavily"
    )
    assert (
        search_tools.configured_search_provider(
            {"DEEPSEEK_API_KEY": "deepseek", "TENCENTCLOUD_WSA_APIKEY": "yuanbao"}
        )
        == "deepseek"
    )
    assert search_tools.configured_search_provider({}) is None


def test_custom_tools_only_exist_for_unsupported_selected_provider() -> None:
    assert search_tools.build_search_provider_tools({"TAVILY_API_KEY": "tvly"}) == []
    tools = search_tools.build_search_provider_tools({"DEEPSEEK_API_KEY": "deepseek"})
    assert [tool.name for tool in tools] == ["deepseek_search"]
    tools = search_tools.build_search_provider_tools({"TENCENTCLOUD_WSA_APIKEY": "yuanbao"})
    assert [tool.name for tool in tools] == ["yuanbao_search"]


@pytest.mark.asyncio
async def test_deepseek_search_calls_provider_and_returns_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.deepseek.com"
            assert request.headers.get("Authorization") == "Bearer deepseek-secret"
            assert b'"query":"current policy"' in request.content
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Policy",
                            "url": "https://example.test",
                            "content": "Current details",
                        }
                    ]
                },
            )

        options = dict(kwargs)
        options["transport"] = httpx.MockTransport(handler)
        return real(*args, **options)

    monkeypatch.setattr(search_tools.httpx, "AsyncClient", factory)
    tool = search_tools.build_search_provider_tools({"DEEPSEEK_API_KEY": "deepseek-secret"})[0]
    result = await tool.ainvoke({"query": "current policy"})
    assert "Policy" in result
    assert "https://example.test" in result
    assert "Current details" in result


@pytest.mark.asyncio
async def test_yuanbao_search_returns_provider_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.wsa.cloud.tencent.com"
            assert request.headers.get("Authorization") == "Bearer yuanbao-secret"
            return httpx.Response(
                200,
                json={
                    "Response": {
                        "Pages": [{"Title": "新闻", "Url": "https://example.cn", "Content": "内容"}]
                    }
                },
            )

        options = dict(kwargs)
        options["transport"] = httpx.MockTransport(handler)
        return real(*args, **options)

    monkeypatch.setattr(search_tools.httpx, "AsyncClient", factory)
    tool = search_tools.build_search_provider_tools({"TENCENTCLOUD_WSA_APIKEY": "yuanbao-secret"})[
        0
    ]
    result = await tool.ainvoke({"query": "政策"})
    assert "新闻" in result
    assert "https://example.cn" in result
    assert "内容" in result
