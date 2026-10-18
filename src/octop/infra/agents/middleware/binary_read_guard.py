"""Route read_file image reads to multimodal context; block other binary inputs."""

from __future__ import annotations

import io
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.i18n import tr
from octop.infra.gateway.media.attachment_hints import (
    VISION_MAX_BYTES,
    WORKSPACE_IMAGE_SCHEME,
    sniff_image_media_type,
)

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

logger = logging.getLogger(__name__)


def _request_locale() -> str:
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return "en"
    if not isinstance(configurable, dict):
        return "en"
    return str(configurable.get("octop_feature_locale") or configurable.get("locale") or "en")


_READ_FILE_TOOL = "read_file"
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_BLOCKED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".bin",
    }
)
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".csv",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".html",
        ".xml",
        ".yaml",
        ".yml",
        ".sql",
        ".sh",
    }
)


def _read_file_path_from_args(params: dict[str, Any]) -> str:
    for key in ("file_path", "path"):
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _extension(path: str) -> str:
    cleaned = path.split("?", 1)[0].split("#", 1)[0]
    return Path(cleaned).suffix.lower()


def _is_inbound_attachment(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return normalized.startswith("inbound/") or "/inbound/" in normalized


def read_file_block_reason(path: str) -> str | None:
    """Return a rejection message when *path* must not be read via ``read_file``."""
    if not path:
        return None
    ext = _extension(path)
    if ext == ".pdf":
        return "Use execute with the pdf skill (pdftotext, pdfplumber, etc.) instead."
    if ext in _BLOCKED_EXTENSIONS:
        return "Use execute or the matching skill instead."
    if (
        _is_inbound_attachment(path)
        and ext
        and (ext not in _TEXT_EXTENSIONS and ext not in _IMAGE_EXTENSIONS)
    ):
        return "Use execute or the matching skill instead."
    return None


class BinaryReadGuardMiddleware(AgentMiddleware[Any, Any]):
    """Preserve binary guards while exposing decodable image reads to vision models."""

    def __init__(self, *, workspace: BackendWorkspace) -> None:
        self._workspace = workspace

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        if tool_name != _READ_FILE_TOOL:
            return await handler(request)

        params = tool_call.get("args") or {}
        if not isinstance(params, dict):
            params = {}
        path = _read_file_path_from_args(params)
        reason = read_file_block_reason(path)
        if reason is not None:
            logger.info("BinaryReadGuard blocked read_file on %s", path)
            return ToolMessage(
                content=reason,
                tool_call_id=str(tool_call.get("id") or ""),
                status="error",
            )

        if _extension(path) not in _IMAGE_EXTENSIONS:
            return await handler(request)

        try:
            data = await self._workspace.adownload_bytes(path)
            if data is None:
                raise FileNotFoundError(path)
            if len(data) <= VISION_MAX_BYTES:
                from PIL import Image  # noqa: PLC0415

                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
        except Exception as exc:
            logger.info("BinaryReadGuard could not decode image %s: %s", path, exc)
            return ToolMessage(
                content=tr("agent_errors.image_read_failed", _request_locale(), path=path),
                tool_call_id=str(tool_call.get("id") or ""),
                status="error",
            )

        mime_type = sniff_image_media_type(data) or mimetypes.guess_type(path)[0] or "image/png"

        return ToolMessage(
            content=[
                {
                    "type": "text",
                    "text": tr("agent_errors.image_read_success", _request_locale(), path=path),
                },
                {
                    "type": "image_url",
                    "workspace_path": path,
                    "mime_type": mime_type,
                    "image_url": {"url": f"{WORKSPACE_IMAGE_SCHEME}{path}"},
                },
            ],
            tool_call_id=str(tool_call.get("id") or ""),
        )


__all__ = ["BinaryReadGuardMiddleware", "read_file_block_reason"]
