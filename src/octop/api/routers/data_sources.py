"""HTTP API for knowledge-base data sources.

A data source is listed and mutated through its knowledge base's ACL: reading
needs read access to the base, creating/deleting/syncing needs write access
(owner or admin). The module key (``knowledge_bases``) is only the first gate —
the resource check lives in :class:`DataSourceService`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server, require_permission

# A sync failure is a knowledge-domain failure (feature disabled, embedding
# prerequisites, limits): reuse the knowledge router's mapping instead of
# describing the same statuses and messages twice.
from octop.api.routers.knowledge_bases import _map_knowledge_error
from octop.infra.db.repos.data_sources import KINDS, DataSourceRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.data_sources import (
    DataSourceService,
    DataSourceSyncFailed,
    DataSourceSyncUnsupported,
)
from octop.infra.knowledge.url_fetch import UrlFetchError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale
from octop.infra.utils.ssrf_guard import OutboundFetchError, UnsafeOutboundUrl

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateDataSourceBody(BaseModel):
    # ``extra="forbid"``: a body field this API does not write must fail loudly.
    # Silently dropping one is how a source ends up configured but inert.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200, description="Display name.")
    kind: str = Field(description="One of: " + ", ".join(KINDS) + ".")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Kind-specific settings. upload: document_id or path of a document in the base; "
            "url: url of the page to fetch; connector: connector_id."
        ),
    )


def _service(server: OctopServer) -> DataSourceService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "knowledge services are not initialized")
    return DataSourceService(server.services)


def _payload(row: DataSourceRow) -> dict[str, Any]:
    payload = asdict(row)
    payload["data_source_id"] = row.id
    payload["config"] = row.config
    return payload


def _map_data_source_error(
    exc: Exception, *, locale: str, server: OctopServer | None = None
) -> OctopError:
    if isinstance(exc, OctopError):
        return exc
    if isinstance(exc, DataSourceSyncUnsupported):
        return OctopError(
            ErrorCode.DATA_SOURCE_SYNC_UNSUPPORTED,
            str(exc),
            details={"kind": exc.kind},
        )
    if isinstance(exc, DataSourceSyncFailed):
        cause = exc.cause
        # A fetch that ran and failed is not an internal error: say what the
        # URL did, so the user can fix it.
        if isinstance(cause, (OutboundFetchError, UrlFetchError)):
            return OctopError(
                ErrorCode.DATA_SOURCE_FETCH_FAILED,
                str(cause),
                details={"reason": str(cause)},
            )
        if isinstance(cause, UnsafeOutboundUrl):
            return OctopError(
                ErrorCode.DATA_SOURCE_INVALID,
                str(cause),
                details={"reason": str(cause)},
            )
        return _map_knowledge_error(cause, locale=locale, server=server)
    if isinstance(exc, UnsafeOutboundUrl):
        return OctopError(ErrorCode.DATA_SOURCE_INVALID, str(exc), details={"reason": str(exc)})
    if isinstance(exc, PermissionError):
        return OctopError.localized(ErrorCode.KNOWLEDGE_FORBIDDEN, locale)
    if isinstance(exc, LookupError):
        return OctopError.localized(ErrorCode.NOT_FOUND, locale)
    if isinstance(exc, ValueError):
        return OctopError(ErrorCode.DATA_SOURCE_INVALID, str(exc), details={"reason": str(exc)})
    logger.exception("unhandled error in data source router: %s", exc)
    return OctopError.localized(ErrorCode.INTERNAL_ERROR, locale, details={"cause": str(exc)})


def _is_admin(user: User) -> bool:
    return bool(user.is_admin)


@router.get(
    "/knowledge-bases/{kb_id}/data-sources",
    summary="List a knowledge base's data sources",
)
async def list_data_sources(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    try:
        rows = _service(server).list_for_base(
            kb_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        return [_payload(row) for row in rows]
    except Exception as exc:
        raise _map_data_source_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/knowledge-bases/{kb_id}/data-sources",
    status_code=status.HTTP_201_CREATED,
    summary="Create a data source",
)
async def create_data_source(
    kb_id: str,
    body: CreateDataSourceBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        row = _service(server).create(
            kb_id,
            actor_user_id=user.id,
            name=body.name,
            kind=body.kind,
            config=body.config,
            is_admin=_is_admin(user),
        )
        return _payload(row)
    except Exception as exc:
        raise _map_data_source_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get("/data-sources/{ds_id}", summary="Get a data source")
async def get_data_source(
    ds_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        return _payload(
            _service(server).get(ds_id, actor_user_id=user.id, is_admin=_is_admin(user))
        )
    except Exception as exc:
        raise _map_data_source_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post("/data-sources/{ds_id}/sync", summary="Ingest a data source")
async def sync_data_source(
    ds_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        # Parsing, embedding, and index writes are blocking work; keep them off
        # the event loop, like every other ingest path.
        row = await asyncio.to_thread(
            _service(server).sync, ds_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        return _payload(row)
    except Exception as exc:
        raise _map_data_source_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.delete(
    "/data-sources/{ds_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data source",
)
async def delete_data_source(
    ds_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> None:
    try:
        _service(server).delete(ds_id, actor_user_id=user.id, is_admin=_is_admin(user))
    except Exception as exc:
        raise _map_data_source_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
