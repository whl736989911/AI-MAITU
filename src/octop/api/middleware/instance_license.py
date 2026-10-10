"""Default-deny HTTP and WebSocket business access while instance-locked."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request

from octop.i18n import license_reason_message
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import current_license
from octop.infra.setup.state import is_setup_pending
from octop.infra.utils.locale import resolve_request_locale

_INSTALL_ATTR = "_octop_instance_license_installed"

# Maintenance authentication survives lock, but no broad API prefix is exempt.
_ALWAYS_ALLOWED_PATTERNS = (("GET", re.compile(r"/api/branding/assets/[^/]+/[^/]+")),)
_ALWAYS_ALLOWED: dict[str, frozenset[str]] = {
    "/api/license/status": frozenset({"GET"}),
    "/api/setup/status": frozenset({"GET"}),
    "/api/branding": frozenset({"GET"}),
    "/api/branding/manifest.webmanifest": frozenset({"GET"}),
    "/api/license/details": frozenset({"GET"}),
    "/api/license/activate": frozenset({"POST"}),
    "/api/license/refresh": frozenset({"POST"}),
    "/api/health": frozenset({"GET", "HEAD"}),
    "/api/health/": frozenset({"GET", "HEAD"}),
    "/api/auth/login": frozenset({"POST"}),
    "/api/auth/logout": frozenset({"POST"}),
    "/api/auth/me": frozenset({"GET", "PATCH"}),
    "/api/auth/change-password": frozenset({"POST"}),
    "/api/auth/captcha": frozenset({"GET"}),
    "/api/auth/oidc/status": frozenset({"GET"}),
    "/api/auth/oidc/start": frozenset({"POST"}),
    "/api/auth/oidc/callback": frozenset({"GET"}),
    "/api/auth/oidc/exchange": frozenset({"POST"}),
    "/api/auth/oauth/status": frozenset({"GET"}),
    "/api/auth/oauth/start": frozenset({"POST"}),
    "/api/auth/oauth/callback": frozenset({"GET"}),
    "/api/auth/oauth/exchange": frozenset({"POST"}),
    "/api/auth/invite/validate": frozenset({"POST"}),
    "/api/i18n/tools": frozenset({"GET"}),
    "/api/i18n/skills": frozenset({"GET"}),
    "/api/docs": frozenset({"GET", "HEAD"}),
    "/api/openapi.json": frozenset({"GET", "HEAD"}),
}

_FIRST_RUN_METHOD_PATHS = frozenset(
    {
        ("GET", "/api/setup/presets"),
        ("GET", "/api/setup/validate-token"),
        ("POST", "/api/setup/test-database"),
        ("POST", "/api/setup/database"),
        ("POST", "/api/setup/begin"),
        ("POST", "/api/setup/verify-password"),
        ("POST", "/api/setup/initial-admin"),
        ("POST", "/api/setup/resume-wizard"),
        ("POST", "/api/setup/finish"),
    }
)

_BLOCKED_HOST_MANAGEMENT = (
    ("POST", re.compile(r"/api/plugins/(?:install|upload|reload|market/[^/]+/install)")),
    ("GET", re.compile(r"/api/agents/[^/]+/terminal/context")),
    ("PUT", re.compile(r"/api/(?:acp|acp/[^/]+|agents/[^/]+/acp(?:/tool|/[^/]+)?)")),
    ("DELETE", re.compile(r"/api/(?:acp/[^/]+|agents/[^/]+/acp/[^/]+)")),
    ("WEBSOCKET", re.compile(r"/api/agents/[^/]+/terminal/ws")),
    ("ANY", re.compile(r"/api/envs(?:/.*)?")),
    ("GET", re.compile(r"/api/admin/storage-backends/[^/]+/tree")),
    ("POST", re.compile(r"/api/admin/storage-backends(?:/[^/]+/test|/probe)?")),
    ("PUT", re.compile(r"/api/admin/storage-backends(?:/[^/]+)?")),
    ("PATCH", re.compile(r"/api/admin/storage-backends/[^/]+")),
    ("DELETE", re.compile(r"/api/admin/storage-backends/[^/]+")),
    ("ANY", re.compile(r"/api/(?:browser|desktop|mobile|filesystem)(?:/.*)?")),
    ("WEBSOCKET", re.compile(r"/api/(?:browser|desktop|mobile)-stream/ws")),
    ("POST", re.compile(r"/api/connectors/[^/]+/install-cli")),
)


def _is_blocked_host_management(method: str, path: str) -> bool:
    return any(
        expected in {method, "ANY"} and pattern.fullmatch(path)
        for expected, pattern in _BLOCKED_HOST_MANAGEMENT
    )


async def _send_forbidden(scope: dict[str, Any], send: Callable[..., Awaitable[None]]) -> None:
    request = Request(scope)
    locale = resolve_request_locale(request)
    error = OctopError(
        ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
        "This management operation is disabled for this customer deployment.",
        status=403,
    )
    body = json.dumps(error.to_envelope(locale=locale), ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _is_locked(service: Any) -> bool:
    try:
        service.require_business_access()
    except OctopError as exc:
        if exc.code == ErrorCode.INSTANCE_LICENSE_LOCKED:
            return True
        raise
    return False


def _setup_is_first_run(scope: dict[str, Any]) -> bool:
    app = scope.get("app")
    server = getattr(getattr(app, "state", None), "octop_server", None)
    return server is not None and is_setup_pending(server)


def _allowed_http(method: str, path: str, scope: dict[str, Any]) -> bool:
    if method in _ALWAYS_ALLOWED.get(path, frozenset()):
        return True
    if any(
        expected == method and pattern.fullmatch(path)
        for expected, pattern in _ALWAYS_ALLOWED_PATTERNS
    ):
        return True
    return (method, path) in _FIRST_RUN_METHOD_PATHS and _setup_is_first_run(scope)


def _locked_response(
    scope: dict[str, Any], send: Callable[..., Awaitable[None]], service: Any
) -> Any:
    async def respond() -> None:
        request = Request(scope)
        reason = service.status().get("reason")
        locale = resolve_request_locale(request)
        error = OctopError(
            ErrorCode.INSTANCE_LICENSE_LOCKED,
            license_reason_message(reason, locale),
            status=403,
            details={"reason": reason},
        )
        body = json.dumps(error.to_envelope(locale=locale), ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return respond


class InstanceLicenseMiddleware:
    """Pure ASGI guard, including websocket handshakes and established sessions."""

    def __init__(self, app: Any, *, service: Any | None = None) -> None:
        self.app = app
        self.service = service

    def _service(self) -> Any:
        return self.service if self.service is not None else current_license()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "GET").upper()
            if path.startswith("/api/") and _is_blocked_host_management(method, path):
                await _send_forbidden(scope, send)
                return
            service = self._service()
            business_request = path.startswith("/api/") and not _allowed_http(method, path, scope)
            if business_request and _is_locked(service):
                await _locked_response(scope, send, service)()
                return
            if not business_request:
                await self.app(scope, receive, send)
                return

            response_started = False

            async def tracked_send(message: Any) -> None:
                nonlocal response_started
                if message.get("type") == "http.response.start":
                    response_started = True
                await send(message)

            try:
                async with service.business_task():
                    await self.app(scope, receive, tracked_send)
            except asyncio.CancelledError:
                if response_started or not _is_locked(service):
                    raise
                await _locked_response(scope, send, service)()
            except OctopError as exc:
                if response_started or exc.code != ErrorCode.INSTANCE_LICENSE_LOCKED:
                    raise
                await _locked_response(scope, send, service)()
            return

        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        service = self._service()
        path = scope.get("path", "")
        closed = False

        async def close_locked() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            reason = service.status().get("reason")
            payload = json.dumps(
                {
                    "error": {
                        "code": ErrorCode.INSTANCE_LICENSE_LOCKED.value,
                        "details": {"reason": reason},
                    }
                },
                separators=(",", ":"),
            )
            await send({"type": "websocket.close", "code": 4403, "reason": payload})

        if path.startswith("/api/") and _is_blocked_host_management("WEBSOCKET", path):
            closed = True
            forbidden = json.dumps(
                {
                    "error": {
                        "code": ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED.value,
                    }
                },
                separators=(",", ":"),
            )
            await send({"type": "websocket.close", "code": 4403, "reason": forbidden})
            return
        if path.startswith("/api/") and _is_locked(service):
            await close_locked()
            return

        unregister: Callable[[], None] | None = None
        if path.startswith("/api/"):
            register = getattr(service, "register_connection", None)
            if callable(register):
                unregister = register(close_locked)

        async def checked_receive() -> Any:
            message = await receive()
            if (
                message.get("type") == "websocket.receive"
                and path.startswith("/api/")
                and _is_locked(service)
            ):
                await close_locked()
                return {"type": "websocket.disconnect", "code": 4403}
            return message

        try:
            await self.app(scope, checked_receive, send)
        finally:
            if unregister is not None:
                unregister()


def install(app: Any, service: Any | None = None) -> None:
    """Install the license gate before routing; can be called during app setup."""
    if getattr(app, _INSTALL_ATTR, False):
        return
    setattr(app, _INSTALL_ATTR, True)
    app.add_middleware(InstanceLicenseMiddleware, service=service)
