from __future__ import annotations

from octop.config import OctopConfig, TlsConfig
from octop.infra.connectors.builder import (
    _build_gateway_spec,
    internal_mcp_url,
)
from octop.infra.connectors.catalog import get_catalog_entry


def test_internal_mcp_uses_plain_http_without_listener_tls() -> None:
    config = OctopConfig(bind_host="0.0.0.0", port=8088)

    url = internal_mcp_url(
        config=config,
        gateway_kind="qq-mail",
        instance_id="instance-1",
        internal_token="secret token",
    )

    assert url == "http://127.0.0.1:8088/api/internal/mcp/qq-mail/instance-1?token=secret%20token"


def test_internal_mcp_uses_loopback_https_when_listener_tls_is_ready() -> None:
    config = OctopConfig(
        bind_host="0.0.0.0",
        port=443,
        tls=TlsConfig(enabled=True, cert_file="ssl/fullchain.pem", key_file="ssl/privkey.pem"),
    )
    entry = get_catalog_entry("qq-mail")
    assert entry is not None

    spec = _build_gateway_spec(
        entry,
        "instance-1",
        {"internal_token": "secret"},
        config,
    )
    assert spec["url"].startswith("https://127.0.0.1:443/")
