"""The DingTalk card title must not expose the upstream project name."""

from __future__ import annotations

import json
from typing import Any

from octop_gateway.channels import BUILTIN_CHANNELS
from octop_gateway.channels.dingtalk import DingTalkConfig

from octop.infra.gateway.channels import dingtalk_branding
from octop.infra.gateway.channels.dingtalk_branding import (
    BrandedDingTalkChannel,
    card_title,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class _PostContext:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _Response:
        return _Response(self._payload)

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Http:
    closed = False

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def post(self, _url: str, **kwargs: Any) -> _PostContext:
        self.payloads.append(kwargs.get("json") or {})
        return _PostContext({"errcode": 0})


def _channel() -> tuple[BrandedDingTalkChannel, _Http]:
    channel = BrandedDingTalkChannel(
        None,  # type: ignore[arg-type]
        DingTalkConfig(app_key="app-key", app_secret="app-secret"),
    )
    http = _Http()
    channel._http = http  # type: ignore[assignment]
    return channel, http


async def test_webhook_markdown_carries_the_product_name() -> None:
    channel, http = _channel()
    await channel._send_via_webhook("https://example.invalid/robot", "hello")

    assert http.payloads
    assert http.payloads[0]["markdown"]["title"] == card_title() == "麦途"


async def test_openapi_markdown_carries_the_product_name() -> None:
    channel, http = _channel()

    async def _headers() -> dict[str, str]:
        return {}

    channel._get_auth_headers = _headers  # type: ignore[method-assign]
    await channel._send_via_openapi("user-1", "hello", {})

    assert http.payloads
    message = json.loads(http.payloads[0]["msgParam"])
    assert message["title"] == "麦途"


async def test_long_webhook_text_stays_plain() -> None:
    channel, http = _channel()
    await channel._send_via_webhook("https://example.invalid/robot", "x" * 4000)

    assert http.payloads[0]["msgtype"] == "text"
    assert "markdown" not in http.payloads[0]


def test_install_pins_the_branded_channel() -> None:
    original = BUILTIN_CHANNELS["dingtalk"]
    loaded = getattr(BUILTIN_CHANNELS, "_loaded", False)
    try:
        dingtalk_branding.install()
        assert BUILTIN_CHANNELS["dingtalk"] is BrandedDingTalkChannel
        # A later lazy load must not overwrite the override.
        BUILTIN_CHANNELS._ensure_loaded()  # type: ignore[attr-defined]
        assert BUILTIN_CHANNELS["dingtalk"] is BrandedDingTalkChannel
    finally:
        BUILTIN_CHANNELS["dingtalk"] = original
        BUILTIN_CHANNELS._loaded = loaded  # type: ignore[attr-defined]
