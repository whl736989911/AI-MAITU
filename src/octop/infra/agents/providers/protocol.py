"""Configuration helpers for trying the same model over another chat protocol."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from harness_agent.config import ProviderConfig


def alternate_protocol_config(provider: ProviderConfig) -> ProviderConfig:
    """Return a detached config for the other generic protocol.

    Only protocol-specific base URL routing is changed. Credentials, model
    metadata, headers, and other provider settings are retained without
    sharing mutable config containers with the primary provider.
    """
    if provider.protocol == "openai":
        protocol: Literal["openai", "anthropic"] = "anthropic"
    elif provider.protocol == "anthropic":
        protocol = "openai"
    else:
        raise ValueError(f"Cannot alternate unsupported protocol {provider.protocol!r}")

    base_url = _alternate_base_url(provider.base_url, provider.protocol)
    return replace(
        provider,
        protocol=protocol,
        base_url=base_url,
        headers=dict(provider.headers),
        models=[replace(model) for model in provider.models],
    )


def _alternate_base_url(base_url: str, source: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid provider base URL: {base_url!r}")

    path = parsed.path.rstrip("/")
    host = (parsed.hostname or "").lower()
    if host == "api.deepseek.com":
        if source == "openai" and path in ("", "/v1"):
            path = "/anthropic"
        elif source == "anthropic" and path == "/anthropic":
            path = "/v1"
    elif source == "openai" and path.endswith("/v1"):
        path = path[:-3]
    elif source == "anthropic" and not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"

    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
