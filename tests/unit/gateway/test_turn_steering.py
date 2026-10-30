from __future__ import annotations

import asyncio

import pytest

from octop.infra.gateway.steering import SteeringConflict, TurnSteeringRegistry


@pytest.mark.asyncio
async def test_steers_drain_one_at_each_safe_boundary_and_followups_are_preserved() -> None:
    registry = TurnSteeringRegistry()
    thread = "thread-a"
    active = await registry.begin_turn(thread, "initial")
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="steer-1",
        kind="steer",
        text="change direction",
    )
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="follow-1",
        kind="followup",
        text="then summarize",
        payload={"authenticated": True},
    )
    item = await registry.take_at_boundary(thread, active)
    assert item is not None and item.client_turn_id == "steer-1"
    assert await registry.take_at_boundary(thread, active) is None

    completion = await registry.complete_turn(thread, active)
    assert completion.next_followup is not None
    assert completion.next_followup.client_turn_id == "follow-1"
    assert completion.next_followup.payload == {"authenticated": True}
    assert completion.next_turn_id is not None


@pytest.mark.asyncio
async def test_stale_turn_duplicate_submit_withdraw_and_hitl_park_are_rejected() -> None:
    registry = TurnSteeringRegistry()
    thread = "thread-b"
    active = await registry.begin_turn(thread, "initial")
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="queued-1",
        kind="steer",
        text="please adjust",
    )
    with pytest.raises(SteeringConflict):
        await registry.submit(
            thread,
            expected_active_turn_id="stale",
            client_turn_id="queued-2",
            kind="steer",
            text="wrong turn",
        )
    with pytest.raises(SteeringConflict):
        await registry.submit(
            thread,
            expected_active_turn_id=active,
            client_turn_id="queued-1",
            kind="steer",
            text="duplicate",
        )
    assert (
        await registry.withdraw(thread, expected_active_turn_id=active, client_turn_id="queued-1")
    ).status == "withdrawn"
    assert await registry.set_parked(thread, active, True)
    with pytest.raises(SteeringConflict):
        await registry.submit(
            thread,
            expected_active_turn_id=active,
            client_turn_id="queued-3",
            kind="followup",
            text="approval still pending",
        )
    assert await registry.take_at_boundary(thread, active) is None
    assert await registry.set_parked(thread, active, False)


@pytest.mark.asyncio
async def test_idle_guard_excludes_turn_admission_until_critical_section_finishes() -> None:
    registry = TurnSteeringRegistry()
    admission: asyncio.Task[str] | None = None
    async with registry.idle_guard("thread-delete") as idle:
        assert idle
        admission = asyncio.create_task(registry.begin_turn("thread-delete", "new-turn"))
        await asyncio.sleep(0)
        assert not admission.done()
    assert admission is not None
    assert await admission


@pytest.mark.asyncio
async def test_completion_is_turn_scoped_and_cancel_reports_accepted_items() -> None:
    registry = TurnSteeringRegistry()
    thread = "thread-c"
    active = await registry.begin_turn(thread, "initial")
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="accepted-1",
        kind="followup",
        text="next turn",
    )
    stale = await registry.complete_turn(thread, "not-current")
    assert stale.next_followup is None
    assert await registry.active_turn_id(thread) == active

    result = await registry.complete_turn(thread, active, cancelled=True)
    assert result.next_followup is None
    assert [item.client_turn_id for item in result.discarded] == ["accepted-1"]
    assert await registry.active_turn_id(thread) is None


@pytest.mark.asyncio
async def test_expired_parked_turn_is_reported_as_discarded() -> None:
    registry = TurnSteeringRegistry(item_ttl_seconds=0.01)
    thread = "thread-d"
    active = await registry.begin_turn(thread, "initial")
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="expires",
        kind="followup",
        text="too late",
    )
    await registry.set_parked(thread, active, True)
    await __import__("asyncio").sleep(0.02)
    expired = await registry.sweep_expired(thread)
    assert [item.client_turn_id for item in expired] == ["expires"]
    assert await registry.active_turn_id(thread) is None


@pytest.mark.asyncio
async def test_model_boundary_emits_drained_receipt_for_exact_client_item() -> None:
    registry = TurnSteeringRegistry()
    receipts: list[tuple[str, str, str]] = []

    async def receive(thread_id: str, event: str, item: object) -> None:
        receipts.append((thread_id, event, item.client_turn_id))

    registry.set_receipt_callback(receive)
    thread = "thread-e"
    active = await registry.begin_turn(thread, "initial")
    await registry.submit(
        thread,
        expected_active_turn_id=active,
        client_turn_id="steer-drained",
        kind="steer",
        text="reorient",
    )

    assert await registry.take_at_boundary(thread, active) is not None
    assert receipts == [(thread, "steer.drained", "steer-drained")]
