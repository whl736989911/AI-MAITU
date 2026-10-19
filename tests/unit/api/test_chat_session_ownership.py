"""Unit tests for dashboard thread resolution's session ownership checks.

AG-01: a dashboard turn used to trust a client-supplied ``session_key`` and
resolve it to whatever thread that key was bound to. These tests pin the
fail-closed checks in ``resolve_thread_id`` without booting a server; the
WebSocket path is covered end-to-end by
``tests/integration/test_chat_ws_injection.py`` (it calls the same function).
"""

from __future__ import annotations

import pytest

from octop.api.routers.chat.turn import resolve_thread_id
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry

AGENT = "shared-agent"
OTHER_AGENT = "other-agent"
ALICE = 1
BOB = 2


class _FakeSession:
    def __init__(self, *, agent_id: str, user_id: int) -> None:
        self.agent_id = agent_id
        self.user_id = user_id


class _FakeThread:
    def __init__(self, *, agent_id: str, user_id: int) -> None:
        self.agent_id = agent_id
        self.user_id = user_id


class _FakeRegistry:
    """Only the surface ``resolve_thread_id`` is allowed to depend on."""

    def __init__(self) -> None:
        self.sessions: dict[str, _FakeSession] = {}
        self.threads: dict[str, _FakeThread] = {}
        self.bindings: dict[str, str] = {}
        self.rebound: list[tuple[str, str, str]] = []
        self.created: list[tuple[str, str, int, str]] = []

    def get_session(self, session_key: str) -> _FakeSession | None:
        return self.sessions.get(session_key)

    def get_thread(self, thread_id: str) -> _FakeThread | None:
        return self.threads.get(thread_id)

    def get_bound_thread_id(self, session_key: str) -> str | None:
        bound = self.bindings.get(session_key)
        return bound if bound is not None and bound in self.threads else None

    async def rebind(self, *, session_key: str, thread_id: str, agent_id: str) -> None:
        self.rebound.append((session_key, thread_id, agent_id))

    async def get_or_create_by_key(
        self, *, session_key: str, agent_id: str, user_id: int, channel_type: str
    ) -> str:
        self.created.append((session_key, agent_id, user_id, channel_type))
        return "thr-created"


def _key(agent_id: str = AGENT, user_id: int = BOB) -> str:
    return ThreadRegistry.dashboard_key(agent_id=agent_id, user_id=user_id)


async def test_another_users_session_key_is_refused() -> None:
    registry = _FakeRegistry()
    alice_key = _key(user_id=ALICE)
    registry.sessions[alice_key] = _FakeSession(agent_id=AGENT, user_id=ALICE)
    registry.threads["thr-alice"] = _FakeThread(agent_id=AGENT, user_id=ALICE)
    registry.bindings[alice_key] = "thr-alice"

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id=None,
            session_key=alice_key,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert registry.created == []
    assert registry.rebound == []


async def test_another_agents_session_key_is_refused() -> None:
    registry = _FakeRegistry()
    foreign_key = _key(agent_id=OTHER_AGENT, user_id=BOB)
    registry.sessions[foreign_key] = _FakeSession(agent_id=OTHER_AGENT, user_id=BOB)

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id=None,
            session_key=foreign_key,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert registry.created == []


async def test_a_poisoned_session_row_under_own_key_is_refused() -> None:
    """A row stored under the caller's predictable key by somebody else."""
    registry = _FakeRegistry()
    own_key = _key()
    registry.sessions[own_key] = _FakeSession(agent_id=AGENT, user_id=ALICE)

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id=None,
            session_key=own_key,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_a_session_bound_to_another_users_thread_is_refused() -> None:
    registry = _FakeRegistry()
    own_key = _key()
    registry.sessions[own_key] = _FakeSession(agent_id=AGENT, user_id=BOB)
    registry.threads["thr-alice"] = _FakeThread(agent_id=AGENT, user_id=ALICE)
    registry.bindings[own_key] = "thr-alice"

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id=None,
            session_key=own_key,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_own_session_key_resolves_own_bound_thread() -> None:
    registry = _FakeRegistry()
    own_key = _key()
    registry.sessions[own_key] = _FakeSession(agent_id=AGENT, user_id=BOB)
    registry.threads["thr-bob"] = _FakeThread(agent_id=AGENT, user_id=BOB)
    registry.bindings[own_key] = "thr-bob"

    thread_id, session_key = await resolve_thread_id(
        agent_id=AGENT,
        user_id=BOB,
        thread_registry=registry,
        thread_id=None,
        session_key=own_key,
    )

    assert (thread_id, session_key) == ("thr-bob", own_key)


async def test_absent_session_key_still_uses_own_dashboard_key() -> None:
    registry = _FakeRegistry()

    thread_id, session_key = await resolve_thread_id(
        agent_id=AGENT,
        user_id=BOB,
        thread_registry=registry,
        thread_id=None,
        session_key=None,
    )

    assert (thread_id, session_key) == ("thr-created", _key())
    assert registry.created == [
        (  # type: ignore[comparison-overlap]
            _key(),
            AGENT,
            BOB,
            ThreadRegistry.CHANNEL_DASHBOARD,
        )
    ]


async def test_explicit_thread_id_of_another_user_is_refused() -> None:
    registry = _FakeRegistry()
    registry.threads["thr-alice"] = _FakeThread(agent_id=AGENT, user_id=ALICE)

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id="thr-alice",
            session_key=None,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert registry.rebound == []


async def test_own_explicit_thread_id_rebinds_own_key() -> None:
    registry = _FakeRegistry()
    own_key = _key()
    registry.threads["thr-bob"] = _FakeThread(agent_id=AGENT, user_id=BOB)

    thread_id, session_key = await resolve_thread_id(
        agent_id=AGENT,
        user_id=BOB,
        thread_registry=registry,
        thread_id="thr-bob",
        session_key=own_key,
    )

    assert (thread_id, session_key) == ("thr-bob", own_key)
    assert registry.rebound == [(own_key, "thr-bob", AGENT)]


async def test_foreign_client_key_cannot_pre_create_a_session() -> None:
    """The attacker must not be able to bind a victim's predictable key first."""
    registry = _FakeRegistry()
    alice_key = _key(user_id=ALICE)

    with pytest.raises(OctopError) as exc_info:
        await resolve_thread_id(
            agent_id=AGENT,
            user_id=BOB,
            thread_registry=registry,
            thread_id=None,
            session_key=alice_key,
        )

    assert exc_info.value.code == ErrorCode.FORBIDDEN
    assert registry.created == []
    assert registry.rebound == []
