from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from octop.api.common.temporary_files import OwnedFileResponse, wait_owned_worker
from octop.infra.utils.tempfiles import managed_tempdir


@pytest.mark.parametrize("outcome", ["complete", "disconnect", "cancel"])
async def test_response_releases_only_its_scratch_after_every_send_outcome(
    tmp_path: Path, outcome: str
) -> None:
    original = tmp_path / "original.bin"
    payload = b"download bytes\n" * 10000
    original.write_bytes(payload)
    context = managed_tempdir("response-", root=tmp_path / "tmp")
    directory = context.__enter__()
    download = directory / "download.bin"
    download.write_bytes(payload)
    response = OwnedFileResponse(download, cleanup=partial(context.__exit__, None, None, None))
    received = bytearray()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            assert download.is_file()
            received.extend(message.get("body", b""))
            if outcome == "disconnect":
                raise OSError("client disconnected")
            if outcome == "cancel":
                raise asyncio.CancelledError()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/download",
        "headers": [],
        "http_version": "1.1",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }
    try:
        if outcome == "disconnect":
            with pytest.raises(OSError):
                await response(scope, receive, send)
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)
            assert received == payload
        assert not directory.exists()
        assert original.read_bytes() == payload
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_preparation_finishes_before_unclaimed_file_is_removed(
    tmp_path: Path, cancel_count: int
) -> None:
    ready = threading.Event()
    release = threading.Event()
    workspaces: list[Path] = []

    def prepare():
        context = managed_tempdir("preparation-", root=tmp_path / "tmp")
        directory = context.__enter__()
        try:
            (directory / "archive").write_bytes(b"still preparing")
            workspaces.append(directory)
            ready.set()
            assert release.wait(timeout=10)
            return context, directory / "archive"
        except BaseException:
            context.__exit__(None, None, None)
            raise

    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(None, prepare)
    request = asyncio.create_task(
        wait_owned_worker(worker, dispose=lambda result: result[0].__exit__(None, None, None))
    )
    try:
        assert await loop.run_in_executor(None, ready.wait, 10)
        for _ in range(cancel_count):
            request.cancel()
            await asyncio.sleep(0)
        assert not request.done()
        assert (workspaces[0] / "archive").read_bytes() == b"still preparing"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not workspaces[0].exists()
    finally:
        release.set()
        if not request.done():
            request.cancel()
        with suppress(asyncio.CancelledError):
            await request
