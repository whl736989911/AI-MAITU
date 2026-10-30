"""Tests for public OIDC callback URL helpers."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from octop.api.common.public_base import (
    prefix_public_path,
    resolve_public_base,
    resolve_public_prefix,
)
from octop.infra.auth.sso.public_base import build_redirect_uri


def _request(
    *,
    scheme: str = "http",
    headers: list[tuple[bytes, bytes]] | None = None,
    root_path: str = "",
) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("internal.example", 8080),
            "path": "/",
            "root_path": root_path,
            "headers": headers or [(b"host", b"internal.example:8080")],
        }
    )


def test_resolve_public_base_uses_forwarded_proxy_headers() -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"octop.example"),
        ]
    )

    assert resolve_public_base(request) == "https://octop.example"


def test_resolve_public_base_uses_request_url_without_proxy_headers() -> None:
    request = _request(scheme="https", headers=[(b"host", b"octop.example")])

    assert resolve_public_base(request) == "https://octop.example"


@pytest.mark.parametrize(
    ("scheme", "host", "port", "expected"),
    [
        ("https", "login.example", "8443", "https://login.example:8443"),
        ("https", "[2001:db8::1]", "8443", "https://[2001:db8::1]:8443"),
        ("https", "login.example:9443", "8443", "https://login.example:9443"),
        ("http", "login.example", "80", "http://login.example"),
        ("https", "login.example", "443", "https://login.example"),
        ("http", "login.example", "443", "http://login.example:443"),
    ],
)
def test_forwarded_port_preserves_external_callback_origin(
    scheme: str, host: str, port: str, expected: str
) -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-proto", scheme.encode()),
            (b"x-forwarded-host", host.encode()),
            (b"x-forwarded-port", port.encode()),
        ]
    )
    assert build_redirect_uri(resolve_public_base(request)) == (
        f"{expected}/api/auth/oidc/callback"
    )


@pytest.mark.parametrize("port", ["0", "65536", "-1", "+8443", "8e3", "8443/path"])
def test_invalid_forwarded_port_cannot_change_callback_origin(port: str) -> None:
    request = _request(
        headers=[
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"login.example"),
            (b"x-forwarded-port", port.encode()),
        ]
    )
    assert resolve_public_base(request) == "https://login.example"


def test_forwarded_port_uses_first_proxy_value_and_host_fallback() -> None:
    request = _request(
        headers=[
            (b"host", b"login.example"),
            (b"x-forwarded-proto", b"https, http"),
            (b"x-forwarded-port", b"8443, 8080"),
        ]
    )
    assert resolve_public_base(request) == "https://login.example:8443"


def test_build_redirect_uri_uses_oidc_callback_path() -> None:
    assert (
        build_redirect_uri("https://octop.example/")
        == "https://octop.example/api/auth/oidc/callback"
    )


def test_build_redirect_uri_accepts_oauth_callback_path() -> None:
    assert (
        build_redirect_uri("https://octop.example", "/api/auth/oauth/callback")
        == "https://octop.example/api/auth/oauth/callback"
    )


def test_forwarded_prefix_is_added_to_public_base_and_redirect_uri() -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"maitu.example"),
            (b"x-forwarded-prefix", b"/maitu/"),
        ]
    )

    public_base = resolve_public_base(request)
    assert public_base == "https://maitu.example/maitu"
    assert build_redirect_uri(public_base) == ("https://maitu.example/maitu/api/auth/oidc/callback")


def test_asgi_root_path_supplies_prefix_when_forwarded_prefix_is_absent() -> None:
    request = _request(root_path="/maitu/")

    assert resolve_public_prefix(request) == "/maitu"
    assert resolve_public_base(request) == "http://internal.example:8080/maitu"


def test_invalid_forwarded_prefix_is_rejected_and_existing_prefix_is_not_duplicated() -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-prefix", b"/maitu/../other"),
        ]
    )

    assert resolve_public_prefix(request) == ""
    assert prefix_public_path("/maitu", "/maitu/brand/logo.png") == ("/maitu/brand/logo.png")
