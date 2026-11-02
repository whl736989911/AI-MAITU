"""Inline image bytes leave state as workspace refs, not base64."""

from __future__ import annotations

import base64
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from octop.infra.agents.middleware.vision_offload import VisionStateOffloadMiddleware


class _Workspace:
    """Minimal workspace facade: only the two calls the middleware uses."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.uploads: list[tuple[str, int]] = []

    def exists(self, path: str) -> bool:
        return (self.root / path).is_file()

    def upload_bytes(self, path: str, data: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.uploads.append((path, len(data)))


def _anthropic_image(payload: bytes, media_type: str = "image/jpeg") -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(payload).decode("ascii"),
        },
    }


def _payload(size: int) -> bytes:
    # Over the 4 KiB floor, and not decodable as an image: offload must not care
    # about the format, only about bytes that would otherwise be copied per step.
    return b"\xff\xd8\xff\xe0" + b"\x01" * size


def test_inline_tool_image_becomes_a_path_ref(tmp_path: Path) -> None:
    workspace = _Workspace(tmp_path)
    middleware = VisionStateOffloadMiddleware(workspace=workspace)
    image = _payload(64 * 1024)
    message = ToolMessage(
        content=[{"type": "text", "text": "scan"}, _anthropic_image(image)],
        tool_call_id="tc1",
    )

    update = middleware.before_model({"messages": [message]}, None)

    assert update is not None
    blocks = update["messages"][0].content
    assert blocks[0] == {"type": "text", "text": "scan"}
    ref = blocks[1]
    assert ref["image_url"]["url"].startswith("workspace://")
    assert "base64" not in str(ref)
    assert len(workspace.uploads) == 1
    stored_rel, stored_size = workspace.uploads[0]
    assert stored_rel.startswith("inbound/vision-")
    assert stored_size == len(image)
    assert (tmp_path / stored_rel).read_bytes() == image


def test_same_image_twice_uploads_once(tmp_path: Path) -> None:
    workspace = _Workspace(tmp_path)
    middleware = VisionStateOffloadMiddleware(workspace=workspace)
    block = _anthropic_image(_payload(8 * 1024))
    first = HumanMessage(content=[block])
    second = AIMessage(content=[block])

    middleware.before_model({"messages": [first]}, None)
    middleware.before_model({"messages": [second]}, None)

    assert len(workspace.uploads) == 1


def test_small_or_already_ref_blocks_are_untouched(tmp_path: Path) -> None:
    workspace = _Workspace(tmp_path)
    middleware = VisionStateOffloadMiddleware(workspace=workspace)
    tiny = _anthropic_image(b"\x00" * 128)
    ref = {
        "type": "image_url",
        "workspace_path": "inbound/x.png",
        "mime_type": "image/png",
        "image_url": {"url": "workspace://inbound/x.png"},
    }
    message = HumanMessage(content=[tiny, ref, {"type": "text", "text": "hi"}])

    assert middleware.before_model({"messages": [message]}, None) is None
    assert workspace.uploads == []


def test_text_only_state_is_untouched(tmp_path: Path) -> None:
    middleware = VisionStateOffloadMiddleware(workspace=_Workspace(tmp_path))
    message = HumanMessage(content="plain text")

    assert middleware.before_model({"messages": [message]}, None) is None
