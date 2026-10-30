"""Native web-search adapters for providers not exposed by the harness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

_SEARCH_PROVIDER_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tavily", ("TAVILY_API_KEY",)),
    ("brave", ("BRAVE_API_KEY",)),
    ("deepseek", ("DEEPSEEK_API_KEY",)),
    ("google", ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")),
    ("kimi", ("MOONSHOT_API_KEY",)),
    ("yuanbao", ("TENCENTCLOUD_WSA_APIKEY",)),
)
SEARCH_PROVIDER_TOOL_NAMES: frozenset[str] = frozenset(
    {f"{provider}_search" for provider, _keys in _SEARCH_PROVIDER_KEYS} | {"searchfree_search"}
)


def configured_search_provider(env: Mapping[str, str]) -> str | None:
    """Select the first fully configured provider in SearchConfig display order."""
    for provider, keys in _SEARCH_PROVIDER_KEYS:
        if all(str(env.get(key, "")).strip() for key in keys):
            return provider
    return None


def build_search_provider_tools(env: Mapping[str, str]) -> list[StructuredTool]:
    """Build only the adapter for a selected provider unsupported by harness."""
    provider = configured_search_provider(env)
    if provider == "deepseek":
        api_key = str(env["DEEPSEEK_API_KEY"]).strip()

        async def deepseek_search(query: str) -> str:
            return await _deepseek_search(query, api_key)

        return [
            StructuredTool.from_function(
                coroutine=deepseek_search,
                name="deepseek_search",
                description="Search the web with DeepSeek Search for current information. Returns ranked results with titles, URLs, and available excerpts.",
            )
        ]
    if provider == "yuanbao":
        api_key = str(env["TENCENTCLOUD_WSA_APIKEY"]).strip()

        async def yuanbao_search(query: str) -> str:
            return await _yuanbao_search(query, api_key)

        return [
            StructuredTool.from_function(
                coroutine=yuanbao_search,
                name="yuanbao_search",
                description="Search the web with Tencent Yuanbao Search for current information. Returns ranked results with titles, URLs, and available excerpts.",
            )
        ]
    return []


async def _deepseek_search(query: str, api_key: str) -> str:
    text = query.strip()
    if not text:
        raise ValueError("search query is required")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/tools/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"query": text, "count": 10, "need_content": True},
        )
    return _format_results(response, provider="DeepSeek", result_keys=("results", "data"))


async def _yuanbao_search(query: str, api_key: str) -> str:
    text = query.strip()
    if not text:
        raise ValueError("search query is required")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.wsa.cloud.tencent.com/SearchPro",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"Query": text, "Cnt": 10},
        )
    if response.status_code >= 400:
        raise _provider_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Yuanbao Search returned invalid JSON") from exc
    body = payload.get("Response") if isinstance(payload, dict) else None
    error = body.get("Error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = str(error.get("Code") or "unknown")
        message = str(error.get("Message") or code)
        raise RuntimeError(f"Yuanbao Search error ({code}): {message}")
    pages = body.get("Pages") if isinstance(body, dict) else None
    return _render_results(pages if isinstance(pages, list) else [], provider="Yuanbao")


def _format_results(
    response: httpx.Response, *, provider: str, result_keys: tuple[str, ...]
) -> str:
    if response.status_code >= 400:
        raise _provider_error(response)
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider} Search returned invalid JSON") from exc
    results: Any = []
    if isinstance(payload, dict):
        for key in result_keys:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                results = candidate
                break
    return _render_results(results, provider=provider)


def _provider_error(response: httpx.Response) -> RuntimeError:
    status = response.status_code
    if status in {401, 403}:
        category = "authentication failed"
    elif status == 402:
        category = "insufficient balance"
    elif status == 429:
        category = "rate limit exceeded"
    elif status >= 500:
        category = "provider service unavailable"
    else:
        category = "request rejected"
    return RuntimeError(f"Web search {category} (HTTP {status})")


def _render_results(results: list[Any], *, provider: str) -> str:
    if not results:
        return f"{provider} Search returned no results."
    rendered: list[str] = []
    for index, item in enumerate(results[:10], 1):
        if not isinstance(item, dict):
            continue
        title = _first_text(item, "title", "Title", "name", "Name")
        url = _first_text(item, "url", "Url", "URL", "link", "Link")
        snippet = _first_text(
            item,
            "content",
            "Content",
            "snippet",
            "Snippet",
            "description",
            "Description",
            "summary",
            "Summary",
        )
        fields = [f"{index}. {title or 'Untitled'}"]
        if url:
            fields.append(f"URL: {url}")
        if snippet:
            fields.append(snippet[:4000])
        rendered.append("\n".join(fields))
    return "\n\n".join(rendered) if rendered else f"{provider} Search returned no usable results."


def _first_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


__all__ = [
    "SEARCH_PROVIDER_TOOL_NAMES",
    "build_search_provider_tools",
    "configured_search_provider",
]
