"""Dashboard chat over WebSocket — routes turns through Gateway / GlobalProcessor."""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from octop.api.common.agent import assert_agent_access
from octop.api.deps import resolve_user_from_token
from octop.api.routers.chat.models import UserTurnWsFrame
from octop.api.routers.chat.sse import json_chunk_default
from octop.api.routers.chat.turn import (
    _turn_plain_text,
    build_dashboard_inbound,
    prepare_dashboard_turn,
    turn_has_content,
)
from octop.infra.errors import OctopError
from octop.infra.gateway.steering import SteeringConflict
from octop.infra.gateway.ws.ws_channel import WS_CHANNEL_ID

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/agents/{agent_id}/chat/ws")
async def dashboard_chat_ws(
    websocket: WebSocket,
    agent_id: str,
    token: str | None = Query(default=None),
) -> None:
    """Bidirectional Dashboard chat. Wire protocol mirrors harness stream chunks."""
    server = websocket.app.state.octop_server
    if not token:
        await websocket.close(code=4001, reason="missing token")
        return

    try:
        user = resolve_user_from_token(server, token)
    except OctopError as exc:
        await websocket.close(code=4001, reason=f"auth: {exc.code.value}")
        return

    assert server.app_runtime is not None  # noqa: S101
    gateway = server.app_runtime.gateway
    hub = gateway.ws_hub
    channel_manager = gateway.channel_manager
    if channel_manager is None:
        await websocket.close(code=1011, reason="gateway not ready")
        return

    try:
        assert_agent_access(server, agent_id, user)
    except OctopError as exc:
        from octop.infra.errors import ErrorCode  # noqa: PLC0415

        code = 4003 if exc.code == ErrorCode.FORBIDDEN else 4404
        await websocket.close(code=code, reason=str(exc.code.value))
        return

    connection_id = uuid.uuid4().hex
    await websocket.accept()

    async def send_frame(frame: dict[str, Any]) -> None:
        if websocket.application_state != WebSocketState.CONNECTED:
            return
        await websocket.send_text(
            json.dumps(frame, ensure_ascii=False, default=json_chunk_default),
        )

    hub.register(connection_id, send_frame)

    try:
        while websocket.application_state == WebSocketState.CONNECTED:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"type": "user_turn", "text": raw}

            if not isinstance(payload, dict):
                continue

            msg_type = str(payload.get("type") or "user_turn")
            if msg_type == "ping":
                await send_frame({"type": "pong"})
                continue

            if msg_type == "subscribe":
                thread_id = str(payload.get("thread_id") or "").strip()
                if not thread_id:
                    await send_frame({"type": "error", "message": "subscribe requires thread_id"})
                    continue
                row = gateway.thread_registry.get_thread(thread_id)
                if row is None or row.agent_id != agent_id or row.user_id != user.id:
                    await send_frame(
                        {"type": "error", "message": f"thread {thread_id!r} not found"},
                    )
                    continue
                hub.subscribe(thread_id, connection_id)
                await send_frame(
                    {
                        "type": "turn_status",
                        "thread_id": thread_id,
                        "active": hub.is_turn_active(thread_id),
                        "active_turn_id": await gateway.steer.active_turn_id(thread_id),
                    },
                )
                expired = await gateway.steer.sweep_expired(thread_id)
                if expired:
                    await send_frame(
                        {
                            "type": "steer.expired",
                            "reason": "park_expired",
                            "client_pending_ids": [item.client_turn_id for item in expired],
                            "thread_id": thread_id,
                        }
                    )
                continue

            if msg_type == "steer_remove":
                thread_id = str(payload.get("thread_id") or "").strip()
                client_pending_id = str(payload.get("client_pending_id") or "").strip()
                expected_turn_id = str(
                    payload.get("expected_turn_id") or payload.get("turn_id") or ""
                ).strip()
                row = gateway.thread_registry.get_thread(thread_id) if thread_id else None
                if row is None or row.agent_id != agent_id or row.user_id != user.id:
                    await send_frame(
                        {
                            "type": "steer.ack",
                            "status": "rejected",
                            "reason": "forbidden",
                            "client_pending_id": client_pending_id,
                            "thread_id": thread_id,
                        }
                    )
                    continue
                try:
                    result = await gateway.steer.withdraw(
                        thread_id,
                        expected_active_turn_id=expected_turn_id,
                        client_turn_id=client_pending_id,
                    )
                except SteeringConflict:
                    await send_frame(
                        {
                            "type": "steer.ack",
                            "status": "rejected",
                            "reason": "expected_turn_id_mismatch",
                            "client_pending_id": client_pending_id,
                            "thread_id": thread_id,
                        }
                    )
                    continue
                await send_frame(
                    {
                        "type": "steer.ack",
                        "status": "removed" if result.accepted else "already_drained",
                        "client_pending_id": client_pending_id,
                        "turn_id": result.turn_id,
                        "thread_id": thread_id,
                    }
                )
                continue

            if msg_type == "cancel":
                thread_id = str(payload.get("thread_id") or "").strip()
                if not thread_id:
                    await send_frame({"type": "error", "message": "cancel requires thread_id"})
                    continue
                row = gateway.thread_registry.get_thread(thread_id)
                if row is None or row.agent_id != agent_id or row.user_id != user.id:
                    await send_frame(
                        {"type": "error", "message": f"thread {thread_id!r} not found"},
                    )
                    continue
                turn_id = await gateway.steer.active_turn_id(thread_id)
                expected_turn_id = str(payload.get("expected_turn_id") or "").strip()
                if turn_id is not None and expected_turn_id != turn_id:
                    await send_frame(
                        {
                            "type": "error",
                            "message": "active turn changed; refresh before cancelling",
                            "code": "stale_turn",
                            "turn_id": turn_id,
                            "thread_id": thread_id,
                        }
                    )
                    continue
                if turn_id is not None:
                    await gateway.complete_steering_turn(
                        thread_id,
                        turn_id,
                        cancelled=True,
                    )
                server.app_runtime.agent_registry.cancel_stream(agent_id, thread_id)
                continue

            if msg_type != "user_turn":
                await send_frame({"type": "error", "message": f"unknown message type: {msg_type}"})
                continue

            try:
                frame = UserTurnWsFrame.model_validate({**payload, "type": "user_turn"})
            except ValidationError as exc:
                await send_frame({"type": "error", "message": str(exc)})
                await send_frame({"type": "done"})
                continue

            turn = frame.to_turn_body()
            if not turn_has_content(turn):
                await send_frame({"type": "error", "message": "empty message"})
                await send_frame({"type": "done"})
                continue

            try:
                prepared = await prepare_dashboard_turn(
                    server,
                    agent_id=agent_id,
                    user=user,
                    turn=turn,
                )
            except OctopError as exc:
                await send_frame({"type": "error", "message": str(exc)})
                await send_frame({"type": "done"})
                continue

            inbound = build_dashboard_inbound(
                agent_id=agent_id,
                user_id=user.id,
                prepared=prepared,
                turn=turn,
                ws_connection_id=connection_id,
                user_is_admin=bool(getattr(user, "is_admin", False)),
            )
            thread_id = prepared.thread_id
            active_turn_id = await gateway.steer.active_turn_id(thread_id)
            delivery = turn.delivery or "auto"
            client_pending_id = turn.client_pending_id or uuid.uuid4().hex
            if active_turn_id is not None:
                if delivery == "steer" or delivery == "auto" or delivery == "follow_up":
                    kind = "followup" if delivery == "follow_up" else "steer"
                    try:
                        accepted = await gateway.steer.submit(
                            thread_id,
                            expected_active_turn_id=turn.expected_turn_id or "",
                            client_turn_id=client_pending_id,
                            kind=kind,
                            text=_turn_plain_text(turn),
                            payload=inbound,
                        )
                    except SteeringConflict as exc:
                        await send_frame(
                            {
                                "type": "steer.ack",
                                "status": "rejected",
                                "reason": str(exc),
                                "client_pending_id": client_pending_id,
                                "turn_id": active_turn_id,
                                "thread_id": thread_id,
                            }
                        )
                        continue
                    await send_frame(
                        {
                            "type": "steer.queued",
                            "client_pending_id": client_pending_id,
                            "pending_id": client_pending_id,
                            "turn_id": accepted.turn_id,
                            "queue_length": await gateway.steer.queue_length(thread_id),
                            "preview": _turn_plain_text(turn)[:200],
                            "thread_id": thread_id,
                        }
                    )
                    await send_frame(
                        {
                            "type": "steer.ack",
                            "status": "queued",
                            "client_pending_id": client_pending_id,
                            "turn_id": accepted.turn_id,
                            "thread_id": thread_id,
                        }
                    )
                    continue
            elif delivery == "steer":
                await send_frame(
                    {
                        "type": "steer.ack",
                        "status": "rejected",
                        "reason": "no_active_turn",
                        "client_pending_id": client_pending_id,
                        "thread_id": thread_id,
                    }
                )
                continue

            try:
                turn_id = await gateway.steer.begin_turn(thread_id, client_pending_id)
            except SteeringConflict:
                await send_frame(
                    {
                        "type": "steer.ack",
                        "status": "rejected",
                        "reason": "turn_active",
                        "client_pending_id": client_pending_id,
                        "thread_id": thread_id,
                    }
                )
                continue
            metadata = dict(inbound.metadata or {})
            metadata["octop_steering_turn_id"] = turn_id
            metadata["client_pending_id"] = client_pending_id
            inbound.metadata = metadata
            channel_manager.enqueue(WS_CHANNEL_ID, inbound)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("dashboard chat ws error agent=%s", agent_id)
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await send_frame({"type": "error", "message": "internal error"})
    finally:
        # Disconnect must not cancel an in-flight turn — clients may reconnect and
        # subscribe to continue receiving subsequent chunks (weak stream resume).
        hub.unregister(connection_id)
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()
