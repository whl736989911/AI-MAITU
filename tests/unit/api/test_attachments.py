"""Unit tests for chat attachment workspace paths."""

from __future__ import annotations

import asyncio
import re
import tempfile
from threading import Event

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_gateway.models import FileContent, InboundMessage
from octop_harness.backends.workspace import BackendWorkspace

from octop.api.common.attachments import dashboard_inbound_preview_url, save_attachment
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.media.attachment_hints import (
    hints_from_content_parts,
    sniff_image_media_type,
)
from octop.infra.gateway.media.inbound_store import (
    inbound_extension,
    validate_inbound_media_type,
    validate_inbound_size,
)
from octop.infra.gateway.media.ingress import AgentBackedMediaBackend
from octop.infra.gateway.process.harness_request import build_content_from_message


def _workspace(root: str) -> BackendWorkspace:
    backend = LocalShellBackend(root_dir=root, virtual_mode=False)
    return BackendWorkspace(backend, root)


def _observe_waiting_upload(monkeypatch, inbound_store, progress):
    register = getattr(inbound_store, "_register_inbound_write_lock", None)
    if register is None:
        return

    def observed_register(*args, **kwargs):
        key, lock = register(*args, **kwargs)

        class ObservedLock:
            async def acquire(self):
                task = asyncio.current_task()
                if task is not None and task.get_name() == "second-upload" and lock.locked():
                    progress.set()
                return await lock.acquire()

            def release(self):
                lock.release()

            async def __aenter__(self):
                await self.acquire()
                return self

            async def __aexit__(self, *args):
                self.release()

        return key, ObservedLock()

    monkeypatch.setattr(inbound_store, "_register_inbound_write_lock", observed_register)


def test_inbound_extension_from_filename() -> None:
    assert inbound_extension("report.pdf", "application/pdf") == ".pdf"
    assert inbound_extension("data.PDF", "application/pdf") == ".pdf"


def test_inbound_extension_from_media_type() -> None:
    assert inbound_extension("upload", "application/pdf") == ".pdf"


def test_inbound_extension_fallback_bin() -> None:
    assert inbound_extension("upload", "application/octet-stream") == ".bin"


def test_sniff_png_from_clipboard_blob() -> None:
    from octop.api.routers.uploads import _resolve_media_type

    png = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
    assert sniff_image_media_type(png) == "image/png"
    assert _resolve_media_type("blob", "application/octet-stream", png) == "image/png"


def test_unknown_mime_falls_back_to_extension() -> None:
    assert (
        validate_inbound_media_type("application/x-zip-compressed", "bundle.zip")
        == "application/zip"
    )
    assert validate_inbound_media_type("application/x-empty", "data.csv") == "text/csv"
    assert validate_inbound_media_type("image/x-png", "photo.png") == "image/png"


def test_programmer_extensions_are_accepted() -> None:
    assert validate_inbound_media_type("video/mp2t", "app.ts") == "text/x-typescript"
    assert validate_inbound_media_type("application/x-sh", "setup.sh") == "text/x-shellscript"
    assert validate_inbound_media_type("text/x-python", "main.py") == "text/x-python"
    assert validate_inbound_media_type("foo/bar", "query.sql") == "application/sql"


def test_office_image_and_archive_extensions_are_accepted() -> None:
    assert validate_inbound_media_type("foo/bar", "notes.doc") == "application/msword"
    assert validate_inbound_media_type("foo/bar", "notes.rtf") == "application/rtf"
    assert validate_inbound_media_type("foo/bar", "book.epub") == "application/epub+zip"
    assert validate_inbound_media_type("foo/bar", "map.xmind") == "application/vnd.xmind.workbook"
    assert validate_inbound_media_type("foo/bar", "photo.heic") == "image/heic"
    assert validate_inbound_media_type("foo/bar", "photo.bmp") == "image/bmp"
    assert validate_inbound_media_type("foo/bar", "bundle.7z") == "application/x-7z-compressed"
    assert validate_inbound_media_type("foo/bar", "bundle.rar") == "application/vnd.rar"
    assert validate_inbound_media_type("foo/bar", "bundle.tgz") == "application/gzip"


def test_video_audio_and_executable_extensions_are_accepted() -> None:
    assert validate_inbound_media_type("foo/bar", "clip.mp4") == "video/mp4"
    assert validate_inbound_media_type("foo/bar", "clip.webm") == "video/webm"
    assert validate_inbound_media_type("foo/bar", "clip.mov") == "video/quicktime"
    assert validate_inbound_media_type("foo/bar", "clip.mkv") == "video/x-matroska"
    assert validate_inbound_media_type("foo/bar", "track.mp3") == "audio/mpeg"
    assert validate_inbound_media_type("foo/bar", "track.wav") == "audio/wav"
    assert validate_inbound_media_type("foo/bar", "track.aac") == "audio/aac"
    assert validate_inbound_media_type("foo/bar", "track.flac") == "audio/flac"
    assert validate_inbound_media_type("foo/bar", "setup.exe") == "application/x-msdownload"
    assert validate_inbound_media_type("foo/bar", "disk.dmg") == "application/x-apple-diskimage"
    assert (
        validate_inbound_media_type("foo/bar", "app.apk")
        == "application/vnd.android.package-archive"
    )


def test_video_audio_and_executable_mime_types_are_accepted() -> None:
    assert validate_inbound_media_type("video/mp4", "blob") == "video/mp4"
    assert validate_inbound_media_type("video/webm", "blob") == "video/webm"
    assert validate_inbound_media_type("video/quicktime", "blob") == "video/quicktime"
    assert validate_inbound_media_type("audio/mpeg", "blob") == "audio/mpeg"
    assert validate_inbound_media_type("audio/x-wav", "blob") == "audio/wav"
    assert validate_inbound_media_type("application/x-msdownload", "setup") == (
        "application/x-msdownload"
    )
    assert (
        validate_inbound_media_type(
            "application/vnd.android.package-archive",
            "app",
        )
        == "application/vnd.android.package-archive"
    )


def test_media_type_aliases_are_canonicalized() -> None:
    assert validate_inbound_media_type("audio/mp3", "blob") == "audio/mpeg"
    assert validate_inbound_media_type("application/javascript", "app.js") == "text/javascript"
    assert validate_inbound_media_type("application/x-zip-compressed", "bundle.zip") == (
        "application/zip"
    )


def test_octet_stream_with_known_extension_uses_extension_mime() -> None:
    assert validate_inbound_media_type("application/octet-stream", "clip.mp4") == "video/mp4"
    assert validate_inbound_media_type("application/octet-stream", "blob") == (
        "application/octet-stream"
    )


def test_av_mime_prefixes_are_accepted() -> None:
    assert validate_inbound_media_type("video/x-msvideo", "blob") == "video/x-msvideo"
    assert validate_inbound_media_type("audio/ogg", "blob") == "audio/ogg"
    assert validate_inbound_media_type("video/mp2t", "app.ts") == "text/x-typescript"


def test_dashboard_preview_url_uses_media_preview_for_av() -> None:
    image = dashboard_inbound_preview_url("a1", "inbound/x.png", media_type="image/png")
    audio = dashboard_inbound_preview_url("a1", "inbound/x.mp3", media_type="audio/mpeg")
    video = dashboard_inbound_preview_url("a1", "inbound/x.mp4", media_type="video/mp4")
    pdf = dashboard_inbound_preview_url("a1", "inbound/x.pdf", media_type="application/pdf")
    assert "/media/preview?" in image
    assert "/media/preview?" in audio
    assert "/media/preview?" in video
    assert "/workspace/download?" in pdf


def test_unknown_mime_without_known_extension_is_rejected() -> None:
    with pytest.raises(OctopError) as exc_info:
        validate_inbound_media_type("application/x-zip-compressed", "bundle")
    assert exc_info.value.code is ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE
    # ``details.reason`` survives i18n localization; the raw message does not.
    assert "application/x-zip-compressed" in str(exc_info.value.details["reason"])
    envelope = exc_info.value.to_envelope(locale="zh")
    assert envelope["error"]["details"]["reason"] == exc_info.value.details["reason"]


def test_allowed_mime_is_kept_even_if_extension_differs() -> None:
    assert validate_inbound_media_type("application/pdf", "notes.txt") == "application/pdf"


def test_inbound_over_size_limit_is_rejected() -> None:
    limit = 1024
    with pytest.raises(OctopError) as exc_info:
        validate_inbound_size(b"x" * (limit + 1), max_bytes=limit)
    assert exc_info.value.code is ErrorCode.ATTACHMENT_TOO_LARGE
    assert exc_info.value.status == 413
    assert exc_info.value.details["max_mb"] == 1
    envelope = exc_info.value.to_envelope(locale="zh")
    assert "1" in envelope["error"]["message"]
    assert "{max_mb}" not in envelope["error"]["message"]


@pytest.mark.asyncio
async def test_save_attachment_zip_from_windows_browser() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        stored = await save_attachment(
            workspace,
            owner_id=1,
            filename="bundle.zip",
            media_type="application/x-zip-compressed",
            data=b"PK\x03\x04",
        )
        assert stored.media_type == "application/zip"
        assert stored.data_path.endswith(".zip")


@pytest.mark.asyncio
async def test_save_attachment_pdf_uses_extension() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        stored = await save_attachment(
            workspace,
            owner_id=1,
            filename="report.pdf",
            media_type="application/pdf",
            data=b"%PDF-1.4",
        )
        assert re.fullmatch(r"inbound/\d{10,}_report\.pdf", stored.data_path)
        assert stored.filename == "report.pdf"
        assert stored.data_path.endswith(".pdf")

        on_disk = await workspace.adownload_bytes(stored.data_path)
        assert on_disk == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_concurrent_same_name_uploads_preserve_each_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.gateway.media import inbound_store

    monkeypatch.setattr(inbound_store.time, "time", lambda: 1_800_000_000)
    first_checked = asyncio.Event()
    release_first = asyncio.Event()
    second_progress = asyncio.Event()
    original_exists = BackendWorkspace.aexists

    async def paused_exists(workspace, path):
        exists = await original_exists(workspace, path)
        if asyncio.current_task().get_name() == "first-upload" and not first_checked.is_set():
            first_checked.set()
            await release_first.wait()
        return exists

    async def second_upload(workspace):
        try:
            return await save_attachment(
                workspace,
                owner_id=2,
                filename="report.pdf",
                media_type="application/pdf",
                data=b"second upload",
            )
        finally:
            second_progress.set()

    monkeypatch.setattr(BackendWorkspace, "aexists", paused_exists)
    _observe_waiting_upload(monkeypatch, inbound_store, second_progress)
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        first_task = asyncio.create_task(
            save_attachment(
                workspace,
                owner_id=1,
                filename="report.pdf",
                media_type="application/pdf",
                data=b"first upload",
            ),
            name="first-upload",
        )
        second_task = None
        try:
            await asyncio.wait_for(first_checked.wait(), 5)
            second_task = asyncio.create_task(
                second_upload(_workspace(ws_dir)), name="second-upload"
            )
            await asyncio.wait_for(second_progress.wait(), 5)
        finally:
            release_first.set()
            await asyncio.gather(
                first_task,
                *([second_task] if second_task is not None else []),
                return_exceptions=True,
            )
        first = first_task.result()
        second = second_task.result()
        assert first.data_path != second.data_path
        assert await workspace.adownload_bytes(first.data_path) == b"first upload"
        assert await workspace.adownload_bytes(second.data_path) == b"second upload"


@pytest.mark.asyncio
async def test_build_content_from_file_part_uses_resolved_path() -> None:
    from octop_gateway.models import TextContent

    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        backend = AgentBackedMediaBackend(workspace)
        msg = InboundMessage(
            channel_id="c",
            channel_type="dashboard",
            content=[
                TextContent(text="summarize"),
                FileContent(
                    filename="report.pdf",
                    mime_type="application/pdf",
                    local_path="inbound/01JTEST.pdf",
                ),
            ],
        )
        out = await build_content_from_message(msg, media_backend=backend)
        assert isinstance(out, str)
        from octop.infra.gateway.media.inbound_store import resolve_inbound_attachment_path

        resolved = resolve_inbound_attachment_path(workspace, "inbound/01JTEST.pdf")
        assert resolved in out
        assert "pdf" in out.lower()


@pytest.mark.asyncio
async def test_hints_from_saved_attachment_path() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        stored = await save_attachment(
            workspace,
            owner_id=1,
            filename="report.pdf",
            media_type="application/pdf",
            data=b"%PDF-1.4",
        )
        hints = hints_from_content_parts(
            [
                FileContent(
                    filename="report.pdf",
                    mime_type="application/pdf",
                    local_path=stored.data_path,
                )
            ],
            workspace=workspace,
        )
        assert len(hints) == 1
        from octop.infra.gateway.media.inbound_store import resolve_inbound_attachment_path

        resolved = resolve_inbound_attachment_path(workspace, stored.data_path)
        assert resolved in hints[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_upload_keeps_name_reserved_until_backend_write_finishes(
    monkeypatch: pytest.MonkeyPatch, cancel_count: int
) -> None:
    from octop.infra.gateway.media import inbound_store

    monkeypatch.setattr(inbound_store.time, "time", lambda: 1_800_000_000)
    worker_started = Event()
    release_worker = Event()
    worker_finished = Event()
    second_progress = asyncio.Event()
    first_paths = []
    original_upload = BackendWorkspace.aupload_bytes

    async def delayed_upload(workspace, path, data):
        if data != b"first upload":
            return await original_upload(workspace, path, data)

        def write():
            first_paths.append(path)
            worker_started.set()
            try:
                if not release_worker.wait(5):
                    raise TimeoutError("backend upload was not released")
                workspace.upload_bytes(path, data)
            finally:
                worker_finished.set()

        await asyncio.to_thread(write)

    async def second_upload(workspace):
        try:
            return await save_attachment(
                workspace,
                owner_id=2,
                filename="report.pdf",
                media_type="application/pdf",
                data=b"second upload",
            )
        finally:
            second_progress.set()

    monkeypatch.setattr(BackendWorkspace, "aupload_bytes", delayed_upload)
    _observe_waiting_upload(monkeypatch, inbound_store, second_progress)
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        first_task = asyncio.create_task(
            save_attachment(
                workspace,
                owner_id=1,
                filename="report.pdf",
                media_type="application/pdf",
                data=b"first upload",
            )
        )
        second_task = None
        try:
            assert await asyncio.to_thread(worker_started.wait, 5)
            for _ in range(cancel_count):
                first_task.cancel()
                await asyncio.sleep(0)
            second_task = asyncio.create_task(
                second_upload(_workspace(ws_dir)), name="second-upload"
            )
            await asyncio.wait_for(second_progress.wait(), 5)
        finally:
            release_worker.set()
            assert await asyncio.to_thread(worker_finished.wait, 5)
            await asyncio.gather(
                first_task,
                *([second_task] if second_task is not None else []),
                return_exceptions=True,
            )
        with pytest.raises(asyncio.CancelledError):
            first_task.result()
        second = second_task.result()
        assert await workspace.adownload_bytes(second.data_path) == b"second upload"
        assert await workspace.adownload_bytes(first_paths[0]) == b"first upload"
