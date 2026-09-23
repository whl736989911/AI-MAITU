"""A submitted run reaches the turn — typed, and nothing else does.

The dashboard's input card sends one turn per run. That turn also carries keys the
*server* decides (whether the caller is an administrator, the model that was
resolved), so the run travels as a field of its own rather than as free-form
metadata: what a client can write has to stay a closed set, and this pins that it is.
"""

from __future__ import annotations

from typing import Any

from octop.api.routers.chat.models import ChatTurnBody, UserTurnWsFrame
from octop.api.routers.chat.turn import PreparedDashboardTurn, build_dashboard_inbound
from octop.infra.agents.feature_workflow import FEATURE_RUN_META_KEY


def _prepared() -> PreparedDashboardTurn:
    return PreparedDashboardTurn(
        thread_id="thr-1",
        session_key="sk",
        mcp_servers=None,
        skills=None,
        model_ref=None,
        inbound_content=[],
        composer_context=None,
        inbound_attachments=[],
    )


def _inbound(frame: UserTurnWsFrame) -> Any:
    return build_dashboard_inbound(
        agent_id="feat-quote-helper",
        user_id=42,
        turn=frame.to_turn_body(),
        prepared=_prepared(),
        ws_connection_id="ws-1",
        user_is_admin=False,
    )


def test_a_submitted_run_reaches_the_turn_as_metadata() -> None:
    frame = UserTurnWsFrame(
        text="▶ 运行",
        feature_run={
            "inputs": {"customer_name": "ACME"},
            "attachments": ["inbound/quote.pdf"],
        },
    )

    inbound = _inbound(frame)

    assert inbound.metadata[FEATURE_RUN_META_KEY] == {
        "inputs": {"customer_name": "ACME"},
        "attachments": ["inbound/quote.pdf"],
    }


def test_a_turn_without_a_run_carries_no_run_metadata() -> None:
    inbound = _inbound(UserTurnWsFrame(text="你好"))

    assert FEATURE_RUN_META_KEY not in inbound.metadata


def test_a_run_that_is_not_an_object_is_dropped_when_the_frame_is_normalized() -> None:
    """A malformed payload on the raw path becomes "no run", not a crashed turn."""
    body = ChatTurnBody.from_ws_payload({"text": "你好", "feature_run": "nope"})

    assert body.feature_run is None


def test_the_run_field_cannot_carry_server_decided_metadata() -> None:
    """The frame's own fields are a closed set: unknown keys are dropped, not trusted."""
    frame = UserTurnWsFrame.model_validate(
        {
            "type": "user_turn",
            "text": "你好",
            "user_is_admin": True,
            "metadata": {"octop_feature_run": {"inputs": {"a": "b"}}},
        }
    )

    inbound = _inbound(frame)

    assert inbound.metadata["user_is_admin"] is False
    assert FEATURE_RUN_META_KEY not in inbound.metadata
