"""Dashboard chat helpers: polish prompt and HITL resume (SSE)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from octop.api.common.agent import assert_agent_access
from octop.api.deps import current_user, get_server
from octop.api.routers.chat.models import ChatWelcomeResponse, HitlResumeBody
from octop.api.routers.chat.sse import format_sse
from octop.i18n.domains.stream import format_stream_error
from octop.infra.agents.experts.catalog import (
    default_welcome_payload,
    normalize_task_examples_for_display,
    parse_task_examples,
    read_workspace_manifest_data,
    welcome_payload_from_manifest_data,
    welcome_payload_has_content,
)
from octop.infra.agents.profile import welcome_from_row
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.hitl.coordinator import (
    HitlChannelCoordinator,
    HitlStreamContext,
    decision_rejection_reason,
)
from octop.infra.gateway.hitl.format import normalize_hitl_request, parse_action_requests
from octop.infra.gateway.hitl.store import HitlPendingRecord
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter()


@router.get(
    "/agents/{agent_id}/chat/welcome",
    summary="Chat welcome quick cards",
    response_model=ChatWelcomeResponse,
)
async def get_chat_welcome(
    agent_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Welcome copy for the empty chat screen.

    Resolution order:
    1. Agent row ``welcome_message`` (instance-owned; set at create/edit).
    2. Agent workspace ``.maitu/manifest.json`` (seeded at create; quick cards + fallback copy).
    3. Default quick cards (``general-assistant`` or a small built-in set).
    """
    assert_agent_access(server, agent_id, user)
    assert server.app_runtime is not None
    registry = server.app_runtime.agent_registry
    catalog = server.expert_catalog

    workspace = registry.workspace_for_agent(agent_id)
    payload: dict[str, Any] | None = None
    task_examples = None
    if workspace is not None:
        manifest = await read_workspace_manifest_data(workspace)
        if manifest is not None:
            welcome = welcome_payload_from_manifest_data(manifest)
            if welcome_payload_has_content(welcome):
                payload = welcome
            task_examples = normalize_task_examples_for_display(parse_task_examples(manifest))

    row = registry.get_row(agent_id)
    if payload is None:
        payload = default_welcome_payload(catalog)

    db_welcome = welcome_from_row(row) if row is not None else None
    if db_welcome is not None:
        payload = {**payload, "welcome_message": {"zh": db_welcome, "en": db_welcome}}
    hitl_enabled = bool(registry.security.load().hitl.enabled)
    return {
        **payload,
        "task_examples": task_examples,
        "hitl_enabled": hitl_enabled,
    }


async def iter_dashboard_hitl_resume_sse(
    *,
    processor: Any,
    hitl_coordinator: HitlChannelCoordinator,
    agent_id: str,
    thread_id: str,
    user_id: int,
    decisions: list[dict[str, Any]],
    pending: HitlPendingRecord | None,
    session_key: str,
    channel_type: str,
    locale: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    gateway: Any | None = None,
) -> AsyncIterator[str]:
    """Stream dashboard HITL resume chunks and persist any nested ``hitl_required``.

    Follow-up interrupts during resume must land in the pending store so history
    reload / refresh can reinject the approval card (same as the initial turn).
    """
    rejected = any(isinstance(d, dict) and d.get("type") == "reject" for d in decisions)
    hitl_ctx = HitlStreamContext(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        session_key=session_key,
        channel_type=channel_type,
    )
    disconnected = False
    steer_registry = getattr(gateway, "steer", None)
    steering_turn_id = (
        await steer_registry.active_turn_id(thread_id) if steer_registry is not None else None
    )
    if steer_registry is not None and steering_turn_id is not None:
        # A valid approval decision resumes this exact parked logical turn.
        await steer_registry.set_parked(thread_id, steering_turn_id, False)
    # Remove the resolved card before the resumed turn starts streaming, so
    # a concurrent history reload cannot re-inject it.
    if pending is not None:
        hitl_coordinator.store.mark_resolved(
            pending.pending_id,
            "rejected" if rejected else "approved",
        )
    try:
        async for chunk in processor.iter_hitl_resume_chunks(
            agent_id=agent_id,
            thread_id=thread_id,
            user_id=user_id,
            decisions=decisions,
        ):
            if not disconnected:
                disconnected = await is_disconnected()
            if isinstance(chunk, dict) and chunk.get("type") == "hitl_required":
                if steering_turn_id is not None and steer_registry is not None:
                    await steer_registry.set_parked(thread_id, steering_turn_id, True)
                request_payload = chunk.get("request")
                if isinstance(request_payload, dict):
                    request_payload = {
                        **normalize_hitl_request(request_payload),
                        "action_requests": parse_action_requests(request_payload),
                    }
                    record = hitl_coordinator.register_from_request(
                        request_payload,
                        ctx=hitl_ctx,
                    )
                    request_payload["pending_id"] = record.pending_id
                    chunk["request"] = request_payload
            if not disconnected:
                yield format_sse("chunk", chunk)

        if steering_turn_id is not None and gateway is not None:
            await gateway.complete_steering_turn(thread_id, steering_turn_id)
        if not disconnected:
            yield format_sse("chunk", {"type": "done"})
    except Exception as exc:
        if steering_turn_id is not None and gateway is not None:
            await gateway.complete_steering_turn(
                thread_id,
                steering_turn_id,
                cancelled=True,
            )
        if pending is not None:
            hitl_coordinator.store.mark_resolved(pending.pending_id, "expired")
        yield format_sse(
            "chunk",
            {"type": "error", "message": format_stream_error(exc, locale)},
        )


def _dashboard_hitl_stream_context(
    server: Any,
    *,
    agent_id: str,
    thread_id: str,
    user_id: int,
    pending: HitlPendingRecord | None,
) -> tuple[str, str]:
    if pending is not None:
        return pending.session_key, pending.channel_type
    row = server.app_runtime.gateway.thread_registry.get_thread(thread_id)
    if row is not None:
        return row.session_key, row.channel_type or "dashboard"
    return f"{agent_id}:dashboard:{user_id}:dm", "dashboard"


@router.post("/agents/{agent_id}/chat/hitl/resume", summary="Resume HITL approval (SSE)")
async def resume_hitl(
    agent_id: str,
    body: HitlResumeBody,
    request: Request,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Resume a paused human-in-the-loop tool approval and stream subsequent chunks."""
    assert_agent_access(server, agent_id, user)
    processor = server.app_runtime.gateway.processor
    hitl_coordinator = processor.hitl_coordinator
    pending_id = (body.pending_id or "").strip()
    pending = hitl_coordinator.store.get(pending_id) if pending_id else None
    thread = server.app_runtime.gateway.thread_registry.get_thread(body.thread_id)
    if (
        pending is None
        or pending.status != "pending"
        or pending.pending_id != pending_id
        or pending.thread_id != body.thread_id
        or pending.agent_id != agent_id
        or pending.user_id != user.id
        or thread is None
        or thread.agent_id != agent_id
        or thread.user_id != user.id
        or pending.session_key != thread.session_key
    ):
        raise OctopError.localized(ErrorCode.HITL_APPROVAL_STALE)
    session_key, channel_type = _dashboard_hitl_stream_context(
        server,
        agent_id=agent_id,
        thread_id=body.thread_id,
        user_id=user.id,
        pending=pending,
    )
    reason = decision_rejection_reason(pending, body.decisions)
    if reason is not None:
        raise HTTPException(status_code=400, detail=reason)
    if body.hitl_policy is not None:
        hitl_coordinator.session_policies.set(body.thread_id, body.hitl_policy.model_dump())

    async def gen() -> AsyncIterator[str]:
        async for frame in iter_dashboard_hitl_resume_sse(
            processor=processor,
            hitl_coordinator=hitl_coordinator,
            agent_id=agent_id,
            thread_id=body.thread_id,
            user_id=user.id,
            decisions=body.decisions,
            pending=pending,
            session_key=session_key,
            channel_type=channel_type,
            locale=resolve_request_locale(request),
            is_disconnected=request.is_disconnected,
            gateway=server.app_runtime.gateway,
        ):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")
