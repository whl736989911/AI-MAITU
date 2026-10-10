"""Terminal context plus a permanently restricted host PTY WebSocket route.

The WebSocket handshake is denied before authentication or shell setup. The
session helpers below remain isolated low-level utilities and are not exposed
through the customer deployment API.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket

from octop.api.common.agent import assert_agent_owner
from octop.api.common.agent_workspace import resolve_agent_workspace_dir
from octop.api.deps import get_server, require_permission
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils import posix_compat

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum number of concurrent live shells (PTY processes), process-wide.
# Re-attaching to an existing persistent session does not count against this.
_MAX_SESSIONS = 10

# Per-session scrollback replay buffer (raw bytes). Replayed to the client on
# re-attach so terminal history survives a refresh / reconnect.
_MAX_SCROLLBACK_BYTES = 512 * 1024

# How long a detached persistent shell is kept alive waiting for a reconnect
# before it is reaped. Long enough to survive a refresh or a brief network drop.
_DETACH_GRACE_SECONDS = 300.0

_TERMINAL_UNSUPPORTED_REASON = (
    "Interactive web terminal is not supported on Windows. "
    "Use Linux or macOS, or run commands via the agent chat interface."
)


def terminal_supported() -> tuple[bool, str]:
    """Return ``(supported, reason_if_unsupported)`` for the PTY WebSocket terminal."""
    if os.name != "posix":
        return False, _TERMINAL_UNSUPPORTED_REASON
    try:
        import fcntl  # noqa: F401
        import pty  # noqa: F401
        import termios  # noqa: F401
    except ImportError:
        return False, "PTY terminal dependencies are not available on this platform."
    return True, ""


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """ioctl TIOCSWINSZ on the master fd. Errors are non-fatal."""
    try:
        posix_compat.set_winsize(fd, cols, rows)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("failed to set winsize: %s", exc)


def _detect_shell() -> str:
    """Resolve the shell for the current platform."""
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "").strip()
        if comspec and os.path.exists(comspec):
            return comspec
        powershell = (
            os.environ.get("SYSTEMROOT", r"C:\Windows")
            + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        if os.path.exists(powershell):
            return powershell
        return "cmd.exe"

    def _usable(path: str) -> bool:
        name = os.path.basename(path).lower()
        if name in {"nologin", "false", "true", "sync"}:
            return False
        return bool(path) and os.path.exists(path)

    shell = os.environ.get("SHELL", "").strip()
    if _usable(shell):
        return shell
    try:
        entry = posix_compat.getpwuid(posix_compat.getuid())
        shell = str(entry.pw_shell)
        if _usable(shell):
            return shell
    except Exception:  # pragma: no cover - non-posix
        pass
    for candidate in ("/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return "/bin/bash"


_ZSH_WEB_TERMINAL_RC = """\
# MAITU Smart Manufacturing web terminal — reduce stray blank / spacer lines in browser PTYs.
export PROMPT_EOL_MARK=
if [[ -f "${HOME}/.zshrc" ]]; then
  source "${HOME}/.zshrc"
fi
export PROMPT_EOL_MARK=
unsetopt prompt_sp 2>/dev/null
unsetopt prompt_cr 2>/dev/null
octop_web_terminal_precmd() {
  export PROMPT_EOL_MARK=
  unsetopt prompt_sp 2>/dev/null
}
typeset -ga precmd_functions
precmd_functions=("${precmd_functions[@]}" octop_web_terminal_precmd)
"""


def _zsh_web_zdotdir() -> str:
    """Return a temp ZDOTDIR whose .zshrc sources the user config then re-applies fixes."""
    d = tempfile.mkdtemp(prefix="octop-zdotdir-")
    with open(os.path.join(d, ".zshrc"), "w", encoding="utf-8") as fh:
        fh.write(_ZSH_WEB_TERMINAL_RC)
    return d


def _shell_env(*, shell: str) -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env.setdefault("LANG", "en_US.UTF-8")
    # Suppress zsh's PROMPT_EOL_MARK (the trailing "%" shown when output
    # does not end with a newline) — it appears as a stray "%" line in the
    # web terminal and is confusing to users.
    env["PROMPT_EOL_MARK"] = ""
    if "zsh" in shell:
        env["OCTOP_WEB_TERMINAL"] = "1"
    return env


class _PtySession:
    """A pseudo-terminal session that can outlive a single WS connection.

    The PTY + shell is owned by the session; attached WebSocket connections
    are mere subscribers fed via per-connection queues. Output keeps being
    captured into ``scrollback`` even while no client is attached, so a
    reconnect can replay the history.
    """

    def __init__(
        self,
        sid: str,
        agent_id: str,
        user_id: int,
        proc: subprocess.Popen[bytes],
        master_fd: int,
        cols: int,
        rows: int,
        persistent: bool,
        zdotdir: str | None = None,
    ) -> None:
        self.sid = sid
        self.agent_id = agent_id
        self.user_id = user_id
        self.persistent = persistent
        self.proc = proc
        self.master_fd = master_fd
        self.cols = cols
        self.rows = rows
        self.zdotdir = zdotdir
        # Bounded scrollback ring buffer (raw bytes), replayed on attach.
        self.scrollback: bytearray = bytearray()
        # Output fan-out queues, one per currently attached connection.
        self.subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self.pump_task: asyncio.Task[None] | None = None
        # Grace-period cleanup handle, armed when the last subscriber detaches.
        self.detach_handle: asyncio.TimerHandle | None = None
        # Reference to the deferred reap task (keeps it from being GC'd).
        self.reap_task: asyncio.Task[None] | None = None
        self.closed = False

    def append_scrollback(self, data: bytes) -> None:
        """Append output to the bounded scrollback buffer."""
        self.scrollback.extend(data)
        excess = len(self.scrollback) - _MAX_SCROLLBACK_BYTES
        if excess > 0:
            del self.scrollback[:excess]


# Process-wide registry of live terminal sessions, keyed by (agent_id, sid).
# The agent_id is part of the key so sessions can never leak across agents.
_sessions: dict[tuple[str, str], _PtySession] = {}
_sessions_lock = asyncio.Lock()


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM (then SIGKILL) the shell's process group. Blocking — run in executor."""
    try:
        if proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            posix_compat.killpg(posix_compat.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                posix_compat.killpg(
                    posix_compat.getpgid(proc.pid),
                    posix_compat.sigkill(),
                )
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=1)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("terminal process cleanup error: %s", exc)


def _spawn_pty_session(
    sid: str,
    agent_id: str,
    user_id: int,
    workspace_dir: str,
    cols: int,
    rows: int,
    persistent: bool,
) -> _PtySession:
    """Create a PTY pair, spawn the shell at ``workspace_dir`` (pump not started).

    Caller must subscribe then call :meth:`_PtySession.start_pump` so an
    immediately-exiting shell cannot race the empty subscriber set under
    ``_sessions_lock``.
    """
    master_fd, slave_fd = posix_compat.openpty()
    _set_winsize(master_fd, cols, rows)
    posix_compat.set_nonblock(master_fd)

    shell = _detect_shell()
    # Interactive flag so rc files (.bashrc / .zshrc) load.
    # Avoid -l (login shell) for zsh: it triggers logout output ("%" line) on exit.
    cmd = [shell, "-i"] if ("bash" in shell or "zsh" in shell) else [shell]
    env = _shell_env(shell=shell)
    zdotdir: str | None = None
    if "zsh" in shell:
        zdotdir = _zsh_web_zdotdir()
        env["ZDOTDIR"] = zdotdir

    cwd = workspace_dir
    if not os.path.isdir(cwd):
        # Fall back rather than fail the whole WebSocket — mkdir races or a
        # deleted workspace should still yield a usable shell.
        cwd = os.path.expanduser("~") or "/"
        logger.warning(
            "terminal cwd missing, falling back: agent=%s wanted=%s using=%s",
            agent_id,
            workspace_dir,
            cwd,
        )

    proc = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=posix_compat.setsid,  # separate process group for clean teardown
        env=env,
        cwd=cwd,
    )
    os.close(slave_fd)

    session = _PtySession(
        sid, agent_id, user_id, proc, master_fd, cols, rows, persistent, zdotdir=zdotdir
    )
    logger.info(
        "terminal opened: agent=%s sid=%s pid=%s shell=%s cwd=%s persistent=%s",
        agent_id,
        sid,
        proc.pid,
        shell,
        cwd,
        persistent,
    )
    return session


def _start_session_pump(session: _PtySession) -> None:
    """Begin PTY fan-out after the first subscriber is attached."""
    if session.pump_task is None and not session.closed:
        session.pump_task = asyncio.create_task(_pump_pty(session))


async def _pump_pty(session: _PtySession) -> None:
    """Read PTY output into scrollback and fan out to subscribers.

    Bound to the *session*, not to any single WebSocket, so output keeps
    being captured even while no client is attached.
    """
    while True:
        data = _read_nonblock(session.master_fd)
        if data is None:
            # Would-block. If the shell has exited, stop draining.
            if session.proc.poll() is not None:
                break
            await asyncio.sleep(0.02)
            continue
        if data == b"":
            break  # EOF / read error → shell gone
        session.append_scrollback(data)
        item: dict[str, object] = {"type": "output", "data": data.decode("utf-8", errors="replace")}
        for q in list(session.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(item)

    # Shell exited on its own — notify attached clients, then reap the session.
    exit_code = session.proc.poll()
    exit_item: dict[str, object] = {
        "type": "exit",
        "code": exit_code if exit_code is not None else 0,
    }
    for q in list(session.subscribers):
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(exit_item)
    await _destroy_session(session, cancel_pump=False)


async def _destroy_session(session: _PtySession, *, cancel_pump: bool) -> None:
    """Tear down a session: stop the pump, close the PTY, kill the shell.

    Idempotent — the ``closed`` flag makes consecutive calls safe. The
    check-and-set has no ``await`` in between, so within one event loop
    only the first caller proceeds.
    """
    if session.closed:
        return
    session.closed = True

    key = (session.agent_id, session.sid)
    async with _sessions_lock:
        # NOCA:IdenticalIsComparison(intentional object-identity check before registry delete)
        if _sessions.get(key) is session:
            del _sessions[key]

    if session.detach_handle is not None:
        session.detach_handle.cancel()
        session.detach_handle = None

    if cancel_pump and session.pump_task is not None and not session.pump_task.done():
        session.pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.pump_task

    with contextlib.suppress(OSError):
        os.close(session.master_fd)

    if session.zdotdir:
        shutil.rmtree(session.zdotdir, ignore_errors=True)

    # Kill the whole process group off-thread so we never block the loop.
    proc = session.proc
    if proc.poll() is None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _terminate_process_group, proc)

    logger.info(
        "terminal session ended: agent=%s sid=%s active=%d",
        session.agent_id,
        session.sid,
        len(_sessions),
    )


def _arm_detach_cleanup(session: _PtySession) -> None:
    """Schedule a persistent session for cleanup once the grace period elapses."""
    if session.closed or session.subscribers:
        return
    loop = asyncio.get_running_loop()
    if session.detach_handle is not None:
        session.detach_handle.cancel()

    def _reap() -> None:
        if not session.subscribers and not session.closed:
            # Keep a reference so the deferred task is not garbage-collected.
            session.reap_task = asyncio.ensure_future(_destroy_session(session, cancel_pump=True))

    session.detach_handle = loop.call_later(_DETACH_GRACE_SECONDS, _reap)
    logger.info(
        "terminal detached: agent=%s sid=%s grace=%.0fs",
        session.agent_id,
        session.sid,
        _DETACH_GRACE_SECONDS,
    )


@router.get("/agents/{agent_id}/terminal/context")
async def terminal_context(
    agent_id: str,
    user: Any = Depends(require_permission("terminal")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return system context for the AI terminal panel."""
    assert server.app_runtime is not None
    row = server.app_runtime.agent_registry.get_row(agent_id)
    if row is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, "no agents for user")
    assert_agent_owner(row, user, server=server)
    # AI-facing path: persisted config value (e.g. ``/.octop/workspaces/<id>``).
    # PTY spawn still uses the host-mapped path via resolve_agent_workspace_dir.
    cfg = server.app_runtime.agent_registry.get_config(agent_id)
    raw_ws = cfg.get("workspace_dir") if isinstance(cfg, dict) else None
    if isinstance(raw_ws, str) and raw_ws.strip():
        workspace_dir = raw_ws.strip()
    else:
        workspace_dir = str(resolve_agent_workspace_dir(server, agent_id))

    os_name = platform.system()
    os_release = platform.release()
    machine = platform.machine()
    os_str = f"{os_name} {os_release} {machine}"

    distro = ""
    try:
        if os_name == "Linux":
            import pathlib as _pathlib

            p = _pathlib.Path("/etc/os-release")
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.startswith("PRETTY_NAME="):
                        distro = line.split("=", 1)[1].strip().strip('"')
                        break
    except Exception:
        pass

    supported, unsupported_reason = terminal_supported()
    return {
        "os": os_str,
        "distro": distro,
        "shell": _detect_shell(),
        "hostname": socket.gethostname(),
        "username": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "workspace_dir": workspace_dir,
        "agent_id": agent_id,
        "agent_name": row.name,
        "terminal_supported": supported,
        "terminal_unsupported_reason": unsupported_reason,
    }


@router.websocket("/agents/{agent_id}/terminal/ws")
async def terminal_ws(
    websocket: WebSocket,
    agent_id: str,
    token: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    cols: int = Query(default=80),
    rows: int = Query(default=24),
) -> None:
    """Reject host PTY access for every customer deployment."""
    await websocket.close(
        code=4403,
        reason=json.dumps({"error": {"code": "INSTANCE_DEPLOYMENT_RESTRICTED"}}),
    )


def _read_nonblock(fd: int | None) -> bytes | None:
    """Return ``bytes`` read, ``None`` if would-block, ``b""`` on EOF."""
    if fd is None:
        return b""
    try:
        return os.read(fd, 4096)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return None
        return b""
