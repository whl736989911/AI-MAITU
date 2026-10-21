"""Thread-scoped, turn-identified steering queues for dashboard chat.

The registry owns only control messages. The processor remains the single owner
of turn execution, thread locks, tool invocation and HITL approval state; it must
call :meth:`take_at_boundary` immediately before an actual model/tool boundary.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

SteeringKind = Literal["steer", "followup"]


class SteeringConflict(ValueError):
    """The client referenced a stale, parked, or absent active turn."""


@dataclass(frozen=True, slots=True)
class SteeringItem:
    client_turn_id: str
    kind: SteeringKind
    text: str
    created_at: float
    expires_at: float
    payload: Any = None


@dataclass(slots=True)
class ActiveTurn:
    turn_id: str
    client_turn_id: str
    started_at: float
    parked: bool = False
    parked_at: float | None = None
    steers: deque[SteeringItem] = field(default_factory=deque)
    followups: deque[SteeringItem] = field(default_factory=deque)
    expired: deque[SteeringItem] = field(default_factory=deque)
    seen_client_turn_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class SteeringCompletion:
    completed_turn_id: str
    next_turn_id: str | None
    next_followup: SteeringItem | None
    discarded: tuple[SteeringItem, ...] = ()


@dataclass(frozen=True, slots=True)
class SteeringResult:
    accepted: bool
    status: Literal["accepted", "withdrawn", "rejected", "expired", "stale"]
    turn_id: str | None
    client_turn_id: str | None


class TurnSteeringRegistry:
    """Concurrency-safe, in-memory steering controls keyed by conversation thread.

    A follow-up is retained until the processor claims it after the active turn
    completes. A steer is consumed exactly once at a processor-declared safe
    boundary. No model/tool work is invoked by this class.
    """

    def __init__(
        self,
        *,
        item_ttl_seconds: float = 1800.0,
        max_followups: int = 8,
        max_steers: int = 8,
    ) -> None:
        if item_ttl_seconds <= 0 or max_followups < 1 or max_steers < 1:
            raise ValueError("steering limits must be positive")
        self._ttl = item_ttl_seconds
        self._max_followups = max_followups
        self._max_steers = max_steers
        self._active: dict[str, ActiveTurn] = {}
        self._locks: dict[str, asyncio.Lock] = {}

        self._receipt_callback: Any = None

    def set_receipt_callback(self, callback: Any) -> None:
        """Register a gateway sink for terminal model-boundary receipts."""
        self._receipt_callback = callback

    async def _emit_receipt(self, thread_id: str, receipt_type: str, item: SteeringItem) -> None:
        callback = self._receipt_callback
        if callback is not None:
            try:
                await callback(thread_id, receipt_type, item)
            except Exception:
                # Receipt delivery must not interrupt model execution.
                return

    def _lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    async def begin_turn(self, thread_id: str, client_turn_id: str) -> str:
        """Register a turn and return its server-generated identity."""
        thread_id = thread_id.strip()
        client_turn_id = client_turn_id.strip()
        if not thread_id or not client_turn_id:
            raise ValueError("thread_id and client_turn_id are required")
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is not None:
                raise SteeringConflict("thread already has an active turn")
            turn_id = uuid.uuid4().hex
            self._active[thread_id] = ActiveTurn(turn_id, client_turn_id, time.monotonic())
            return turn_id

    async def active_turn_id(self, thread_id: str) -> str | None:
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            return current.turn_id if current else None

    async def set_parked(self, thread_id: str, turn_id: str, parked: bool) -> bool:
        """Mark a running turn parked during HITL; stale turn ids are ignored."""
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != turn_id:
                return False
            current.parked = parked
            current.parked_at = time.monotonic() if parked else None
            return True

    async def submit(
        self,
        thread_id: str,
        *,
        expected_active_turn_id: str,
        client_turn_id: str,
        kind: SteeringKind,
        text: str,
        payload: Any = None,
    ) -> SteeringResult:
        thread_id = thread_id.strip()
        client_turn_id = client_turn_id.strip()
        text = text.strip()
        if not thread_id or not expected_active_turn_id or not client_turn_id or not text:
            raise ValueError("thread, active turn, client turn, and text are required")
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != expected_active_turn_id or current.parked:
                raise SteeringConflict("active turn is stale or waiting for approval")
            if client_turn_id in current.seen_client_turn_ids:
                raise SteeringConflict("client_turn_id has already been used")
            self._expire(current)
            now = time.monotonic()
            item = SteeringItem(client_turn_id, kind, text, now, now + self._ttl, payload)
            queue = current.steers if kind == "steer" else current.followups
            queue_limit = self._max_steers if kind == "steer" else self._max_followups
            if len(queue) >= queue_limit:
                raise SteeringConflict(f"{kind} queue is full")
            queue.append(item)
            current.seen_client_turn_ids.add(client_turn_id)
            return SteeringResult(True, "accepted", current.turn_id, client_turn_id)

    async def withdraw(
        self,
        thread_id: str,
        *,
        expected_active_turn_id: str,
        client_turn_id: str,
    ) -> SteeringResult:
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != expected_active_turn_id:
                raise SteeringConflict("active turn is stale")
            for queue in (current.steers, current.followups):
                retained = deque(item for item in queue if item.client_turn_id != client_turn_id)
                if len(retained) != len(queue):
                    queue.clear()
                    queue.extend(retained)
                    return SteeringResult(True, "withdrawn", current.turn_id, client_turn_id)
            return SteeringResult(False, "stale", current.turn_id, client_turn_id)

    async def reject(self, thread_id: str, *, expected_active_turn_id: str) -> SteeringResult:
        """Reject steering for this active run without mutating HITL decisions."""
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != expected_active_turn_id:
                raise SteeringConflict("active turn is stale")
            current.steers.clear()
            current.followups.clear()
            return SteeringResult(True, "rejected", current.turn_id, None)

    async def take_at_boundary(self, thread_id: str, turn_id: str) -> SteeringItem | None:
        """Atomically drain one pending steering instruction at a safe boundary."""
        item: SteeringItem | None = None
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != turn_id or current.parked:
                return None
            self._expire(current)
            while current.steers:
                candidate = current.steers.popleft()
                if candidate.expires_at > time.monotonic():
                    item = candidate
                    break
        if item is not None:
            await self._emit_receipt(thread_id, "steer.drained", item)
        return item

    async def complete_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        cancelled: bool = False,
    ) -> SteeringCompletion:
        """Complete only the matching turn, preserving every accepted queued item."""
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None or current.turn_id != turn_id:
                return SteeringCompletion(turn_id, None, None)
            self._expire(current)
            expired = tuple(current.expired)
            current.expired.clear()
            if cancelled:
                discarded = (*expired, *current.steers, *current.followups)
                self._active.pop(thread_id, None)
                return SteeringCompletion(turn_id, None, None, tuple(discarded))
            pending = sorted(
                (*current.steers, *current.followups),
                key=lambda item: item.created_at,
            )
            current.steers.clear()
            current.followups.clear()
            if not pending:
                self._active.pop(thread_id, None)
                return SteeringCompletion(turn_id, None, None, tuple(expired))
            first, *remaining = pending
            current.followups.extend(
                SteeringItem(
                    item.client_turn_id,
                    "followup",
                    item.text,
                    item.created_at,
                    item.expires_at,
                    item.payload,
                )
                for item in remaining
            )
            next_turn_id = uuid.uuid4().hex
            current.turn_id = next_turn_id
            current.client_turn_id = first.client_turn_id
            current.started_at = time.monotonic()
            current.parked = False
            current.parked_at = None
            return SteeringCompletion(
                turn_id,
                next_turn_id,
                SteeringItem(
                    first.client_turn_id,
                    "followup",
                    first.text,
                    first.created_at,
                    first.expires_at,
                    first.payload,
                ),
                tuple(expired),
            )

    def _expire(self, current: ActiveTurn) -> tuple[SteeringItem, ...]:
        now = time.monotonic()
        expired = tuple(
            item for item in (*current.steers, *current.followups) if item.expires_at <= now
        )
        current.steers = deque(item for item in current.steers if item.expires_at > now)
        current.followups = deque(item for item in current.followups if item.expires_at > now)
        current.expired.extend(expired)
        return expired

    async def queue_length(self, thread_id: str) -> int:
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None:
                return 0
            self._expire(current)
            return len(current.steers) + len(current.followups)

    async def sweep_expired(self, thread_id: str) -> tuple[SteeringItem, ...]:
        async with self._lock(thread_id):
            current = self._active.get(thread_id)
            if current is None:
                return ()
            self._expire(current)
            if (
                current.parked
                and current.parked_at is not None
                and current.parked_at + self._ttl <= time.monotonic()
            ):
                current.expired.extend((*current.steers, *current.followups))
                current.steers.clear()
                current.followups.clear()
                self._active.pop(thread_id, None)
            expired = tuple(current.expired)
            current.expired.clear()
            return expired


__all__ = [
    "ActiveTurn",
    "SteeringCompletion",
    "SteeringConflict",
    "SteeringItem",
    "SteeringResult",
    "TurnSteeringRegistry",
]
