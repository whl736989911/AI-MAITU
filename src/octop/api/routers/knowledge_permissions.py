"""Knowledge-base and document access metadata using native resource ACLs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_any_permission
from octop.infra.knowledge.permissions import (
    KnowledgePermissionService,
    StalePermissionVersion,
)
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/knowledge-bases")


class GrantBody(BaseModel):
    grantee_type: Literal["user", "unit", "role"]
    grantee_id: str = Field(min_length=1, max_length=128)


class PermissionBody(BaseModel):
    visibility: Literal["private", "unit", "public"]
    unit_key: str | None = Field(default=None, max_length=64)
    permission: Literal["read", "write"] = "read"
    grants: list[GrantBody] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, max_length=200)
    expected_version: int = Field(ge=0)


class KnowledgePermissionOrigin(BaseModel):
    resource_type: Literal["knowledge_base", "knowledge_document"]
    resource_id: str
    path: str


class KnowledgeAclEntry(BaseModel):
    visibility: Literal["private", "unit", "public"]
    unit_key: str | None
    permission: Literal["read", "write"]
    grants: list[GrantBody]
    version: int


class KnowledgeEffectivePermission(BaseModel):
    read: bool
    write: bool


class KnowledgePermissionChange(BaseModel):
    change_id: str
    status: str = Field(
        description="Applied immediately or pending the existing sharing approval flow."
    )
    impact_scope: str


class KnowledgePendingPermission(KnowledgePermissionChange):
    reason: str | None
    created_at: int


class KnowledgePermissionView(KnowledgePermissionOrigin):
    version: int = Field(description="Version to send as expected_version when saving changes.")
    access_scope: Literal["company", "department", "personal", "custom"]
    department_name: str | None
    inherited_from: KnowledgePermissionOrigin | None
    permission: Literal["read", "write"]
    effective: KnowledgeEffectivePermission
    can_manage: bool = Field(
        description="Content write access alone does not permit ACL management."
    )
    entry: KnowledgeAclEntry | None = Field(description="Explicit ACL; omitted for non-managers.")
    pending: KnowledgePendingPermission | None


class KnowledgePermissionListing(BaseModel):
    knowledge_base: KnowledgePermissionView
    documents: list[KnowledgePermissionView] = Field(
        description="Only authorized files and folders; denied names and paths are never included."
    )


class KnowledgePermissionChangeResult(BaseModel):
    permission: KnowledgePermissionView
    change: KnowledgePermissionChange


class KnowledgeUserPrincipal(BaseModel):
    id: int
    username: str
    label: str


class KnowledgeDepartmentPrincipal(BaseModel):
    key: str
    label: str


class KnowledgeRolePrincipal(BaseModel):
    id: str
    label: str


class KnowledgePermissionPrincipals(BaseModel):
    users: list[KnowledgeUserPrincipal]
    departments: list[KnowledgeDepartmentPrincipal]
    roles: list[KnowledgeRolePrincipal]


def _service(server: Any) -> KnowledgePermissionService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="knowledge services are not initialized")
    return KnowledgePermissionService(server.services)


async def _handle(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return await asyncio.get_running_loop().run_in_executor(None, call)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="knowledge resource not found") from exc
    except StalePermissionVersion as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_PERMISSION_VERSION",
                "message": "Permissions changed; reload before saving.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/{kb_id}/permission-principals",
    summary="List principals available to a knowledge ACL manager",
    response_model=KnowledgePermissionPrincipals,
)
async def get_permission_principals(
    kb_id: str,
    request: Request,
    server: Any = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).list_principals(
            kb_id,
            actor_user_id=user.id,
            locale=resolve_request_locale(request),
        )
    )


@router.get(
    "/{kb_id}/permissions",
    summary="Get effective knowledge-base permissions",
    response_model=KnowledgePermissionListing,
)
async def get_knowledge_permissions(
    kb_id: str,
    request: Request,
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).list_permissions(
            kb_id,
            actor_user_id=user.id,
            locale=resolve_request_locale(request),
        )
    )


@router.get(
    "/{kb_id}/documents/{doc_id}/permissions",
    summary="Get effective knowledge-document permissions",
    response_model=KnowledgePermissionView,
)
async def get_document_permissions(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).get_document_permissions(
            kb_id,
            doc_id,
            actor_user_id=user.id,
            locale=resolve_request_locale(request),
        )
    )


@router.put(
    "/{kb_id}/permissions",
    summary="Change knowledge-base permissions",
    response_model=KnowledgePermissionChangeResult,
)
async def put_knowledge_permissions(
    kb_id: str,
    body: PermissionBody,
    request: Request,
    server: Any = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).put_permissions(
            kb_id,
            "knowledge_base",
            kb_id,
            actor_user_id=user.id,
            visibility=body.visibility,
            unit_key=body.unit_key,
            permission=body.permission,
            grants=[(grant.grantee_type, grant.grantee_id) for grant in body.grants],
            reason=body.reason,
            expected_version=body.expected_version,
            locale=resolve_request_locale(request),
        )
    )


@router.put(
    "/{kb_id}/documents/{doc_id}/permissions",
    summary="Change knowledge-document permissions",
    response_model=KnowledgePermissionChangeResult,
)
async def put_document_permissions(
    kb_id: str,
    doc_id: str,
    body: PermissionBody,
    request: Request,
    server: Any = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).put_permissions(
            kb_id,
            "knowledge_document",
            doc_id,
            actor_user_id=user.id,
            visibility=body.visibility,
            unit_key=body.unit_key,
            permission=body.permission,
            grants=[(grant.grantee_type, grant.grantee_id) for grant in body.grants],
            reason=body.reason,
            expected_version=body.expected_version,
            locale=resolve_request_locale(request),
        )
    )
