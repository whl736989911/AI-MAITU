from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from octop.api.routers.branding import branding_manifest


def _request(*, headers: list[tuple[bytes, bytes]], root_path: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("maitu.example", 443),
            "path": "/api/branding/manifest.webmanifest",
            "root_path": root_path,
            "headers": headers,
        }
    )


@pytest.mark.asyncio
async def test_branding_manifest_uses_proxy_prefix_for_scope_and_icons() -> None:
    response = await branding_manifest(
        _request(
            headers=[
                (b"host", b"maitu.example"),
                (b"x-forwarded-prefix", b"/maitu"),
            ]
        ),
        SimpleNamespace(services=None),
    )
    manifest = json.loads(response.body)

    assert manifest["start_url"] == "/maitu/"
    assert manifest["scope"] == "/maitu/"
    assert manifest["id"] == "/maitu/"
    assert all(icon["src"].startswith("/maitu/brand/") for icon in manifest["icons"])


@pytest.mark.asyncio
async def test_branding_manifest_uses_asgi_root_path_and_does_not_double_prefix() -> None:
    response = await branding_manifest(
        _request(
            headers=[
                (b"host", b"maitu.example"),
                (b"x-forwarded-prefix", b"/maitu"),
            ],
            root_path="/maitu",
        ),
        SimpleNamespace(services=None),
    )
    manifest = json.loads(response.body)

    assert manifest["start_url"] == "/maitu/"
    assert manifest["icons"][0]["src"].count("/maitu/") == 1
