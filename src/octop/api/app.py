"""FastAPI app factory."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from scalar_fastapi import get_scalar_api_reference

from octop.api.middleware.jwt_auth import install as install_jwt_auth
from octop.api.middleware.license_gate import install as install_license_gate
from octop.api.middleware.setup_lockdown import install as install_setup_lockdown
from octop.api.openapi_meta import OPENAPI_TAGS, api_description, configure_openapi
from octop.api.routers.licenses import require_license_business
from octop.branding import platform_name
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.setup.version_info import get_local_version
from octop.infra.utils.locale import resolve_request_locale

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RouterMount:
    router: Any
    prefix: str
    tags: Sequence[str]
    # Business mounts additionally enforce the instance-license gate at the
    # route level (defense in depth under the ASGI middleware).
    gated: bool = False


_NO_CACHE_DASHBOARD_NAMES = frozenset({"sw.js", "manifest.json", "index.html"})


def dashboard_cache_control(full_path: str) -> str | None:
    """Cache-Control for a dashboard SPA path, or ``None`` to leave unset."""
    name = Path(full_path).name.lower() if full_path else "index.html"
    if not full_path or name in _NO_CACHE_DASHBOARD_NAMES:
        return "no-cache"
    # Vite emits content-hashed files under assets/ — safe to pin forever.
    if full_path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _dashboard_response(path: Path, full_path: str) -> FileResponse:
    response = FileResponse(path)
    cache_control = dashboard_cache_control(full_path)
    if cache_control is not None:
        response.headers["Cache-Control"] = cache_control
    return response


def is_dashboard_asset_path(full_path: str) -> bool:
    """True for hashed build artifacts that must 404 instead of falling back."""
    return full_path.startswith("assets/")


def _dashboard_fallback(index_file: Path, full_path: str) -> FileResponse:
    """Serve the SPA shell for routes; 404 for a missing hashed asset.

    Answering ``/assets/index.<old-hash>.js`` with ``index.html`` makes the
    browser reject HTML as a module script ("not a valid JavaScript MIME
    type"), which hides the real cause — a stale shell after an upgrade.
    """
    if is_dashboard_asset_path(full_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return _dashboard_response(index_file, "")


def _mount_routers(app: FastAPI, mounts: Sequence[_RouterMount]) -> None:
    for spec in mounts:
        dependencies = [Depends(require_license_business)] if spec.gated else None
        app.include_router(
            spec.router,
            prefix=spec.prefix,
            tags=list(spec.tags),
            dependencies=dependencies,
        )


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(OctopError)
    async def _octop(request: Request, exc: OctopError) -> JSONResponse:
        # Client responses are locale-localized; log the original English message
        # so INTERNAL_ERROR (and other 5xx) remain diagnosable in ~/.octop/logs.
        if exc.status >= 500:
            logger.error(
                "OctopError %s in %s: %s",
                exc.code.value,
                request.url.path,
                exc.message,
                exc_info=exc,
            )
        locale = resolve_request_locale(request)
        return JSONResponse(status_code=exc.status, content=exc.to_envelope(locale=locale))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception in %s", request.url.path)
        locale = resolve_request_locale(request)
        err = OctopError.localized(ErrorCode.INTERNAL_ERROR, locale)
        return JSONResponse(status_code=err.status, content=err.to_envelope())


def build_app(server: OctopServer) -> FastAPI:
    cfg = server.services.config if server.services else getattr(server, "config", None)
    enable_dashboard = cfg.enable_dashboard if cfg else True
    enable_api_docs = cfg.enable_api_docs if cfg else False
    enable_mobile = (
        cfg.capabilities.mobile.enabled if cfg and cfg.capabilities.mobile.enabled else False
    )

    from octop.api.routers.branding import _state as _branding_state

    api_title = f"{_branding_state(server)['name']['en']} API"
    app = FastAPI(
        title=api_title,
        version=get_local_version(),
        description=api_description(),
        openapi_url="/api/openapi.json" if enable_api_docs else None,
        openapi_tags=OPENAPI_TAGS,
    )
    configure_openapi(app)
    app.state.octop_server = server
    _install_exception_handlers(app)

    if cfg and cfg.cors_origins:
        from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[ACCESS_TOKEN_RESPONSE_HEADER],
        )

    install_jwt_auth(app, server)
    install_setup_lockdown(app, server)
    # Outermost: denies/cancels business HTTP+WS before any other processing.
    install_license_gate(app, server)

    from octop.infra.setup.tls.challenge import challenge_store

    @app.get("/.well-known/acme-challenge/{token}", include_in_schema=False)
    async def acme_http01_challenge(token: str) -> PlainTextResponse:
        body = challenge_store.get(token)
        if body is None:
            raise HTTPException(status_code=404, detail="challenge not found")
        return PlainTextResponse(body)

    from octop.api.routers import (
        acp,
        admin,
        agent_files,
        agent_tools,
        agents,
        auth,
        auth_oauth,
        auth_oidc,
        backup,
        branding,
        browser,
        channels,
        chat,
        connectors,
        cron,
        data_sources,
        desktop,
        envs,
        experts,
        feature_data,
        feature_workflow,
        features,
        health,
        i18n,
        internal_mcp,
        invites,
        knowledge_access_notifications,
        knowledge_bases,
        knowledge_collections,
        knowledge_permissions,
        licenses,
        mbti,
        media_generation,
        memory,
        memory_portable,
        memory_review,
        memory_storage,
        mobile,
        ollama_models,
        onnx_models,
        org_units,
        plugins,
        preferences,
        proactive_care,
        providers,
        search,
        settings,
        setup,
        sharing,
        skill_packages,
        skills,
        slash,
        subagents,
        teams,
        terminal,
        update,
        uploads,
        usage,
        users,
        voice,
        workspace,
    )
    from octop.api.routers.filesystem import router as filesystem_router
    from octop.api.routers.observability import router as observability_router
    from octop.api.routers.providers import admin_router as admin_providers_router
    from octop.api.routers.security import router as security_router
    from octop.api.routers.storage_backends import admin_router as admin_storage_router
    from octop.api.routers.storage_backends import user_router as storage_backends_user_router
    from octop.api.routers.tls import router as tls_router
    from octop.api.routers.voice import admin_router as admin_voice_router

    _mount_routers(
        app,
        [
            _RouterMount(setup.router, "/api", ["setup"]),
            _RouterMount(branding.router, "/api", ["branding"]),
            _RouterMount(auth.router, "/api/auth", ["auth"]),
            _RouterMount(auth_oidc.router, "/api/auth", ["auth"]),
            _RouterMount(auth_oauth.router, "/api/auth", ["auth"]),
            _RouterMount(invites.public_router, "/api/auth/invite", ["auth"]),
            _RouterMount(preferences.router, "/api", ["auth"]),
            _RouterMount(i18n.router, "/api", ["i18n"]),
            _RouterMount(health.router, "/api/health", ["health"]),
            _RouterMount(licenses.router, "/api/license", ["license"]),
            _RouterMount(invites.admin_router, "/api/users/invites", ["users"]),
            _RouterMount(users.router, "/api/users", ["users"]),
            _RouterMount(org_units.router, "/api/org-units", ["users"]),
            _RouterMount(sharing.router, "/api/sharing", ["sharing"], gated=True),
            _RouterMount(agents.router, "/api/agents", ["agents"], gated=True),
            _RouterMount(agent_tools.router, "/api", ["agents"], gated=True),
            _RouterMount(feature_workflow.router, "/api", ["agents"], gated=True),
            _RouterMount(feature_data.router, "/api", ["agents"], gated=True),
            _RouterMount(acp.router, "/api", ["agents"], gated=True),
            _RouterMount(chat.router, "/api", ["chat"], gated=True),
            _RouterMount(slash.router, "/api", ["slash"], gated=True),
            _RouterMount(connectors.router, "/api", ["connectors"], gated=True),
            _RouterMount(knowledge_bases.router, "/api", ["knowledge"], gated=True),
            _RouterMount(knowledge_access_notifications.router, "/api", ["knowledge"], gated=True),
            _RouterMount(knowledge_permissions.router, "/api", ["knowledge"], gated=True),
            _RouterMount(knowledge_collections.router, "/api", ["knowledge"], gated=True),
            _RouterMount(data_sources.router, "/api", ["knowledge"], gated=True),
            _RouterMount(internal_mcp.router, "/api", ["internal-mcp"], gated=True),
            _RouterMount(channels.router, "/api", ["channels"], gated=True),
            _RouterMount(cron.router, "/api", ["cron"], gated=True),
            _RouterMount(settings.router, "/api", ["settings"]),
            _RouterMount(envs.router, "/api", ["envs"], gated=True),
            _RouterMount(search.router, "/api", ["search"], gated=True),
            _RouterMount(providers.router, "/api/providers", ["providers"], gated=True),
            _RouterMount(voice.router, "/api/voice", ["voice"], gated=True),
            _RouterMount(admin.router, "/api/admin", ["admin"], gated=True),
            _RouterMount(backup.router, "/api/admin", ["admin"], gated=True),
            _RouterMount(admin_providers_router, "/api/admin/providers", ["admin"], gated=True),
            _RouterMount(admin_voice_router, "/api/admin/voice/providers", ["admin"], gated=True),
            _RouterMount(
                observability_router, "/api/admin/observability", ["observability"], gated=True
            ),
            _RouterMount(
                media_generation.router,
                "/api/admin/media-generation",
                ["providers"],
                gated=True,
            ),
            _RouterMount(tls_router, "/api/admin/tls", ["tls"], gated=True),
            _RouterMount(security_router, "/api/admin/security", ["security"], gated=True),
            _RouterMount(
                admin_storage_router, "/api/admin/storage-backends", ["admin"], gated=True
            ),
            _RouterMount(
                storage_backends_user_router,
                "/api/storage-backends",
                ["storage-backends"],
                gated=True,
            ),
            _RouterMount(filesystem_router, "/api/filesystem", ["filesystem"], gated=True),
            _RouterMount(mbti.router, "/api", ["mbti"], gated=True),
            _RouterMount(experts.router, "/api", ["experts"], gated=True),
            _RouterMount(teams.router, "/api", ["teams"], gated=True),
            _RouterMount(features.router, "/api/features", ["features"], gated=True),
            _RouterMount(workspace.router, "/api", ["workspace"], gated=True),
            _RouterMount(agent_files.router, "/api", ["agent_files"], gated=True),
            _RouterMount(memory.router, "/api", ["memory"], gated=True),
            _RouterMount(memory_portable.router, "/api", ["memory"], gated=True),
            _RouterMount(memory_review.router, "/api", ["memory"], gated=True),
            _RouterMount(memory_storage.router, "/api", ["memory"], gated=True),
            _RouterMount(proactive_care.router, "/api", ["proactive-care"], gated=True),
            _RouterMount(usage.router, "/api", ["usage"], gated=True),
            _RouterMount(usage.admin_router, "/api/admin", ["admin"], gated=True),
            _RouterMount(skill_packages.router, "/api", ["skill-packages"], gated=True),
            _RouterMount(skills.router, "/api", ["skills"], gated=True),
            _RouterMount(subagents.router, "/api", ["subagents"], gated=True),
            _RouterMount(terminal.router, "/api", ["terminal"], gated=True),
            _RouterMount(uploads.router, "/api", ["chat"], gated=True),
            _RouterMount(update.router, "/api", ["update"]),
            _RouterMount(browser.router, "/api", ["browser"], gated=True),
            _RouterMount(desktop.router, "/api", ["desktop"], gated=True),
            _RouterMount(ollama_models.router, "/api", ["ollama"], gated=True),
            _RouterMount(onnx_models.router, "/api", ["onnx"], gated=True),
            _RouterMount(plugins.router, "/api", ["plugins"], gated=True),
        ],
    )

    if enable_mobile:
        _mount_routers(
            app,
            [
                _RouterMount(mobile.router, "/api", ["mobile"]),
            ],
        )

    if enable_api_docs:

        @app.get("/api/docs", include_in_schema=False)
        async def api_docs() -> HTMLResponse:
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=f"{platform_name('en')} API",
            )

    if enable_dashboard:
        dashboard_dir = Path(__file__).parent.parent / "dashboard"
        index_file = dashboard_dir / "index.html"
        if index_file.exists():

            @app.get("/{full_path:path}", include_in_schema=False)
            async def spa_fallback(full_path: str) -> FileResponse:
                if full_path.startswith(("api/", "ws/")):
                    raise HTTPException(status_code=404, detail="Not Found")

                if full_path:
                    raw_path = Path(full_path)
                    # Reject absolute paths and parent-dir references before
                    # joining, so user input never drives a path expression.
                    if raw_path.is_absolute() or ".." in raw_path.parts:
                        return _dashboard_fallback(index_file, full_path)
                    candidate = (dashboard_dir / Path(*raw_path.parts)).resolve()
                    try:
                        candidate.relative_to(dashboard_dir.resolve())
                    except ValueError:
                        return _dashboard_fallback(index_file, full_path)
                    if candidate.is_file():
                        return _dashboard_response(candidate, full_path)
                return _dashboard_fallback(index_file, full_path)

    return app
