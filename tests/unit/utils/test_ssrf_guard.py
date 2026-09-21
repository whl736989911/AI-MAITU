"""Unit tests for SSRF guard (infra/utils/ssrf_guard.py)."""

from __future__ import annotations

import httpx
import pytest

from octop.infra.utils import ssrf_guard
from octop.infra.utils.ssrf_guard import (
    OutboundFetchError,
    UnsafeOutboundUrl,
    _read_capped,
    host_allowed_for_issuer,
    issuer_base_domain,
    safe_get,
    validate_https_url,
)


def test_issuer_base_domain() -> None:
    assert issuer_base_domain("https://mcp.notion.com") == "notion.com"


@pytest.mark.parametrize(
    ("host", "issuer", "allowed"),
    [
        ("mcp.notion.com", "https://mcp.notion.com", True),
        ("api.notion.com", "https://mcp.notion.com", True),
        ("evil.com", "https://mcp.notion.com", False),
        ("notion.com.evil.com", "https://mcp.notion.com", False),
    ],
)
def test_host_allowed_for_issuer(host: str, issuer: str, allowed: bool) -> None:
    assert host_allowed_for_issuer(host, issuer) is allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.notion.com/token",
        "https://127.0.0.1/token",
        "https://10.0.0.1/token",
        "https://localhost/token",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_https_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeOutboundUrl):
        validate_https_url(url, field="token_endpoint")


def test_validate_https_url_accepts_public_host() -> None:
    assert (
        validate_https_url("https://mcp.notion.com/.well-known/oauth-authorization-server")
        == "https://mcp.notion.com/.well-known/oauth-authorization-server"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/page",
        "https://127.0.0.1/page",
        "https://10.1.2.3/page",
        "https://localhost/page",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/page",
        "file:///etc/passwd",
    ],
)
def test_safe_get_refuses_internal_and_non_https_targets(url: str) -> None:
    """The ingest fetch must never reach a private, local, or non-https host."""
    with pytest.raises(UnsafeOutboundUrl):
        safe_get(url, max_bytes=1000)


def test_safe_get_refuses_a_host_that_does_not_resolve() -> None:
    with pytest.raises(UnsafeOutboundUrl):
        safe_get("https://octop-knowledge-does-not-exist.invalid/page", max_bytes=1000)


def test_declared_length_over_the_ceiling_is_refused_before_reading() -> None:
    response = httpx.Response(200, content=b"x" * 10, headers={"content-length": "999999999"})

    with pytest.raises(OutboundFetchError, match="ingest limit"):
        _read_capped(response, target="https://example.com/big", max_bytes=1000)


def test_a_body_past_the_ceiling_is_abandoned_while_streaming() -> None:
    response = httpx.Response(200, content=b"x" * 4000)

    with pytest.raises(OutboundFetchError, match="ingest limit"):
        _read_capped(response, target="https://example.com/big", max_bytes=1000)

    allowed = httpx.Response(200, content=b"x" * 40)
    assert len(_read_capped(allowed, target="https://example.com/ok", max_bytes=1000)) == 40


class _ScriptedTransport(httpx.BaseTransport):
    """Serve canned responses so the redirect/status policy can be driven.

    Only the socket layer is faked: the URL still goes through the real scheme
    and address checks, and IP literals keep the real DNS out of the test.
    """

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self._responses = responses
        self.seen: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append(url)
        response = self._responses.get(url)
        if response is None:
            raise AssertionError(f"unscripted request: {url}")
        response.request = request
        return response


def _safe_get_with(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, httpx.Response], url: str, **kwargs: object
) -> tuple[object, _ScriptedTransport]:
    transport = _ScriptedTransport(responses)
    monkeypatch.setattr(ssrf_guard, "PinnedIPSyncTransport", lambda *_a, **_k: transport)
    return safe_get(url, max_bytes=1000, **kwargs), transport  # type: ignore[arg-type]


def test_a_public_redirect_is_followed_to_the_final_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit, transport = _safe_get_with(
        monkeypatch,
        {
            "https://1.1.1.1/start": httpx.Response(
                301, headers={"location": "https://1.0.0.1/page"}
            ),
            "https://1.0.0.1/page": httpx.Response(
                200, content=b"<h1>Final</h1>", headers={"content-type": "text/html"}
            ),
        },
        "https://1.1.1.1/start",
    )

    assert hit.content == b"<h1>Final</h1>"  # type: ignore[attr-defined]
    assert hit.final_url == "https://1.0.0.1/page"  # type: ignore[attr-defined]
    assert transport.seen == ["https://1.1.1.1/start", "https://1.0.0.1/page"]


def test_a_redirect_onto_an_internal_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public URL must not be able to bounce the fetch onto the intranet."""
    with pytest.raises(UnsafeOutboundUrl, match="redirect to"):
        _safe_get_with(
            monkeypatch,
            {
                "https://1.1.1.1/start": httpx.Response(
                    302, headers={"location": "https://169.254.169.254/latest/meta-data"}
                )
            },
            "https://1.1.1.1/start",
        )


def test_a_non_200_response_is_reported_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(OutboundFetchError, match="404"):
        _safe_get_with(
            monkeypatch,
            {"https://1.1.1.1/gone": httpx.Response(404, content=b"nope")},
            "https://1.1.1.1/gone",
        )


def test_a_redirect_without_a_location_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(OutboundFetchError, match="without a location"):
        _safe_get_with(
            monkeypatch,
            {"https://1.1.1.1/start": httpx.Response(302)},
            "https://1.1.1.1/start",
        )


def test_a_redirect_loop_is_stopped_at_the_hop_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(OutboundFetchError, match="redirected more than 2 times"):
        _safe_get_with(
            monkeypatch,
            {
                "https://1.1.1.1/loop": httpx.Response(
                    307, headers={"location": "https://1.0.0.1/loop"}
                ),
                "https://1.0.0.1/loop": httpx.Response(
                    307, headers={"location": "https://1.1.1.1/loop"}
                ),
            },
            "https://1.1.1.1/loop",
            max_redirects=2,
        )
