"""tests/unit/test_tool_media.py"""

from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends import resolve_backend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.backend.resolver import default_agent_backend_spec
from octop.infra.gateway.media.backend_files import (
    ensure_workspace_media_path,
)
from octop.infra.gateway.media.constants import INBOUND_DIR, OUTBOUND_DIR
from octop.infra.gateway.media.ingress import AgentBackedMediaBackend
from octop.infra.gateway.media.tool_media import (
    attachment_frames_from_tool_result,
    enrich_media_block_preview,
    enrich_tool_result_with_backend,
    iter_media_blocks,
    media_events_from_tool_result,
)


def _workspace(root: str, *, virtual_mode: bool = False) -> BackendWorkspace:
    backend = LocalShellBackend(root_dir=root, virtual_mode=virtual_mode)
    return BackendWorkspace(backend, root)


def _default_virtual_workspace(root: str) -> BackendWorkspace:
    """BackendWorkspace matching Octop's platform default agent backend.

    POSIX keeps host-rooted shell access; harness scopes deepagents artifacts
    to the agent workspace. Windows scopes the local-shell root to the
    agent workspace so virtual-mode uploads stay on the same drive.
    """
    spec = default_agent_backend_spec(Path(root))
    return BackendWorkspace(resolve_backend(spec, workspace_dir=root), root)


def test_iter_media_blocks_supports_workbuddy_generation_envelopes() -> None:
    content = json.dumps(
        {
            "type": "image_gen_tool_result",
            "images": [
                {
                    "path": "generated/images/a.png",
                    "localPath": "/private/a.png",
                    "mediaType": "image/png",
                }
            ],
        }
    )

    assert iter_media_blocks(content) == [
        {
            "type": "image",
            "path": "generated/images/a.png",
            "filename": "a.png",
            "media_type": "image/png",
            "source": {
                "type": "url",
                "url": "generated/images/a.png",
                "media_type": "image/png",
            },
        }
    ]


@pytest.mark.asyncio
async def test_import_external_file_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as foreign_dir:
        external = Path(foreign_dir) / "shot.png"
        external.write_bytes(b"\x89PNG\r\n")
        workspace = _workspace(ws)
        rel = await ensure_workspace_media_path(
            workspace,
            external.as_uri(),
            filename="shot.png",
            mime="image/png",
        )
        assert rel is None
        assert not (Path(ws) / OUTBOUND_DIR).exists()


@pytest.mark.asyncio
async def test_import_external_file_virtual_mode_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as foreign_dir:
        external = Path(foreign_dir) / "harness-browser.png"
        external.write_bytes(b"\x89PNG\r\n")
        workspace = _default_virtual_workspace(ws)
        rel = await ensure_workspace_media_path(
            workspace,
            external.as_uri(),
            filename="harness-browser.png",
            mime="image/png",
        )
        assert rel is None
        assert not (Path(ws) / OUTBOUND_DIR).exists()


@pytest.mark.asyncio
async def test_attachment_frame_rejects_external_host_media() -> None:
    with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as foreign_dir:
        external = Path(foreign_dir) / "harness.png"
        external.write_bytes(b"IMG")
        workspace = _workspace(ws, virtual_mode=True)
        chunk = {
            "type": "tool_result",
            "messages": [
                {
                    "content": json.dumps(
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": external.as_uri(),
                                "media_type": "image/png",
                            },
                            "filename": "harness.png",
                        },
                    ),
                },
            ],
        }
        enriched = await enrich_tool_result_with_backend(
            chunk,
            agent_id="agent-1",
            workspace=workspace,
        )
        content = enriched["messages"][0]["content"]
        parsed = json.loads(content)
        assert "preview_url" not in parsed
        frames = [
            f
            async for f in attachment_frames_from_tool_result(
                enriched,
                agent_id="agent-1",
                workspace=workspace,
            )
        ]
        assert frames == []


@pytest.mark.asyncio
async def test_attachment_frame_uses_workspace_media_preview() -> None:
    with tempfile.TemporaryDirectory() as ws:
        external = Path(ws) / "outbound" / "harness.png"
        external.parent.mkdir()
        external.write_bytes(b"IMG")
        workspace = _workspace(ws, virtual_mode=False)
        chunk = {
            "type": "tool_result",
            "messages": [
                {
                    "content": json.dumps(
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": external.as_uri(),
                                "media_type": "image/png",
                            },
                            "filename": "harness.png",
                        },
                    ),
                },
            ],
        }
        enriched = await enrich_tool_result_with_backend(
            chunk,
            agent_id="agent-1",
            workspace=workspace,
        )
        frames = [
            f
            async for f in attachment_frames_from_tool_result(
                enriched,
                agent_id="agent-1",
                workspace=workspace,
            )
        ]
        assert len(frames) == 1
        assert frames[0]["type"] == "attachment"
        assert frames[0]["kind"] == "image"
        assert frames[0]["filename"] == "harness.png"
        assert frames[0]["mime_type"] == "image/png"
        assert "data" not in frames[0]
        assert frames[0]["preview_url"].startswith("/api/agents/agent-1/media/preview")
        assert "file%" in frames[0]["preview_url"] or "source=" in frames[0]["preview_url"]


def test_iter_media_blocks_single_object() -> None:
    blocks = iter_media_blocks('{"type": "image", "source": {"type": "url", "url": "https://x"}}')
    assert len(blocks) == 1


def test_iter_media_blocks_dict_content() -> None:
    block = {
        "type": "image",
        "source": {"type": "url", "url": "file:///tmp/shot.png", "media_type": "image/png"},
    }
    assert len(iter_media_blocks(block)) == 1


@pytest.mark.asyncio
async def test_enrich_send_file_dict_content() -> None:
    with tempfile.TemporaryDirectory() as ws:
        png = Path(ws) / "outbound" / "shot.png"
        png.parent.mkdir()
        png.write_bytes(b"\x89PNG\r\n")
        workspace = _workspace(ws, virtual_mode=True)
        chunk = {
            "type": "tool_result",
            "messages": [
                {
                    "content": {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": png.as_uri(),
                            "media_type": "image/png",
                        },
                        "filename": png.name,
                    },
                },
            ],
        }
        enriched = await enrich_tool_result_with_backend(
            chunk,
            agent_id="agent-1",
            workspace=workspace,
        )
        content = enriched["messages"][0]["content"]
        assert isinstance(content, dict)
        assert content.get("preview_url")
        assert content["source"]["url"] == content["preview_url"]
        assert content["filename"] == png.name
        assert not str(content.get("path") or "").startswith("/api/")
        assert "/home/" not in json.dumps(content, ensure_ascii=False)
        frames = [
            f
            async for f in attachment_frames_from_tool_result(
                enriched,
                agent_id="agent-1",
                workspace=workspace,
            )
        ]
        assert len(frames) == 1


def test_agent_backed_media_backend_inbound_fragments() -> None:
    from unittest.mock import MagicMock

    workspace = BackendWorkspace(MagicMock(), "/tmp/ws")
    media = AgentBackedMediaBackend(workspace)

    assert media._inbound_fragment("outbound/img.png") == "outbound/img.png"
    assert media._inbound_fragment("inbound/img.png") == "inbound/img.png"
    assert media._inbound_fragment("/outbound/img.png") == "outbound/img.png"
    assert media._inbound_fragment("img.png") == f"{INBOUND_DIR}/img.png"
    assert media._inbound_fragment("img.png") != f"{OUTBOUND_DIR}/img.png"


def testenrich_media_block_preview_outbound() -> None:
    block = {
        "type": "image",
        "source": {
            "type": "url",
            "url": "file:///tmp/workspace/outbound/chart.png",
            "media_type": "image/png",
        },
        "filename": "chart.png",
        "path": "/tmp/workspace/outbound/chart.png",
    }
    enriched = enrich_media_block_preview(block, agent_id="agent-x")
    assert enriched["preview_url"].startswith("/api/agents/agent-x/media/preview?")
    assert enriched["source"]["url"] == enriched["preview_url"]
    assert enriched["filename"] == "chart.png"


@pytest.mark.asyncio
async def test_enrich_send_file_normalizes_owned_absolute_path() -> None:
    """An owned absolute file path becomes a workspace key without copying."""
    with tempfile.TemporaryDirectory() as ws:
        workspace = _workspace(ws, virtual_mode=False)
        generated = Path(ws) / "generated" / "water-ppt"
        generated.mkdir(parents=True)
        pptx = generated / "保护地球节约用水.pptx"
        pptx.write_bytes(b"PKDATA")
        abs_path = str(pptx.resolve())
        chunk = {
            "type": "tool_result",
            "messages": [
                {
                    "content": {
                        "type": "file",
                        "source": {
                            "type": "url",
                            "url": Path(abs_path).as_uri(),
                            "media_type": (
                                "application/vnd.openxmlformats-officedocument"
                                ".presentationml.presentation"
                            ),
                        },
                        "filename": "保护地球节约用水.pptx",
                    },
                },
            ],
        }
        enriched = await enrich_tool_result_with_backend(
            chunk,
            agent_id="main",
            workspace=workspace,
        )
        content = enriched["messages"][0]["content"]
        assert isinstance(content, dict)
        assert content["type"] == "file"
        assert content.get("path") == "generated/water-ppt/保护地球节约用水.pptx"
        assert content["filename"] == "保护地球节约用水.pptx"
        assert "preview_url" not in content
        assert "source" not in content
        outbound = Path(ws) / "outbound"
        assert not outbound.exists() or list(outbound.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", ["outbound", "inbound"])
async def test_send_file_virtual_path_preserves_live_and_history_cards(
    tmp_path: Path, directory: str
) -> None:
    workspace = _workspace(str(tmp_path), virtual_mode=True)
    document = tmp_path / directory / "修改意见.docx"
    document.parent.mkdir()
    document.write_bytes(b"PK-document")
    block = {
        "type": "file",
        "path": f"/{directory}/{document.name}",
        "source": {
            "type": "url",
            "url": document.as_uri(),
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        "filename": document.name,
    }
    chunk = {"type": "tool_result", "messages": [{"content": block}]}
    enriched = await enrich_tool_result_with_backend(chunk, agent_id="agent-1", workspace=workspace)
    key = f"{directory}/{document.name}"
    assert enriched["messages"][0]["content"]["path"] == key
    assert enrich_media_block_preview(block, agent_id="agent-1")["path"] == key
    for result in (chunk, enriched):
        frames = [
            frame
            async for frame in attachment_frames_from_tool_result(
                result, agent_id="agent-1", workspace=workspace
            )
        ]
        assert len(frames) == 1
        assert frames[0]["kind"] == "file"
        assert frames[0]["filename"] == document.name
        assert frames[0]["path"] == key
        assert await workspace.adownload_bytes(frames[0]["path"]) == b"PK-document"


@pytest.mark.asyncio
async def test_send_file_falls_back_to_owned_materialized_url(tmp_path: Path) -> None:
    workspace = _workspace(str(tmp_path), virtual_mode=True)
    document = tmp_path / "generated" / "report.docx"
    document.parent.mkdir()
    document.write_bytes(b"PK-generated-document")
    chunk = {
        "type": "tool_result",
        "messages": [
            {
                "content": {
                    "type": "file",
                    "path": "/generated/report.docx",
                    "source": {"type": "url", "url": document.as_uri()},
                    "filename": document.name,
                }
            }
        ],
    }
    enriched = await enrich_tool_result_with_backend(chunk, agent_id="agent-1", workspace=workspace)
    assert enriched["messages"][0]["content"]["path"] == "generated/report.docx"
    frames = [
        frame
        async for frame in attachment_frames_from_tool_result(
            enriched, agent_id="agent-1", workspace=workspace
        )
    ]
    assert len(frames) == 1
    assert await workspace.adownload_bytes(frames[0]["path"]) == b"PK-generated-document"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["relative", "absolute", "file_url"])
async def test_send_file_keeps_nested_outbound_file_not_same_name_shadow(
    tmp_path: Path, shape: str
) -> None:
    from urllib.parse import parse_qs, urlsplit

    workspace = _workspace(str(tmp_path), virtual_mode=True)
    document = tmp_path / "reports" / "outbound" / "report.docx"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"PK-correct-nested-document")
    shadow = tmp_path / "outbound" / document.name
    shadow.parent.mkdir()
    shadow.write_bytes(b"PK-wrong-shadow")
    key = "reports/outbound/report.docx"
    path = (
        key if shape == "relative" else str(document) if shape == "absolute" else document.as_uri()
    )
    chunk = {
        "type": "tool_result",
        "messages": [{"content": {"type": "file", "path": path, "filename": document.name}}],
    }
    enriched = await enrich_tool_result_with_backend(chunk, agent_id="agent-1", workspace=workspace)
    assert enriched["messages"][0]["content"]["path"] == key
    frames = [
        frame
        async for frame in attachment_frames_from_tool_result(
            enriched, agent_id="agent-1", workspace=workspace
        )
    ]
    assert len(frames) == 1
    download_key = parse_qs(urlsplit(frames[0]["url"]).query)["path"][0]
    assert download_key == key
    assert await workspace.adownload_bytes(download_key) == b"PK-correct-nested-document"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["absolute", "traversal", "network"])
async def test_send_file_rejects_foreign_candidates_even_with_local_shadow(
    tmp_path: Path, shape: str
) -> None:
    root = tmp_path / "current"
    root.mkdir()
    workspace = _workspace(str(root), virtual_mode=True)
    foreign = tmp_path / "foreign" / "outbound" / "report.docx"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"PK-private-foreign-document")
    shadow = root / "outbound" / foreign.name
    shadow.parent.mkdir()
    shadow.write_bytes(b"PK-local-shadow")
    path = {
        "absolute": str(foreign),
        "traversal": "../foreign/outbound/report.docx",
        "network": "//foreign/outbound/report.docx",
    }[shape]
    chunk = {
        "type": "tool_result",
        "messages": [
            {
                "content": {
                    "type": "file",
                    "path": path,
                    "source": {"type": "url", "url": foreign.as_uri()},
                    "filename": foreign.name,
                }
            }
        ],
    }
    enriched = await enrich_tool_result_with_backend(chunk, agent_id="agent-1", workspace=workspace)
    assert "path" not in enriched["messages"][0]["content"]
    for result in (chunk, enriched):
        assert [
            frame
            async for frame in attachment_frames_from_tool_result(
                result, agent_id="agent-1", workspace=workspace
            )
        ] == []


@pytest.mark.asyncio
async def test_enrich_send_file_rewrites_path_to_dashboard_api() -> None:
    """Host path from send_file must become workspace/download API for dashboard."""
    with tempfile.TemporaryDirectory() as ws:
        workspace = _workspace(ws, virtual_mode=False)
        outbound = Path(ws) / "outbound"
        outbound.mkdir()
        pptx = outbound / "1783513904_地球介绍.pptx"
        pptx.write_bytes(b"PKDATA")
        chunk = {
            "type": "tool_result",
            "messages": [
                {
                    "content": {
                        "type": "file",
                        "source": {
                            "type": "url",
                            "url": pptx.as_uri(),
                            "media_type": (
                                "application/vnd.openxmlformats-officedocument"
                                ".presentationml.presentation"
                            ),
                        },
                        "filename": "地球介绍.pptx",
                        "path": "outbound/1783513904_地球介绍.pptx",
                    },
                },
            ],
        }
        enriched = await enrich_tool_result_with_backend(
            chunk,
            agent_id="Y9F9E6",
            workspace=workspace,
        )
        content = enriched["messages"][0]["content"]
        assert isinstance(content, dict)
        assert "preview_url" not in content
        assert "source" not in content
        assert content["path"] == "outbound/1783513904_地球介绍.pptx"
        assert content["filename"] == "地球介绍.pptx"
        assert content.get("media_type", "").endswith("presentation")
        assert "/home/" not in json.dumps(content, ensure_ascii=False)
        assert not str(content["path"]).startswith("/api/")


def test_dashboard_media_url_keeps_authenticated_agent_id() -> None:
    from octop.infra.gateway.media.backend_files import dashboard_media_url

    path = "file:///Users/me/.octop/agents/W4MFVJ/outbound/screenshots/harness.png"
    url = dashboard_media_url("6X3Z7C", path)
    assert url is not None
    assert url.startswith("/api/agents/6X3Z7C/media/preview?")
    assert "file%" in url or "source=" in url


@pytest.mark.asyncio
async def test_resolve_preview_keeps_host_absolute_screenshot() -> None:
    """Browser screenshots under agent outbound/ must load via absolute path."""
    from deepagents.backends.local_shell import LocalShellBackend
    from harness_agent.backends.workspace import BackendWorkspace

    from octop.infra.gateway.media.backend_files import resolve_preview_payload

    with tempfile.TemporaryDirectory() as ws:
        shots = Path(ws) / "outbound" / "screenshots"
        shots.mkdir(parents=True)
        png = shots / "harness-abs.png"
        png.write_bytes(b"\x89PNG\r\n" + b"x" * 64)
        backend = LocalShellBackend(root_dir="/", virtual_mode=True)
        workspace = BackendWorkspace(backend, ws)
        # Tool returned an absolute file:// path — pass it through unchanged.
        payload = await resolve_preview_payload(
            source=png.as_uri(),
            workspace=workspace,
            mime_hint="image/png",
        )
        assert payload is not None
        data, mime = payload
        assert data.startswith(b"\x89PNG")
        assert mime == "image/png"


@pytest.mark.asyncio
async def test_enrich_plain_text_screenshot_output() -> None:
    from octop.infra.gateway.media.tool_media import enrich_tool_output_string

    text = (
        "Screenshot saved to /Users/me/.octop/agents/A1/outbound/screenshots/harness.png "
        "(116 KB, 1440x900)"
    )
    enriched = await enrich_tool_output_string(
        text,
        agent_id="A1",
        workspace=None,
    )
    assert enriched != text
    parsed = json.loads(enriched)
    assert isinstance(parsed, list)
    assert parsed[0]["type"] == "text"
    assert parsed[1]["type"] == "image"
    assert parsed[1]["preview_url"].startswith("/api/agents/A1/")


@pytest.mark.parametrize("prefix", ["", "See ", '"', "'", "(", "\n"])
def test_plain_text_outbound_image_at_token_boundary(prefix: str) -> None:
    from octop.infra.gateway.media.tool_media import _plain_text_image_path

    path = "/workspace/outbound/screenshots/chart.PNG"
    assert _plain_text_image_path(f"{prefix}{path})") == path


def test_plain_text_image_scan_handles_long_web_output() -> None:
    from octop.infra.gateway.media.tool_media import enrich_tool_output_string_sync

    # A long URL/encoded token without an image used to stall the event loop.
    text = "Fetched page: https://example.com/" + "a" * 100_000
    started = time.perf_counter()
    assert enrich_tool_output_string_sync(text, agent_id="A1") == text
    assert time.perf_counter() - started < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["source", "top_level"])
async def test_inline_files_keep_downloadable_bytes_without_overwriting_same_name(
    tmp_path: Path, shape: str
) -> None:
    workspace = _workspace(str(tmp_path), virtual_mode=True)
    paths = []
    for data in (b"%PDF-first-document", b"%PDF-second-document"):
        payload = base64.b64encode(data).decode("ascii")
        block = {"type": "file", "filename": "修改意见.pdf", "mime_type": "application/pdf"}
        if shape == "source":
            block["source"] = {"type": "base64", "data": payload, "media_type": "application/pdf"}
        else:
            block["base64"] = payload
        chunk = {"type": "tool_result", "messages": [{"content": block}]}
        enriched = await enrich_tool_result_with_backend(
            chunk, agent_id="agent-1", workspace=workspace
        )
        frames = [
            frame
            async for frame in attachment_frames_from_tool_result(
                enriched, agent_id="agent-1", workspace=workspace
            )
        ]
        assert len(frames) == 1
        assert frames[0]["filename"] == "修改意见.pdf"
        assert await workspace.adownload_bytes(frames[0]["path"]) == data
        paths.append(frames[0]["path"])
    assert paths[0] != paths[1]
    assert await workspace.adownload_bytes(paths[0]) == b"%PDF-first-document"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "image", "video", "audio"])
@pytest.mark.parametrize("shape", ["relative", "virtual"])
async def test_im_workspace_media_sends_exact_bytes(tmp_path: Path, kind: str, shape: str) -> None:
    workspace = _workspace(str(tmp_path), virtual_mode=True)
    key = "reports/outbound/结果.pdf" if shape == "relative" else "outbound/结果.pdf"
    data = b"correct-workspace-media"
    await workspace.aupload_bytes(key, data)
    path = key if shape == "relative" else f"/{key}"
    chunk = {
        "type": "tool_result",
        "messages": [
            {
                "content": {
                    "type": kind,
                    "source": {"type": "url", "url": path},
                    "filename": "结果.pdf",
                }
            }
        ],
    }
    events = [event async for event in media_events_from_tool_result(chunk, workspace=workspace)]
    assert len(events) == 1
    part = events[0].content[0]
    assert base64.b64decode(part.data) == data
    assert not part.url


@pytest.mark.asyncio
async def test_im_file_rejects_foreign_file_even_with_same_name_shadow(tmp_path: Path) -> None:
    root = tmp_path / "current"
    root.mkdir()
    workspace = _workspace(str(root), virtual_mode=True)
    foreign = tmp_path / "foreign" / "outbound" / "report.pdf"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign-private-data")
    await workspace.aupload_bytes("outbound/report.pdf", b"local-shadow")
    for path in ("../foreign/outbound/report.pdf", foreign.as_uri()):
        chunk = {
            "type": "tool_result",
            "messages": [{"content": {"type": "file", "source": {"type": "url", "url": path}}}],
        }
        assert [
            event async for event in media_events_from_tool_result(chunk, workspace=workspace)
        ] == []
