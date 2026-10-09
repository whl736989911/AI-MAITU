"""Personal source-bound materialized knowledge reading views."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_any_permission
from octop.api.routers.knowledge_bases import KNOWLEDGE_PAGE_KEYS, _map_knowledge_error
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/knowledge-bases")


class CollectionCoverage(BaseModel):
    total: int = Field(description="Currently readable files in this selection.")
    indexed: int = Field(description="Files with a current located text index.")
    read: int = Field(description="Files with retained, source-verified reading entries.")
    pending: int = Field(description="Files not yet extracted or currently being processed.")
    failed: int = Field(description="Files whose extraction failed or requires a password.")
    unsupported: int = Field(description="Files with unsupported formats.")


class KnowledgeCollection(BaseModel):
    id: str = Field(description="Public identifier of this independent personal selection.")
    kb_id: str
    target_document_id: str = Field(description="Original selected file or folder identifier.")
    target_path: str = Field(description="Current path of the selected original.")
    target_kind: Literal["file", "folder"]
    name: str
    keywords: list[str] = Field(description="User-confirmed keywords; initially empty.")
    status: Literal["ready", "partial", "pending"]
    coverage: CollectionCoverage
    created_at: int
    updated_at: int


class CreateCollectionsBody(BaseModel):
    document_ids: list[str] = Field(
        min_length=1,
        max_length=1000,
        description="Explicit selections. A parent folder and its child remain independent.",
    )


class UpdateCollectionBody(BaseModel):
    keywords: list[str] = Field(
        max_length=32, description="Keywords to retain; an empty list disables keyword extraction."
    )
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CollectionDocuments(BaseModel):
    collection: KnowledgeCollection
    keyword_document: str = Field(description="Permission-filtered keyword Markdown view.")
    reading_document: str = Field(
        description="Permission-filtered accumulated reading Markdown view."
    )
    storage_path: str = Field(
        description="Server-internal directory, separate from source folders."
    )


async def _run(
    server: OctopServer,
    request: Request,
    user: User,
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                operation, *args, actor_user_id=user.id, is_admin=bool(user.is_admin), **kwargs
            ),
        )
    except ValueError as exc:
        raise OctopError.localized(
            ErrorCode.KNOWLEDGE_COLLECTION_INVALID, resolve_request_locale(request)
        ) from exc
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


def _service(server: OctopServer) -> KnowledgeCollectionService:
    assert server.services is not None
    return KnowledgeCollectionService(server.services)


@router.get(
    "/{kb_id}/collections",
    response_model=list[KnowledgeCollection],
    summary="List the current user's independent file and folder collections",
)
async def list_collections(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(server, request, user, _service(server).list, kb_id)


@router.post(
    "/{kb_id}/collections",
    response_model=list[KnowledgeCollection],
    status_code=201,
    summary="Create one independent pair of documents for each explicit selection",
    description="Personal selections require source read access, not source write access. "
    "Keywords start empty. A selected folder does not create separate pairs for its descendants.",
)
async def create_collections(
    kb_id: str,
    body: CreateCollectionsBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(server, request, user, _service(server).create, kb_id, body.document_ids)


@router.patch(
    "/{kb_id}/collections/{collection_id}",
    response_model=KnowledgeCollection,
    summary="Update a personal collection's name and confirmed keywords",
)
async def update_collection(
    kb_id: str,
    collection_id: str,
    body: UpdateCollectionBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(
        server,
        request,
        user,
        _service(server).update,
        kb_id,
        collection_id,
        keywords=body.keywords,
        name=body.name,
    )


@router.post(
    "/{kb_id}/collections/{collection_id}/generate",
    response_model=KnowledgeCollection,
    summary="Refresh the two documents from current authorized source content",
    description="With keywords configured, missing text extraction uses the existing parser/OCR "
    "settings. Failures remain visible in coverage. Empty keywords never trigger a full-folder read.",
)
async def generate_collection(
    kb_id: str,
    collection_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(server, request, user, _service(server).generate, kb_id, collection_id)


@router.get(
    "/{kb_id}/collections/{collection_id}/documents",
    response_model=CollectionDocuments,
    summary="Read the current keyword and reading-index Markdown documents",
    description="Checks current owner, source access and indexed versions before rendering. "
    "Cached excerpts are candidates; verify the original segment before citing a fact.",
)
async def collection_documents(
    kb_id: str,
    collection_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(server, request, user, _service(server).documents, kb_id, collection_id)


@router.delete(
    "/{kb_id}/collections/{collection_id}",
    status_code=204,
    summary="Delete a personal collection and its two documents, never its originals",
)
async def delete_collection(
    kb_id: str,
    collection_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Response:
    await _run(server, request, user, _service(server).delete, kb_id, collection_id)
    return Response(status_code=204)
