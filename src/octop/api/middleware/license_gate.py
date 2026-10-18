"""Instance-license ASGI gate for business HTTP + WebSocket traffic.

While a managed license is not allowing business, every ``/api`` business route
is denied with ``423 INSTANCE_LICENSE_LOCKED`` (websockets close with custom
code ``4423``) *before* reaching routers — independent of the dashboard — and
already-running requests/streams/connections are cancelled at the moment the
license flips invalid.

Exempt while locked (identity/bootstrap plane only): auth, setup wizard,
license management, health, server timezone, i18n labels, update endpoints,
user/identity administration, and read-only branding for the login screen.
Non-``/api`` paths (dashboard SPA shell/assets) are never gated here.

Unmanaged installs (no ``MAITU_LICENSE_TRUSTED_KEYS_FILE``) bypass the gate
entirely: the middleware is not even installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer

logger = logging.getLogger(__name__)

LICENSE_WS_CLOSE_CODE = 4423

_EXACT_PATHS = frozenset(
    {
        "/api/health",
        "/api/settings/timezone",
        "/api/update",
        "/api/update/status",
        "/api/update/check",
        "/api/update/upgrade",
        "/api/update/restart",
        "/api/preferences",
        "/api/branding/manifest.webmanifest",
    }
)

# Branding reads keep the locked login screen renderable; branding writes
# (PUT/DELETE/logo upload) stay license-gated.
_BRANDING_READ_PATHS = frozenset({"/api/branding"})

_EXEMPT_PREFIXES = (
    "/api/setup/",
    "/api/auth/",
    "/api/license/",
    "/api/i18n/",
    "/api/users",
    "/api/org-units",
    "/api/branding/assets/",
)

_INSTALL_ATTR = "_octop_license_gate_installed"


def is_license_exempt_path(path: str, method: str | None = None) -> bool:
    """True when the route stays reachable while the license is locked."""
    if method is not None and method.upper() == "OPTIONS":
        # CORS preflight must keep flowing to CORSMiddleware (inner) so the
        # browser sees a proper preflight result, not a gate error.
        return True
    if path in _EXACT_PATHS:
        return True
    if path in _BRANDING_READ_PATHS:
        return method is None or method.upper() in ("GET", "HEAD")
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def is_business_scope(scope: dict[str, Any]) -> bool:
    """True for HTTP/WS scopes the license gate must police."""
    if scope["type"] not in ("http", "websocket"):
        return False
    path: str = scope.get("path", "")
    return path.startswith("/api/") or path.startswith("/ws")


def license_lock_envelope(locale: str | None) -> dict[str, Any]:
    err = OctopError(
        ErrorCode.INSTANCE_LICENSE_LOCKED,
        "This instance's license does not permit business access",
    )
    return err.to_envelope(locale=locale)


class LicenseGateMiddleware:
    """Pure-ASGI gate so websockets and streaming responses are covered too.

    The latest instance built by ``add_middleware`` is published on
    :attr:`live`; the server transition listener calls :meth:`deny_active`
    through :class:`LicenseGateListener`.
    """

    live: LicenseGateMiddleware | None = None

    def __init__(self, app: Any, server: OctopServer) -> None:
        self.app = app
        self.server = server
        self._active: set[asyncio.Task[None]] = set()
        LicenseGateMiddleware.live = self

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not is_business_scope(scope):
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        method = scope.get("method") if scope["type"] == "http" else None
        if is_license_exempt_path(path, method):
            await self.app(scope, receive, send)
            return
        if self.server.license_allows_business():
            task = asyncio.current_task()
            if task is not None:
                self._active.add(task)
            try:
                await self.app(scope, receive, self._guard_websocket_close(send, scope))
            finally:
                if task is not None:
                    self._active.discard(task)
            return
        if scope["type"] == "websocket":
            await self._deny_websocket(receive, send)
            return
        await self._deny_http(scope, send)

    def _guard_websocket_close(self, send: Any, scope: dict[str, Any]) -> Any:
        if scope["type"] != "websocket":
            return send

        async def guarded_send(message: dict[str, Any]) -> None:
            if (
                message.get("type") == "websocket.close"
                and not self.server.license_allows_business()
            ):
                message = {**message, "code": LICENSE_WS_CLOSE_CODE}
            await send(message)

        return guarded_send

    async def _deny_http(self, scope: dict[str, Any], send: Any) -> None:
        from octop.infra.utils.locale import resolve_request_locale  # noqa: PLC0415

        try:
            from starlette.requests import Request  # noqa: PLC0415

            locale = resolve_request_locale(Request(scope))
        except Exception:
            locale = None
        body = json.dumps(license_lock_envelope(locale), ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 423,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _deny_websocket(self, receive: Any, send: Any) -> None:
        try:
            message = await receive()
        except Exception:
            return
        if message.get("type") != "websocket.connect":
            return
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": LICENSE_WS_CLOSE_CODE})

    def deny_active(self) -> None:
        """Cancel in-flight business requests/streams/sockets (license went invalid)."""
        for task in tuple(self._active):
            task.cancel()


class LicenseGateListener:
    """Server license listener that cancels live gate tasks on lock."""

    def __init__(self, server: OctopServer) -> None:
        self._server = server

    async def on_change(self, allowed: bool) -> None:
        gate = LicenseGateMiddleware.live
        if not allowed and gate is not None:
            gate.deny_active()


def install(app: Any, server: OctopServer) -> None:
    """Install the outermost ASGI gate + wire the transition cancellation."""
    if getattr(app, _INSTALL_ATTR, False):
        return
    setattr(app, _INSTALL_ATTR, True)
    app.add_middleware(LicenseGateMiddleware, server=server)
    server.add_license_listener(LicenseGateListener(server).on_change)
