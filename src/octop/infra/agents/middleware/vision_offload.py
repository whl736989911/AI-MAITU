"""Keep inline image bytes out of graph state before every model call.

The harness offload middleware already handles the OpenAI-style shapes
(``{"type": "image", "base64": ...}`` and ``image_url`` data URIs). Blocks that
arrive in the Anthropic shape — ``{"type": "image", "source": {"type":
"base64", "media_type": ..., "data": ...}}`` — are not recognised, so their
base64 stayed in the ``messages`` channel. LangGraph re-serializes that whole
channel on every step, so one 1.1 MB image became megabytes of checkpoints per
turn (the disk-full incident).

This middleware rewrites those blocks to path-only refs backed by a
content-addressed file under ``inbound/``; ``WorkspaceImageMaterializeMiddleware``
expands the ref back to bytes on the model request only, so the model still sees
the image.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage

from octop.infra.gateway.media.attachment_hints import (
    is_workspace_image_ref,
    ref_for_inline_image_bytes,
)

logger = logging.getLogger(__name__)

MIN_INLINE_BYTES = 4 * 1024
_IMAGE_TYPES = frozenset({"image", "image_url", "input_image"})


def _image_bytes(block: object) -> tuple[bytes, str] | None:
    """Return ``(bytes, mime)`` for an inline Anthropic-shaped image block."""
    if not isinstance(block, dict) or str(block.get("type") or "") not in _IMAGE_TYPES:
        return None
    source = block.get("source")
    if isinstance(source, dict):
        if str(source.get("type") or "") != "base64":
            return None
        payload = source.get("data")
        mime = str(source.get("media_type") or block.get("mime_type") or "image/png")
    else:
        payload = block.get("base64")
        mime = str(block.get("mime_type") or "image/png")
    if not isinstance(payload, str) or not payload:
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < MIN_INLINE_BYTES:
        return None
    return raw, mime


class VisionStateOffloadMiddleware(AgentMiddleware[Any, Any]):
    """Replace inline image bytes in state with workspace refs."""

    def __init__(self, workspace: Any) -> None:
        super().__init__()
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "VisionStateOffloadMiddleware"

    def before_model(self, state: Any, runtime: Any = None) -> dict[str, Any] | None:
        try:
            messages = list((state or {}).get("messages") or [])
        except AttributeError:
            return None
        rewritten: list[BaseMessage] = []
        changed = False
        for message in messages:
            content = getattr(message, "content", None)
            if not isinstance(content, list):
                rewritten.append(message)
                continue
            blocks: list[Any] = []
            message_changed = False
            for block in content:
                replacement = self._offload(block)
                if replacement is not None:
                    blocks.append(replacement)
                    message_changed = True
                else:
                    blocks.append(block)
            if message_changed:
                rewritten.append(message.model_copy(update={"content": blocks}))
                changed = True
            else:
                rewritten.append(message)
        return {"messages": rewritten} if changed else None

    async def abefore_model(self, state: Any, runtime: Any = None) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def _offload(self, block: Any) -> dict[str, Any] | None:
        if is_workspace_image_ref(block):
            return None
        parsed = _image_bytes(block)
        if parsed is None:
            return None
        raw, mime = parsed
        ref = ref_for_inline_image_bytes(raw, mime, workspace=self._workspace)
        if ref is None:
            return None
        logger.info("offloaded inline image (%d bytes) to %s", len(raw), ref["workspace_path"])
        return ref
