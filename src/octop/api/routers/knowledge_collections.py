"""Prompt-registered, source-ACL-shared collections with two Markdown views."""

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
    read: int = Field(description="Files with retained, source-verified reading evidence.")
    generated: int = Field(description="Files with a current prompt-backed topic result.")
    failed: int = Field(description="Files whose extraction failed or that need visual inspection.")
    pending: int = Field(description="Files not yet processed by the current prompt.")


class CollectionGeneration(BaseModel):
    state: Literal["idle", "running", "ready", "partial", "error"] = Field(
        description="State of the last explicit organize run over this collection."
    )
    error: str = Field(description="Failure reason of the last organize run, empty when none.")
    generated_at: int | None = Field(description="Completion time of the last organize run.")
    model_ref: str = Field(description="Provider/model that produced the current topic results.")


class KnowledgeCollection(BaseModel):
    id: str = Field(description="Public identifier of this source-bound selection.")
    kb_id: str
    target_document_id: str = Field(description="Original selected file or folder identifier.")
    target_path: str = Field(description="Current path of the selected original.")
    target_kind: Literal["file", "folder"]
    name: str
    prompt: str = Field(description="Freeform user prompt that directs topic organization.")
    created_by: int = Field(description="User that registered this collection.")
    status: Literal["pending", "partial", "ready", "error"]
    coverage: CollectionCoverage
    generation: CollectionGeneration
    created_at: int
    updated_at: int


class CreateCollectionsBody(BaseModel):
    document_ids: list[str] = Field(
        min_length=1,
        max_length=1000,
        description="Explicit selections. A parent folder and its child remain independent.",
    )
    prompt: str = Field(
        default="",
        max_length=2000,
        description="Freeform topical prompt. Optional: empty-prompt collections still "
        "accumulate reading evidence; organize requires a prompt. Nothing is read or "
        "parsed at creation.",
    )
    name: str | None = Field(default=None, min_length=1, max_length=200)


class UpdateCollectionBody(BaseModel):
    prompt: str | None = Field(
        default=None,
        max_length=2000,
        description="Replacement prompt; an empty string clears it and marks topic results stale.",
    )
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CollectionDocuments(BaseModel):
    collection: KnowledgeCollection
    topic_document: str = Field(
        description="Permission-filtered prompt-backed topic Markdown view."
    )
    reading_document: str = Field(
        description="Permission-filtered accumulated source-reading Markdown view."
    )
    storage_path: str = Field(
        description="Server-internal directory of the caller-filtered materialization."
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
    registry = getattr(server.app_runtime, "agent_registry", None) if server.app_runtime else None
    return KnowledgeCollectionService(server.services, agent_registry=registry)


@router.get(
    "/{kb_id}/collections",
    response_model=list[KnowledgeCollection],
    summary="List the collections this caller may see through the source ACL",
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
    summary="Register one collection per explicit selection with a freeform prompt",
    description="Creation requires source read access only and never reads or parses the "
    "originals. Use the organize endpoint to run prompt-directed extraction.",
)
async def create_collections(
    kb_id: str,
    body: CreateCollectionsBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> Any:
    return await _run(
        server,
        request,
        user,
        _service(server).create,
        kb_id,
        body.document_ids,
        prompt=body.prompt,
        name=body.name,
    )


@router.patch(
    "/{kb_id}/collections/{collection_id}",
    response_model=KnowledgeCollection,
    summary="Update a collection's prompt or name",
    description="Requires source write access on the target original or knowledge-base "
    "management; shared readers cannot change the prompt. Changing the prompt marks "
    "existing topic results stale until organize runs again.",
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
        prompt=body.prompt,
        name=body.name,
    )


@router.post(
    "/{kb_id}/collections/{collection_id}/generate",
    response_model=KnowledgeCollection,
    summary="Organize: run prompt-directed topical extraction over readable originals",
    description="Requires source write access on the target. Reads every readable original "
    "in scope through the authorized source service, then extracts prompt-directed topic "
    "results per source with the configured platform model. Concurrent organize runs are "
    "rejected; failures stay visible in generation state and coverage.",
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
    summary="Read the current topic and reading Markdown documents",
    description="Checks current source access before rendering; sources this caller cannot "
    "read never appear. Topic content is model-derived from stored evidence; verify the "
    "original source before citing a fact.",
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
    summary="Delete a collection and its two documents, never its originals",
    description="Requires source write access on the target original or knowledge-base management.",
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
