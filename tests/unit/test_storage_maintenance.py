from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from pathlib import Path

import pytest

from octop.infra.server import OctopServer


async def test_cancelled_maintenance_waits_for_its_file_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    server = OctopServer(home=tmp_path)

    def maintain() -> None:
        entered.set()
        assert release.wait(timeout=10)
        (tmp_path / "worker-finished").write_bytes(b"done")
        finished.set()

    monkeypatch.setattr(server, "_maintain_storage", maintain)
    task = asyncio.create_task(server._run_storage_maintenance())
    try:
        assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert (tmp_path / "worker-finished").read_bytes() == b"done"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
