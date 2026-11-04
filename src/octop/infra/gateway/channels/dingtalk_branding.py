"""Brand the DingTalk message-card title owned by octop-gateway.

The runtime hard-codes ``"title": "Octop"`` in the Markdown payloads it sends
to DingTalk, so channel users would see the upstream project name. Until the
runtime exposes the title as configuration, register a thin subclass that only
changes that literal; payload shape, error handling and long-text behaviour stay
identical to the upstream implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from octop_gateway.channels import BUILTIN_CHANNELS
from octop_gateway.channels.dingtalk import _API_BASE, DingTalkChannel

from octop.branding import platform_short_name

logger = logging.getLogger(__name__)


def card_title() -> str:
    """The product name shown as the DingTalk card title."""
    return platform_short_name("zh") or "麦途"


class BrandedDingTalkChannel(DingTalkChannel):
    """DingTalk channel whose Markdown cards carry the configured product name."""

    async def _send_via_webhook(self, webhook_url: str, text: str) -> None:
        """Send a Markdown message via DingTalk robot webhook (reply mode)."""
        http = await self._ensure_http()
        if len(text) > 3500:
            payload: dict[str, Any] = {
                "msgtype": "text",
                "text": {"content": text},
            }
        else:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": card_title(),
                    "text": text,
                },
            }

        try:
            async with http.post(webhook_url, json=payload) as resp:
                data = await resp.json()
                errcode = data.get("errcode", 0)
                if errcode != 0:
                    logger.error(
                        "DingTalk webhook send failed: errcode=%s errmsg=%s",
                        errcode,
                        data.get("errmsg", "unknown"),
                    )
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("HTTP error sending DingTalk webhook message")

    async def _send_via_openapi(self, subject_id: str, text: str, meta: dict[str, Any]) -> None:
        """Send a single-chat (DM) message via DingTalk OpenAPI."""
        del meta
        http = await self._ensure_http()
        headers = await self._get_auth_headers()
        url = f"{_API_BASE}/v1.0/robot/oToMessages/batchSend"

        if len(text) > 3500:
            msg_key = "sampleText"
            msg_param = {"content": text}
        else:
            msg_key = "sampleMarkdown"
            msg_param = {"title": card_title(), "text": text}

        payload = {
            "robotCode": self._config.effective_robot_code,
            "userIds": [subject_id],
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param),
        }

        try:
            async with http.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()
                if "processQueryKey" not in data and "requestId" not in data:
                    logger.error("DingTalk OpenAPI send failed: %s", data)
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("HTTP error sending DingTalk OpenAPI message")


def install() -> None:
    """Register the branded channel for every later DingTalk channel creation.

    The runtime registry is lazy: loading it later would re-register the
    upstream class and undo the override. Marking it loaded keeps per-kind
    lazy loading intact while pinning this one kind to the branded subclass.
    """
    BUILTIN_CHANNELS["dingtalk"] = BrandedDingTalkChannel
    BUILTIN_CHANNELS._loaded = True  # type: ignore[attr-defined]


__all__ = ["BrandedDingTalkChannel", "card_title", "install"]
