"""Unit tests for terminal helpers and the deployment lockdown boundary."""

from __future__ import annotations

import asyncio
import contextlib
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.websockets import WebSocketState

from octop.api.routers import terminal

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX terminal required")


def _make_session(
    sid: str,
    agent_id: str,
    user_id: int,
    persistent: bool,
    *,
    scrollback: bytes = b"",
    cols: int = 80,
    rows: int = 24,
    master_fd: int | None = None,
) -> terminal._PtySession:
    fd = master_fd if master_fd is not None else os.open(os.devnull, os.O_RDWR)
    proc = Mock()
    proc.poll.return_value = 0
    proc.pid = 99999
    proc.returncode = 0
    session = terminal._PtySession(sid, agent_id, user_id, proc, fd, cols, rows, persistent)
    session.scrollback = bytearray(scrollback)
    return session


@pytest.fixture(autouse=True)
def _reset_terminal_state():
    terminal._sessions.clear()
    terminal._sessions_lock = asyncio.Lock()
    yield
    for session in list(terminal._sessions.values()):
        if isinstance(session, terminal._PtySession):
            if session.detach_handle is not None:
                session.detach_handle.cancel()
            if not session.closed:
                session.closed = True
                with contextlib.suppress(OSError):
                    os.close(session.master_fd)
    terminal._sessions.clear()


def test_scrollback_trims_to_max() -> None:
    session = _make_session("s1", "agent", 1, persistent=True)
    session.append_scrollback(b"x" * (terminal._MAX_SCROLLBACK_BYTES + 100))
    assert len(session.scrollback) == terminal._MAX_SCROLLBACK_BYTES
    assert bytes(session.scrollback).endswith(b"x" * 100)


def test_scrollback_preserves_order_under_limit() -> None:
    session = _make_session("s1", "agent", 1, persistent=True)
    session.append_scrollback(b"abc")
    session.append_scrollback(b"def")
    assert bytes(session.scrollback) == b"abcdef"


def test_session_defaults() -> None:
    session = _make_session("s1", "agent", 1, persistent=False)
    assert session.closed is False
    assert session.subscribers == set()
    assert session.pump_task is None
    assert session.detach_handle is None
    assert session.persistent is False


def test_sessions_keyed_by_agent_and_sid() -> None:
    first = _make_session("sid", "agent-a", 1, persistent=True)
    second = _make_session("sid", "agent-b", 1, persistent=True)
    terminal._sessions[("agent-a", "sid")] = first
    terminal._sessions[("agent-b", "sid")] = second
    assert terminal._sessions[("agent-a", "sid")] is first
    assert terminal._sessions[("agent-b", "sid")] is second
    assert len(terminal._sessions) == 2


async def test_destroy_session_is_idempotent() -> None:
    session = _make_session("s1", "agent", 1, persistent=False)
    terminal._sessions[("agent", "s1")] = session
    await terminal._destroy_session(session, cancel_pump=True)
    assert session.closed is True
    assert ("agent", "s1") not in terminal._sessions
    await terminal._destroy_session(session, cancel_pump=True)
    assert ("agent", "s1") not in terminal._sessions


async def test_arm_detach_cleanup_skips_when_subscribers_present() -> None:
    session = _make_session("s1", "agent", 1, persistent=True)
    terminal._sessions[("agent", "s1")] = session
    session.subscribers.add(asyncio.Queue())
    terminal._arm_detach_cleanup(session)
    assert session.detach_handle is None


async def test_arm_detach_cleanup_arms_when_empty() -> None:
    session = _make_session("s1", "agent", 1, persistent=True)
    terminal._sessions[("agent", "s1")] = session
    terminal._arm_detach_cleanup(session)
    assert session.detach_handle is not None
    session.detach_handle.cancel()


@posix_only
async def test_pump_pty_drains_fans_out_and_reaps() -> None:
    read_fd, write_fd = os.pipe()
    proc = Mock()
    proc.poll.return_value = 0
    session = terminal._PtySession("s1", "agent", 1, proc, read_fd, 80, 24, persistent=True)
    terminal._sessions[("agent", "s1")] = session
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    session.subscribers.add(queue)
    os.write(write_fd, b"hello\n")
    os.close(write_fd)

    await terminal._pump_pty(session)

    assert bytes(session.scrollback) == b"hello\n"
    assert await queue.get() == {"type": "output", "data": "hello\n"}
    assert await queue.get() == {"type": "exit", "code": 0}
    assert session.closed is True
    assert ("agent", "s1") not in terminal._sessions


class _FakeWS:
    def __init__(self) -> None:
        self.close_code: int | None = None
        self.reason: str | None = None
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.app = SimpleNamespace(state=SimpleNamespace(octop_server=object()))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_code = code
        self.reason = reason
        self.application_state = WebSocketState.DISCONNECTED

    async def accept(self) -> None:
        self.accepted = True
        self.application_state = WebSocketState.CONNECTED


@pytest.mark.parametrize("token", [None, "admin-token", "member-token"])
async def test_terminal_websocket_fails_closed_before_shell_spawn(
    monkeypatch, token: str | None
) -> None:
    websocket = _FakeWS()
    spawn = Mock(side_effect=AssertionError("PTY spawn must never run"))
    monkeypatch.setattr(terminal, "_spawn_pty_session", spawn)
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "test-trusted-keys.json")
    monkeypatch.setattr(terminal, "resolve_user_from_token", lambda *_args: Mock())

    await terminal.terminal_ws(websocket, agent_id="agent", token=token)

    assert websocket.close_code == (4001 if token is None else 4403)
    assert websocket.accepted is False
    spawn.assert_not_called()


def test_detect_shell_cmd_no_login_flag(monkeypatch) -> None:
    import shutil

    if os.name != "posix":
        pytest.skip("POSIX shells only")
    for shell_name in ("bash", "zsh"):
        shell_path = shutil.which(shell_name)
        if shell_path is None:
            continue
        monkeypatch.setenv("SHELL", shell_path)
        shell = terminal._detect_shell()
        cmd = [shell, "-i"] if ("bash" in shell or "zsh" in shell) else [shell]
        assert "-l" not in cmd
        assert "-i" in cmd
