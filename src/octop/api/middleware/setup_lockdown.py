"""Lock down non-setup endpoints while first-run setup remains pending.

Returns ``503 {"setup_required": true}`` so the SPA can hard-redirect
to ``/setup``; an existing administrator can still reach authentication.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_OPEN_PREFIXES = (
    "/api/setup/",
    "/api/health/",
)

_OPEN_DURING_SETUP = {
    "/api/auth/login",
    "/api/auth/captcha",
    "/api/auth/oidc/status",
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
    "/api/auth/oidc/exchange",
}


def install(app: Any, server: Any) -> None:
    @app.middleware("http")  # type: ignore[untyped-decorator]
    async def _lockdown(
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        cfg = server.services.config if server.services else getattr(server, "config", None)
        open_exact = ["/api/health"]
        if (request.method == "GET" and path == "/api/license/status") or (
            request.method == "POST" and path in {"/api/license/activate", "/api/license/refresh"}
        ):
            return await call_next(request)
        if cfg and cfg.enable_api_docs:
            open_exact.extend(("/api/docs", "/api/openapi.json"))
        if path in open_exact or any(path.startswith(p) for p in _OPEN_PREFIXES):
            return await call_next(request)
        if (
            path in _OPEN_DURING_SETUP
            and server.user_manager is not None
            and any(user.is_admin for user in server.user_manager.list())
        ):
            return await call_next(request)

        from octop.infra.setup.state import is_setup_pending

        if is_setup_pending(server):
            return JSONResponse(
                status_code=503,
                content={"setup_required": True},
            )
        return await call_next(request)
