"""Remote-control sockets must not retain permissions from their handshake."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.websockets import WebSocketState

from octop.api.common.capabilities import WebSocketCapabilityGuard
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RepoBundle]:
    monkeypatch.setattr("octop.infra.users.permissions.license_managed_install", lambda: False)
    db = SqlitePool(tmp_path / "control.db")
    run_migrations(db)
    try:
        yield RepoBundle.from_pool(db)
    finally:
        db.close()


class _Socket:
    def __init__(self, repos: RepoBundle) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                octop_server=SimpleNamespace(services=SimpleNamespace(repos=repos))
            )
        )
        self.application_state = WebSocketState.CONNECTED
        self.closed = asyncio.Event()
        self.close_code: int | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.close_code = code
        self.application_state = WebSocketState.DISCONNECTED
        self.closed.set()


def _guard(repos: RepoBundle, user_id: int) -> tuple[_Socket, WebSocketCapabilityGuard]:
    socket: Any = _Socket(repos)
    return socket, WebSocketCapabilityGuard(socket, user_id=user_id, permission="browser")


async def test_revocation_disconnects_only_the_affected_user(repos: RepoBundle) -> None:
    alice = repos.user_repo.create(username="alice", role="user", permissions=["browser"])
    bob = repos.user_repo.create(username="bob", role="user", permissions=["browser"])
    alice_socket, alice_guard = _guard(repos, alice)
    bob_socket, bob_guard = _guard(repos, bob)
    assert await alice_guard.check()
    assert await bob_guard.check()

    repos.user_repo.set_permissions(alice, [])

    assert not await alice_guard.check()
    assert alice_socket.close_code == 4003
    assert await bob_guard.check()
    assert bob_socket.application_state == WebSocketState.CONNECTED


async def test_disabled_administrator_loses_an_existing_socket(repos: RepoBundle) -> None:
    user_id = repos.user_repo.create(username="admin", role="admin")
    socket, guard = _guard(repos, user_id)
    assert await guard.check()

    repos.user_repo.set_disabled(user_id, True)

    assert not await guard.check()
    assert socket.close_code == 4003


async def test_idle_stream_is_disconnected_without_another_client_message(
    repos: RepoBundle,
) -> None:
    user_id = repos.user_repo.create(username="idle", role="user", permissions=["browser"])
    socket, guard = _guard(repos, user_id)
    assert await guard.check()
    guard.start()
    try:
        repos.user_repo.set_permissions(user_id, [])
        await asyncio.wait_for(socket.closed.wait(), timeout=5)
        assert socket.close_code == 4003
    finally:
        await guard.stop()
