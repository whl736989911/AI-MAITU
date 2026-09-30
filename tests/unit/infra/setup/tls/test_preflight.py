"""Unit tests for TLS preflight checks."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from octop.config import OctopConfig, TlsConfig
from octop.infra.setup.tls.preflight import run_preflight


def test_public_ip_probe_skips_unusable_endpoints(monkeypatch):
    from octop.infra.setup.tls import preflight

    seen = []
    original_client = httpx.Client

    def respond(request):
        seen.append(str(request.url))
        if request.url.path == "/private":
            return httpx.Response(200, text="10.0.0.2")
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        return httpx.Response(200, text="address: 1.1.1.1")

    monkeypatch.setattr(
        preflight.httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            timeout=kwargs["timeout"],
            follow_redirects=kwargs["follow_redirects"],
        ),
    )
    urls = (
        "https://example.com/private",
        "https://example.com/redirect",
        "https://example.com/public",
    )
    assert preflight._fetch_public_ip_from_urls(urls, timeout=1) == "1.1.1.1"
    assert seen == list(urls)


def test_preflight_requires_domain():
    result = run_preflight("", OctopConfig())
    assert not result.ok
    assert any(c.id == "domain" and not c.ok for c in result.checks)


def test_preflight_bind_host_and_port(tmp_path):
    cfg = OctopConfig(bind_host="127.0.0.1", port=8088)
    with (
        patch("octop.infra.setup.tls.preflight._port_available", return_value=True),
        patch("octop.infra.setup.tls.preflight._fetch_public_ip", return_value="203.0.113.1"),
        patch("octop.infra.setup.tls.preflight._resolve_domain_ips", return_value={"203.0.113.1"}),
    ):
        result = run_preflight("octop.example.com", cfg)
    assert not result.ok
    ids = {c.id for c in result.checks}
    assert "bind_host" in ids
    assert "port" in ids
    bind = next(c for c in result.checks if c.id == "bind_host")
    port = next(c for c in result.checks if c.id == "port")
    assert not bind.ok
    assert not port.ok


def test_preflight_ok_when_all_match():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=80,
        tls=TlsConfig(enabled=False),
    )
    with (
        patch("octop.infra.setup.tls.preflight._port_available", return_value=True),
        patch("octop.infra.setup.tls.preflight._fetch_public_ip", return_value="203.0.113.1"),
        patch("octop.infra.setup.tls.preflight._resolve_domain_ips", return_value={"203.0.113.1"}),
    ):
        result = run_preflight("octop.example.com", cfg)
    assert result.ok
    assert all(c.ok for c in result.checks)


def test_preflight_rejects_when_tls_already_enabled():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=8088,
        tls=TlsConfig(enabled=True),
    )
    with (
        patch("octop.infra.setup.tls.preflight._port_available", return_value=True),
        patch("octop.infra.setup.tls.preflight._fetch_public_ip", return_value="203.0.113.1"),
        patch("octop.infra.setup.tls.preflight._resolve_domain_ips", return_value={"203.0.113.1"}),
    ):
        result = run_preflight("octop.example.com", cfg)
    assert not result.ok
    assert any(c.id == "tls_enabled" and not c.ok for c in result.checks)


def test_preflight_renewal_mode_dual_port():
    cfg = OctopConfig(
        bind_host="0.0.0.0",
        port=443,
        tls=TlsConfig(enabled=True, http_port=80),
    )
    with (
        patch("octop.infra.setup.tls.preflight._fetch_public_ip", return_value="203.0.113.1"),
        patch("octop.infra.setup.tls.preflight._resolve_domain_ips", return_value={"203.0.113.1"}),
    ):
        result = run_preflight("octop.example.com", cfg)
    assert result.renewal
    assert result.ok
    assert any(c.id == "dual_port" and c.ok for c in result.checks)
