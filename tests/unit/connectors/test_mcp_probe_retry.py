from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from octop.infra.connectors.probe import probe_streamable_http_mcp


@pytest.mark.asyncio
async def test_streamable_http_probe_retries_transient_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp
    import mcp.client.streamable_http as streamable_http

    attempts = 0

    class Transport:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary disconnect")
            return object(), object(), None

        async def __aexit__(self, *_args):
            return None

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="search", description="Search")])

    monkeypatch.setattr(
        streamable_http,
        "streamablehttp_client",
        lambda *_args, **_kwargs: Transport(),
    )
    monkeypatch.setattr(mcp, "ClientSession", Session)

    result = await probe_streamable_http_mcp("https://mcp.example/test", {}, kind="notion")

    assert attempts == 2
    assert result == {
        "ok": True,
        "tool_count": 1,
        "tools": [{"name": "search", "description": "Search"}],
    }
