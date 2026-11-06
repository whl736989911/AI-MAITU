"""Responses that own scratch files until their ASGI send has ended."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)


async def wait_owned_worker[T](worker: asyncio.Future[T], *, dispose: Callable[[T], Any]) -> T:
    """Finish file preparation before cancelling, then dispose any unclaimed result."""
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled() and worker.exception() is None:
            cleanup = asyncio.get_running_loop().run_in_executor(None, dispose, worker.result())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
        raise


class OwnedFileResponse(FileResponse):
    """A file response that releases scratch ownership after every send outcome.

    ``cleanup`` closes a managed temporary-directory context after the response
    has finished reading the file, including when sending fails or is cancelled.
    """

    def __init__(self, *args: Any, cleanup: Callable[[], Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup
        self._cleanup_task: asyncio.Future[Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.get_running_loop().run_in_executor(None, self._cleanup)
            cancelled = False
            while not self._cleanup_task.done():
                try:
                    await asyncio.shield(self._cleanup_task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            try:
                self._cleanup_task.result()
            except Exception:
                logger.exception("failed to clean up owned response file")
            if cancelled:
                raise asyncio.CancelledError
