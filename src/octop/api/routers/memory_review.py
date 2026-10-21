from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from octop.api.common.agent import (
    AgentCapability,
    assert_agent_capability_write,
    require_agent_row,
)
from octop.api.common.memory_client import (
    MemoryScope,
    call_memory_rpc,
    open_memory_scope,
    resolve_memory_access,
)
from octop.api.deps import current_user, get_server
from octop.infra.agents.memory_context import (
    document_lock_path,
    read_private_document,
    read_workspace_document,
)
from octop.infra.agents.memory_review import (
    ReviewReceipt,
    _configured_model,
    apply_review_changes,
    discard_receipt,
    facts_digest,
    save_receipt,
    take_receipt,
)
from octop.infra.agents.memory_review import (
    review as semantic_review,
)
from octop.infra.errors import ErrorCode, OctopError

router = APIRouter()


class ReviewRequest(BaseModel):
    scope: MemoryScope | None = None


class ApplyRequest(BaseModel):
    scope: MemoryScope | None = None
    receipt: str
    fact_ids: list[str] = Field(min_length=1, max_length=160)


class MemoryReviewConflict(BaseModel):
    id: str
    fact: str
    document: Literal["USER.md", "MEMORY.md"]
    evidence: str
    relation: Literal["contradiction", "duplicate"]


class MemoryReviewCoverage(BaseModel):
    facts_reviewed: int
    facts_total: int
    complete: bool


class ReviewResponse(BaseModel):
    status: Literal["reviewed", "unavailable"]
    reason: str | None = None
    message: str | None = None
    receipt: str | None = None
    scope: MemoryScope
    manual_authoritative: bool = False
    coverage: MemoryReviewCoverage
    conflicts: list[MemoryReviewConflict] = Field(default_factory=list)


class ApplyResponse(BaseModel):
    status: Literal["applied"]
    action: Literal["kept_manual_deprecated_facts"]
    deprecated_fact_ids: list[str]


async def _read_docs(server: Any, agent_id: str, scope: MemoryScope, user: Any) -> list[Any]:
    if scope == "private":
        memory = await open_memory_scope(
            agent_id=agent_id, scope="private", user=user, server=server
        )
        return await asyncio.gather(
            *(
                asyncio.to_thread(read_private_document, memory, name)
                for name in ("USER.md", "MEMORY.md")
            )
        )
    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
    return await asyncio.gather(
        *(
            asyncio.to_thread(read_workspace_document, workspace, name)
            for name in ("USER.md", "MEMORY.md")
        )
    )


def _scope(access: Any, requested: MemoryScope | None) -> MemoryScope:
    selected = requested or access.default_scope
    if selected not in {"shared", "private"}:
        raise OctopError(ErrorCode.FORBIDDEN, "memory scope is unavailable")
    if selected == "private" and not access.private_writable:
        raise OctopError(
            ErrorCode.FORBIDDEN, "private memory is available only to the authenticated caller"
        )
    return selected


async def _facts(
    agent_id: str, scope: MemoryScope, user: Any, server: Any
) -> tuple[list[dict[str, Any]], int, bool]:
    result = await call_memory_rpc(
        agent_id=agent_id,
        method="list_atoms",
        params={
            "include_deprecated": False,
            "limit": 160,
            "offset": 0,
            "order_by": "created_at",
            "order": "desc",
        },
        user=user,
        as_user=None,
        server=server,
        scope=scope,
    )
    raw = result.get("items", []) if isinstance(result, dict) else []
    if not isinstance(raw, list):
        raise OctopError(ErrorCode.INTERNAL_ERROR, "memory returned an invalid fact list")
    facts = [
        item for item in raw if isinstance(item, dict) and isinstance(item.get("assertion"), str)
    ][:160]
    total = int(result.get("total", len(facts))) if isinstance(result, dict) else len(facts)
    partial = (
        bool(result.get("has_more", total > len(facts))) if isinstance(result, dict) else False
    )
    return facts, total, partial


@router.post(
    "/agents/{agent_id}/memory/review",
    summary="Review manual memory against structured facts",
    response_model=ReviewResponse,
)
async def review_memory(
    agent_id: str,
    body: ReviewRequest,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    access = await resolve_memory_access(agent_id, user=user, as_user=None, server=server)
    scope = _scope(access, body.scope)
    docs = await _read_docs(server, agent_id, scope, user)
    facts, total, partial = await _facts(agent_id, scope, user, server)
    model = _configured_model(server, agent_id)
    if model is None:
        return {
            "status": "unavailable",
            "reason": "no_configured_model",
            "scope": scope,
            "coverage": {"facts_reviewed": 0, "facts_total": total, "complete": not partial},
        }
    try:
        conflicts = await asyncio.to_thread(
            semantic_review,
            model,
            facts,
            [{"name": doc.name, "text": doc.text} for doc in docs],
        )
    except Exception:
        return {
            "status": "unavailable",
            "reason": "model_review_failed",
            "message": "The configured model could not complete a valid review.",
            "scope": scope,
            "coverage": {
                "facts_reviewed": len(facts),
                "facts_total": total,
                "complete": not partial,
            },
        }
    snapshots = {doc.name: doc.snapshot for doc in docs}
    keyed_facts = {str(fact.get("atom_id") or fact.get("id")): fact for fact in facts}
    token = save_receipt(
        ReviewReceipt(
            int(user.id),
            agent_id,
            scope,
            snapshots,
            facts_digest(facts),
            keyed_facts,
            conflicts,
            time.time() + 900,
        )
    )
    return {
        "status": "reviewed",
        "receipt": token,
        "scope": scope,
        "manual_authoritative": True,
        "coverage": {"facts_reviewed": len(facts), "facts_total": total, "complete": not partial},
        "conflicts": conflicts,
    }


@router.post(
    "/agents/{agent_id}/memory/review/apply",
    summary="Keep manual memory and deactivate selected contradictory facts",
    response_model=ApplyResponse,
)
async def apply_memory_review(
    agent_id: str,
    body: ApplyRequest,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    access = await resolve_memory_access(agent_id, user=user, as_user=None, server=server)
    scope = _scope(access, body.scope)
    receipt = take_receipt(body.receipt, user_id=int(user.id), agent_id=agent_id, scope=scope)
    if receipt is None:
        raise HTTPException(
            status_code=409,
            detail="review receipt is stale or belongs to another user, agent, or scope",
        )
    selected = sorted(set(body.fact_ids))
    contradictions = {
        str(conflict["id"])
        for conflict in receipt.conflicts
        if conflict.get("relation") == "contradiction"
    }
    if not selected or not set(selected).issubset(contradictions):
        raise HTTPException(status_code=400, detail="select only reviewed contradictory facts")

    registry = server.app_runtime.agent_registry
    loop = asyncio.get_running_loop()
    lock_paths = [
        document_lock_path(
            server.paths,
            agent_id,
            scope=scope,
            user_id=int(user.id) if scope == "private" else None,
            name=name,
        )
        for name in ("USER.md", "MEMORY.md")
    ]

    def check_access() -> None:
        future = asyncio.run_coroutine_threadsafe(
            resolve_memory_access(agent_id, user=user, as_user=None, server=server), loop
        )
        latest = future.result()
        latest_scope = _scope(latest, scope)
        writable = latest.private_writable if latest_scope == "private" else latest.shared_writable
        if not writable:
            raise OctopError(ErrorCode.FORBIDDEN, "this memory scope is read-only")
        row = require_agent_row(agent_id, user=user, as_user=None, server=server)
        assert_agent_capability_write(
            row,
            user,
            AgentCapability.MEMORY,
            memory_stage=latest.stage,
            memory_scope=latest_scope,
            server=server,
        )
        coordinator = registry.memory_slim
        status = coordinator.status(agent_id) if coordinator is not None else None
        if isinstance(status, dict) and status.get("phase") in {
            "backing_up",
            "deduplicating",
            "compacting",
        }:
            raise OctopError.localized(ErrorCode.AGENT_BUSY)

    async with registry._track_invocation(agent_id):
        memory = await open_memory_scope(agent_id=agent_id, scope=scope, user=user, server=server)
        workspace = registry.workspace_for_agent(agent_id) if scope == "shared" else None

        def read_documents() -> list[Any]:
            if scope == "private":
                return [read_private_document(memory, name) for name in ("USER.md", "MEMORY.md")]
            if workspace is None:
                raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
            return [read_workspace_document(workspace, name) for name in ("USER.md", "MEMORY.md")]

        operation = asyncio.create_task(
            asyncio.to_thread(
                apply_review_changes,
                receipt,
                selected,
                lock_paths,
                memory=memory,
                read_documents=read_documents,
                check_access=check_access,
            )
        )
        try:
            applied_ids = await asyncio.shield(operation)
        except asyncio.CancelledError:
            await operation
            raise
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    discard_receipt(body.receipt)
    return {
        "status": "applied",
        "action": "kept_manual_deprecated_facts",
        "deprecated_fact_ids": applied_ids,
    }
