"""Per-user durable notices about revoked knowledge authorizations."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.permissions import KnowledgePermissionService
from octop.infra.server import OctopServer
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/knowledge-access-notifications")


class KnowledgeAccessNotification(BaseModel):
    id: str
    kb_id: str
    resource_id: str
    resource_path: str = Field(
        description="Previously authorized logical path, never a physical storage address."
    )
    event_kind: Literal["authorization_revoked", "delegation_revoked"]
    access_retained: bool = Field(
        description="True when valid read access remains after the authorization or delegation change."
    )
    read_at: int | None
    created_at: int


class KnowledgeAccessNotificationListing(BaseModel):
    items: list[KnowledgeAccessNotification]
    unread_count: int = Field(ge=0)


@router.get(
    "",
    response_model=KnowledgeAccessNotificationListing,
    summary="List your knowledge-authorization revocation notifications",
    description=(
        "Returns only the signed-in user's durable notifications, including upstream "
        "cascading revocations. Does not require continued access to the affected "
        "knowledge base or its module. A notification never restores resource access."
    ),
)
async def list_access_notifications(
    request: Request,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    service = KnowledgePermissionService(server.services)
    locale = resolve_request_locale(request)
    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: service.list_notifications(
            actor_user_id=user.id,
            unread_only=unread_only,
            limit=limit,
            locale=locale,
        ),
    )


@router.post(
    "/{notification_id}/read",
    status_code=204,
    response_class=Response,
    summary="Mark your knowledge-authorization notification as read",
    description=(
        "Idempotent for your own notification. Missing notifications and notifications "
        "belonging to another user both return 404."
    ),
)
async def read_access_notification(
    notification_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> Response:
    service = KnowledgePermissionService(server.services)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: service.mark_notification_read(notification_id, actor_user_id=user.id),
        )
    except LookupError as exc:
        raise OctopError.localized(ErrorCode.NOT_FOUND, resolve_request_locale(request)) from exc
    return Response(status_code=204)
