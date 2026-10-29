"""Knowledge-base and document access metadata using native resource ACLs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_any_permission
from octop.infra.knowledge.delegations import KnowledgeDelegationService
from octop.infra.knowledge.permissions import (
    KnowledgePermissionService,
    StalePermissionVersion,
)
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/knowledge-bases")


class GrantBody(BaseModel):
    grantee_type: Literal["user", "unit", "role"]
    grantee_id: str = Field(min_length=1, max_length=128)
    # Delegation authority rides on individual user grants only; the service
    # rejects the flag anywhere else.
    can_delegate: bool = False


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


class KnowledgeGrantView(BaseModel):
    grantee_type: Literal["user", "unit", "role"]
    grantee_id: str
    can_delegate: bool = False


class KnowledgeAclEntry(BaseModel):
    visibility: Literal["private", "unit", "public"]
    unit_key: str | None
    permission: Literal["read", "write"]
    grants: list[KnowledgeGrantView]
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
    can_delegate: bool = Field(
        description=(
            "Server-effective delegated authority: the viewer may issue "
            "delegation edges for this resource. Independent of content write."
        )
    )
    entry: KnowledgeAclEntry | None = Field(description="Explicit ACL; omitted for non-managers.")
    pending: KnowledgePendingPermission | None


class UploadAccess(BaseModel):
    scope: Literal["company", "department", "personal", "authorized"]
    permission: Literal["read", "write"]
    unit_key: str | None = None
    department_name: str | None = None


class UploadAccessListing(BaseModel):
    options: list[UploadAccess]
    default: UploadAccess | None


class DelegationGrant(BaseModel):
    id: str
    grantor_user_id: int
    grantee_user_id: int
    grantee_label: str
    can_delegate: bool
    can_revoke: bool


class DelegationPrincipal(BaseModel):
    id: int
    username: str
    label: str


class DelegationListing(BaseModel):
    version: int
    can_delegate: bool
    permission: Literal["read", "write"]
    grants: list[DelegationGrant]
    principals: list[DelegationPrincipal]


class DelegationUpsertBody(BaseModel):
    grantee_user_id: int
    can_delegate: bool = False


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


def _delegation_service(server: Any) -> KnowledgeDelegationService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="knowledge services are not initialized")
    return KnowledgeDelegationService(server.services.db)


async def _handle[T](call: Callable[[], T]) -> T:
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
            grant_delegation=frozenset(
                (grant.grantee_type, grant.grantee_id)
                for grant in body.grants
                if grant.can_delegate
            ),
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
            grant_delegation=frozenset(
                (grant.grantee_type, grant.grantee_id)
                for grant in body.grants
                if grant.can_delegate
            ),
        )
    )


@router.get(
    "/{kb_id}/upload-permissions",
    response_model=UploadAccessListing,
    summary="Get safe upload access options for one destination",
)
async def get_upload_permissions(
    kb_id: str,
    request: Request,
    folder: str = Query(default="", max_length=1000),
    data_source_id: str | None = Query(default=None, max_length=128),
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> dict[str, Any]:
    return await _handle(
        lambda: _service(server).upload_access(
            kb_id,
            folder=folder,
            data_source_id=data_source_id,
            actor_user_id=user.id,
            locale=resolve_request_locale(request),
        )
    )


@router.get(
    "/{kb_id}/documents/{doc_id}/delegations",
    response_model=DelegationListing,
    summary="List delegation grants for one document",
)
async def get_document_delegations(
    kb_id: str,
    doc_id: str,
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> dict[str, Any]:
    return await _handle(
        lambda: _delegation_service(server).list_delegations(kb_id, doc_id, actor_user_id=user.id)
    )


@router.post(
    "/{kb_id}/documents/{doc_id}/delegations",
    status_code=status.HTTP_201_CREATED,
    response_model=DelegationListing,
    summary="Grant or update one delegation edge",
)
async def post_document_delegations(
    kb_id: str,
    doc_id: str,
    body: DelegationUpsertBody,
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> dict[str, Any]:
    return await _handle(
        lambda: _delegation_service(server).upsert_delegation(
            kb_id,
            doc_id,
            actor_user_id=user.id,
            grantee_user_id=body.grantee_user_id,
            can_delegate=body.can_delegate,
        )
    )


@router.delete(
    "/{kb_id}/documents/{doc_id}/delegations/{delegation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one delegation edge and cascade its descendants",
)
async def delete_document_delegation(
    kb_id: str,
    doc_id: str,
    delegation_id: str,
    server: Any = Depends(get_server),
    user: Any = Depends(require_any_permission("knowledge_bases", "knowledge_settings")),
) -> None:
    await _handle(
        lambda: _delegation_service(server).revoke_delegation(
            kb_id, doc_id, delegation_id, actor_user_id=user.id
        )
    )
