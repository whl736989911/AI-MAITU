"""Regression tests for AG-01 — dashboard chat WS ``session_key`` ownership.

A dashboard turn must never accept a client-supplied ``session_key`` that
belongs to another user's session. Before the fix the key resolved to whatever
thread it was bound to, so a peer with access to the same agent could run a
turn inside the victim's thread (checkpointer context + history) or pre-bind a
victim's predictable key to their own thread.

The attack turns are refused by ``resolve_thread_id`` before the harness runs,
so the fake stream is never consumed for those cases.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.infra.gateway.threads import ThreadRegistry
from tests.support.app import octop_client
from tests.support.auth import (
    auth_header,
    bootstrap_admin,
    create_agent,
    ensure_users,
    resolve_user_id,
    seed_openai_provider,
)
from tests.support.fakes import FakeHarnessAgent
from tests.support.http import chat_ws_path, ws_connect


def _chat_ws(c: httpx.AsyncClient, aid: str, auth: dict[str, str]) -> Any:
    return ws_connect(c._octop_app, chat_ws_path(aid, auth))  # type: ignore[attr-defined]


@pytest.fixture
async def env(tmp_octop_home: Path) -> AsyncIterator[Any]:
    fake = FakeHarnessAgent(
        chunks=[{"type": "token", "node": "agent", "content": "ok"}],
    )
    async with octop_client(tmp_octop_home, fake_agent=fake) as (c, srv):
        await bootstrap_admin(c, tmp_octop_home)
        admin_auth = await auth_header(c)
        await seed_openai_provider(c, admin_auth)
        users = await ensure_users(c, admin_auth, "alice", "bob")
        aid = await create_agent(c, users["alice"])
        shared = await c.patch(
            f"/api/agents/{aid}",
            headers=users["alice"],
            json={"is_shared": True},
        )
        assert shared.status_code == 200, shared.text
        yield c, srv, fake, admin_auth, users["alice"], users["bob"], aid


async def _create_thread(c: httpx.AsyncClient, aid: str, auth: dict[str, str]) -> str:
    response = await c.post(f"/api/agents/{aid}/threads", headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["thread_id"]


async def _history(
    c: httpx.AsyncClient, aid: str, thread_id: str, auth: dict[str, str]
) -> list[dict[str, Any]]:
    response = await c.get(f"/api/agents/{aid}/threads/{thread_id}/history", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["messages"]


async def test_peer_session_key_cannot_enter_another_users_thread(env: Any) -> None:
    c, _srv, _fake, admin_auth, alice_auth, bob_auth, aid = env
    alice_id = await resolve_user_id(c, admin_auth, "alice")
    thread_id = await _create_thread(c, aid, alice_auth)
    before = await _history(c, aid, thread_id, alice_auth)

    async with _chat_ws(c, aid, bob_auth) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "injected-by-bob",
                "messages": [{"role": "user", "content": "injected-by-bob"}],
                "session_key": ThreadRegistry.dashboard_key(agent_id=aid, user_id=alice_id),
            }
        )
        frames = await ws.drain_turn()

    assert frames[0]["type"] == "error", frames
    assert "not owned" in frames[0]["message"]
    assert all(frame.get("type") != "token" for frame in frames)

    after = await _history(c, aid, thread_id, alice_auth)
    assert after == before
    assert "injected-by-bob" not in json.dumps(after, ensure_ascii=False)


async def test_own_session_key_still_starts_a_turn(env: Any) -> None:
    c, _srv, _fake, admin_auth, _alice_auth, bob_auth, aid = env
    bob_id = await resolve_user_id(c, admin_auth, "bob")

    async with _chat_ws(c, aid, bob_auth) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "hello",
                "messages": [{"role": "user", "content": "hello"}],
                "session_key": ThreadRegistry.dashboard_key(agent_id=aid, user_id=bob_id),
            }
        )
        frames = await ws.drain_turn()

    types = [frame.get("type") for frame in frames]
    assert "error" not in types, frames
    assert "token" in types, frames
    assert frames[-1]["type"] == "done"


async def test_foreign_session_key_cannot_rebind_an_owned_thread(env: Any) -> None:
    """Even with a valid own ``thread_id``, a peer's key must not be written."""
    c, srv, _fake, admin_auth, alice_auth, bob_auth, aid = env
    alice_id = await resolve_user_id(c, admin_auth, "alice")
    bob_id = await resolve_user_id(c, admin_auth, "bob")
    bob_thread = await _create_thread(c, aid, bob_auth)
    alice_key = ThreadRegistry.dashboard_key(agent_id=aid, user_id=alice_id)

    async with _chat_ws(c, aid, bob_auth) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "steal",
                "messages": [{"role": "user", "content": "steal"}],
                "thread_id": bob_thread,
                "session_key": alice_key,
            }
        )
        frames = await ws.drain_turn()

    assert frames[0]["type"] == "error", frames
    assert "not owned" in frames[0]["message"]

    registry = srv.app_runtime.gateway.thread_registry
    assert registry.get_session(alice_key) is None
    bob_session = registry.get_session(ThreadRegistry.dashboard_key(agent_id=aid, user_id=bob_id))
    assert bob_session is not None
    assert bob_session.user_id == bob_id
    assert bob_session.thread_id == bob_thread
