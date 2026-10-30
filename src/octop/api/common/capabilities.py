"""Live authorization for already-authenticated remote-control sockets."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.capabilities import UserCapabilityPolicy

logger = logging.getLogger(__name__)


class WebSocketCapabilityGuard:
    """Recheck the caller before input and disconnect revoked idle streams."""

    def __init__(self, websocket: WebSocket, *, user_id: int, permission: str) -> None:
        self._websocket = websocket
        self._user_id = user_id
        self._permission = permission
        self._watcher: asyncio.Task[None] | None = None

    async def check(self) -> bool:
        if self._websocket.application_state != WebSocketState.CONNECTED:
            return False
        server = self._websocket.app.state.octop_server
        try:
            UserCapabilityPolicy(server.services.repos).require_permissions(
                self._user_id, (self._permission,)
            )
        except OctopError as exc:
            await self._close(4003, exc.code.value)
            return False
        except Exception:
            logger.exception("Cannot authorize remote-control socket")
            await self._close(1011, ErrorCode.INTERNAL_ERROR.value)
            return False
        return True

    def start(self) -> None:
        self._watcher = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while self._websocket.application_state == WebSocketState.CONNECTED:
            await asyncio.sleep(1)
            if not await self.check():
                return

    async def _close(self, code: int, reason: str) -> None:
        if self._websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect, OSError):
                await self._websocket.close(code=code, reason=reason)

    async def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
