"""Disposable Docker execution for an agent's existing filesystem workspace."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import socket
import tempfile
import threading
import time
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any, cast

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 300
_OUTPUT_LIMIT = 64 * 1024
_DOCKER_TIMEOUT = 5
_IMAGE_NATIVE = "MAITU Smart Manufacturing:latest"


def isolated_execute_enabled() -> bool:
    """True only when the operator explicitly opts into Docker-isolated execution.

    Docker isolation spawns per-command containers through the Docker socket, so
    it is never a default: it requires an explicit trusted configuration (env
    set by the deployment overlay). Without it, agents run through the platform
    bwrap/managed-shell path instead.
    """
    return os.environ.get("MAITU_ISOLATED_EXECUTE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_FILESYSTEM_OPERATIONS = frozenset(
    {
        "ls",
        "read",
        "write",
        "edit",
        "delete",
        "grep",
        "agrep",
        "glob",
        "upload_files",
        "download_files",
    }
)


@cache
def _seccomp_profile() -> str:
    """Keep Moby's default syscall filtering with trusted pivot_root setup allowed."""
    return "seccomp=" + Path(__file__).with_name("_execute_seccomp.json").read_text(
        encoding="utf-8"
    )


_BWRAP_LAUNCHER = r"""
import json
import os
import subprocess
import stat
import sys

command = sys.argv[1]
policy = json.loads(sys.argv[2])
fds = []
def keep_fd(fd):
    fds.append(fd)
    return fd

def empty_fd():
    fd = os.memfd_create("octop-private-mask")
    return str(keep_fd(fd))
system_roots = (
    ("/usr", "/usr"), ("/usr/local", "/usr/local"), ("/bin", "/bin"),
    ("/lib", "/lib"), ("/lib64", "/lib64"),
    ("/usr/local/maitu-venv", "/usr/local/maitu-venv"),
)
system_fds = []
for source, destination in system_roots:
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    system_fds.append((fd, destination))
# Overlay mount points live in an ephemeral lower layer, not in user content.
workspace = os.path.realpath(os.path.join("/agents", policy["workspace_name"]))
workdir = os.path.join("/agents", policy["work_name"])
lower = "/sandbox/lower"
merged = "/sandbox/root"
os.mkdir(lower)
os.mkdir(merged)
for name in ("bin", "lib", "lib64", "usr", "proc", "dev", "tmp", "etc",
             ".maitu", ".octop", "sessions", "conversation_history", "large_tool_results"):
    os.mkdir(os.path.join(lower, name))
for name in ("USER.md", "MEMORY.md", "memory.sqlite", "memory.sqlite-wal",
             "memory.sqlite-shm", "checkpoints.sqlite", "checkpoints.sqlite-wal",
             "checkpoints.sqlite-shm"):
    with open(os.path.join(lower, name), "xb"):
        pass
subprocess.run([
    "/bin/mount", "-t", "overlay", "overlay", "-o",
    f"lowerdir={lower},upperdir={workspace},workdir={workdir},index=off,metacopy=off",
    merged,
], check=True)
argv = [
    "/usr/bin/bwrap", "--die-with-parent", "--unshare-pid",
    "--unshare-ipc", "--unshare-uts", "--new-session",
    "--cap-drop", "ALL", "--bind", merged, "/",
    "--dir", "/bin", "--dir", "/lib", "--dir", "/lib64",
    "--dir", "/usr", "--dir", "/usr/local",
    "--dir", "/proc", "--dir", "/dev", "--dir", "/tmp", "--dir", "/etc",
    "--proc", "/proc", "--dev", "/dev",
]
for fd, destination in system_fds:
    argv.extend(("--ro-bind", f"/proc/self/fd/{keep_fd(fd)}", destination))
argv.extend((
    "--size", "67108864", "--tmpfs", "/tmp",
    "--tmpfs", "/.maitu", "--tmpfs", "/.octop", "--tmpfs", "/sessions",
    "--tmpfs", "/conversation_history", "--tmpfs", "/large_tool_results",
))


for name in ("memory.sqlite", "memory.sqlite-wal", "memory.sqlite-shm",
             "checkpoints.sqlite", "checkpoints.sqlite-wal", "checkpoints.sqlite-shm"):
    argv.extend(("--ro-bind-data", empty_fd(), "/" + name))
for system_dir in ("/etc/fonts", "/etc/libreoffice"):
    if os.path.isdir(system_dir):
        fd = os.open(system_dir, os.O_RDONLY | os.O_DIRECTORY)
        argv.extend(("--ro-bind", f"/proc/self/fd/{keep_fd(fd)}", system_dir))

if policy["feature"] and not policy["shared_writable"]:
    found = set()
    for name in os.listdir(workspace):
        if name not in {"USER.md", "MEMORY.md"}:
            continue
        path = os.path.join(workspace, name)
        fd = None
        try:
            opened = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            if stat.S_ISREG(os.fstat(opened).st_mode):
                fd = opened
            else:
                os.close(opened)
        except OSError:
            pass
        if fd is None:
            argv.extend(("--ro-bind-data", empty_fd(), "/" + name))
        else:
            argv.extend(("--ro-bind", f"/proc/self/fd/{keep_fd(fd)}", "/" + name))
        found.add(name)
    for name in ("USER.md", "MEMORY.md"):
        if name not in found:
            argv.extend(("--ro-bind-data", empty_fd(), "/" + name))

private_roots = [os.path.realpath(os.path.join(workspace, name)) for name in (".maitu", ".octop")]
private_root = next(
    (root for root in private_roots if os.path.isdir(root)),
    private_roots[0],
)
private_paths = {
    os.path.join(private_root, name)
    for name in ("memory.sqlite", "memory.sqlite-wal", "memory.sqlite-shm",
                 "checkpoints.sqlite", "checkpoints.sqlite-wal", "checkpoints.sqlite-shm", "sessions")
}
private_paths.update(
    os.path.join(workspace, name)
    for name in ("sessions", "conversation_history", "large_tool_results")
)
for relative in policy["readonly_dirs"]:
    source = os.path.realpath(os.path.join(workspace, relative))
    path_parts = os.path.normpath(relative).split(os.sep)
    is_system_state = (
        any(source == root for root in private_roots)
        or any(source == hidden or source.startswith(hidden + os.sep) for hidden in private_paths)
        or (path_parts and path_parts[0] in {".maitu", ".octop"} and len(path_parts) > 1
            and path_parts[1] not in {"skills", "auth"})
    )
    if (
        is_system_state
        or source == workspace
        or not source.startswith(workspace + os.sep)
        or not os.path.isdir(source)
    ):
        continue
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        continue
    argv.extend(("--ro-bind", f"/proc/self/fd/{keep_fd(fd)}", "/" + relative))

argv.extend((
    "--chdir", "/", "--clearenv",
    "--setenv", "PATH", "/usr/local/maitu-venv/bin:/usr/local/bin:/usr/bin:/bin",
    "--setenv", "HOME", "/", "--setenv", "TMPDIR", "/tmp",
    "--setenv", "LANG", "C.UTF-8",
    "--", "/usr/local/maitu-venv/bin/python3", "-I", "-S", "-c",
    "import os,sys; os.closerange(3, 1048576); os.execv('/bin/sh', ['/bin/sh', '-c', sys.argv[1]])",
    command,
))
result = subprocess.run(
    argv,
    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/", "TMPDIR": "/tmp", "LANG": "C.UTF-8"},
    pass_fds=tuple(fds),
    check=False,
)
sys.exit(result.returncode)
"""


def _shared_workspace_writable() -> bool:
    try:
        from octop.infra.agents.middleware.feature_memory import turn_feature_memory_context

        context = turn_feature_memory_context()
    except Exception:
        return False
    return bool(context and context.shared_writable)


class IsolatedFilesystemBackend(FilesystemBackend, SandboxBackendProtocol):
    """Keep FilesystemBackend file operations; isolate commands in disposable Docker."""

    def __init__(
        self,
        *,
        filesystem: FilesystemBackend,
        managed_root: str | Path,
        locale: str,
        feature_agent: bool,
        readonly_dirs: Sequence[str | Path],
    ) -> None:
        self._filesystem = filesystem
        super().__init__(
            root_dir=filesystem.cwd,
            virtual_mode=filesystem.virtual_mode,
            max_file_size_mb=max(1, filesystem.max_file_size_bytes // (1024 * 1024)),
        )
        self.max_file_size_bytes = filesystem.max_file_size_bytes
        self._managed_root = Path(managed_root).resolve()
        self._locale = locale
        self._feature_agent = feature_agent
        workspace = self.cwd.resolve()
        self._readonly_dirs: list[str] = []
        for candidate in readonly_dirs:
            resolved = Path(candidate).resolve()
            if resolved.is_relative_to(workspace):
                relative = resolved.relative_to(workspace).as_posix()
                if relative and relative not in self._readonly_dirs:
                    self._readonly_dirs.append(relative)
        self._execute_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active: dict[threading.Event, Any] = {}

    def __getattribute__(self, name: str) -> Any:
        if name in _FILESYSTEM_OPERATIONS:
            filesystem = object.__getattribute__(self, "_filesystem")
            return getattr(filesystem, name)
        return super().__getattribute__(name)

    @property
    def id(self) -> str:
        return f"octop-isolated:{self.cwd}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._execute(
            command,
            timeout,
            None,
            self._feature_agent and _shared_workspace_writable(),
        )

    def _execute(
        self,
        command: str,
        timeout: int | None,
        cancelled: threading.Event | None,
        shared_writable: bool,
    ) -> ExecuteResponse:
        from octop.i18n import tr

        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(output=tr("execute.invalid_command", self._locale), exit_code=2)
        effective_timeout = (
            _DEFAULT_TIMEOUT if timeout is None else max(1, min(int(timeout), _MAX_TIMEOUT))
        )
        try:
            return self._run_container(command, effective_timeout, cancelled, shared_writable)
        except Exception as exc:
            logger.warning("Isolated execute unavailable (%s)", type(exc).__name__)
            return ExecuteResponse(output=tr("execute.unavailable", self._locale), exit_code=125)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        cancelled = threading.Event()
        shared_writable = context.run(_shared_workspace_writable) if self._feature_agent else False
        future = loop.run_in_executor(
            None,
            context.run,
            self._execute,
            command,
            timeout,
            cancelled,
            shared_writable,
        )
        try:
            return await future
        except asyncio.CancelledError:
            cancelled.set()
            with self._active_lock:
                container = self._active.get(cancelled)
            if container is not None:
                with contextlib.suppress(Exception):
                    await loop.run_in_executor(None, container.kill)
            raise

    def _run_container(
        self,
        command: str,
        timeout: int,
        cancelled: threading.Event | None,
        shared_writable: bool,
    ) -> ExecuteResponse:
        with self._execute_lock:
            if cancelled is not None and cancelled.is_set():
                return ExecuteResponse(output="", exit_code=130)
            return self._run_isolated(command, timeout, cancelled, shared_writable)

    def _run_isolated(
        self,
        command: str,
        timeout: int,
        cancelled: threading.Event | None,
        shared_writable: bool,
    ) -> ExecuteResponse:
        import docker
        from requests.exceptions import ConnectionError as RequestsConnectionError
        from requests.exceptions import ReadTimeout
        from urllib3.exceptions import ReadTimeoutError

        docker_sdk = cast(Any, docker)
        client = docker_sdk.from_env(timeout=_DOCKER_TIMEOUT)
        container = None
        work_dir: tempfile.TemporaryDirectory[str] | None = None
        started = time.monotonic()
        try:
            image, host_workspace = _resolve_image_and_workspace(
                client, self.cwd, self._managed_root
            )
            work_dir = tempfile.TemporaryDirectory(prefix=".execute-", dir=self.cwd.parent)
            runner_argv = [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/",
                "TMPDIR=/tmp",
                "LANG=C.UTF-8",
                "/usr/local/maitu-venv/bin/python3",
                "-I",
                "-S",
                "-c",
                _BWRAP_LAUNCHER,
                command,
                json.dumps(
                    {
                        "feature": self._feature_agent,
                        "shared_writable": shared_writable,
                        "readonly_dirs": self._readonly_dirs,
                        "workspace_name": host_workspace.name,
                        "work_name": Path(work_dir.name).name,
                    }
                ),
            ]
            container = client.containers.run(
                image,
                entrypoint=[],
                command=runner_argv,
                detach=True,
                network_mode="none",
                read_only=True,
                volumes={str(host_workspace.parent): {"bind": "/agents", "mode": "rw"}},
                tmpfs={"/sandbox": "rw,nosuid,nodev,size=64m"},
                working_dir="/",
                environment={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/",
                    "TMPDIR": "/tmp",
                    "LANG": "C.UTF-8",
                },
                use_config_proxy=False,
                user="0:0",
                cap_drop=["ALL"],
                cap_add=["SYS_ADMIN", "CHOWN", "FOWNER", "DAC_OVERRIDE"],
                security_opt=[
                    "no-new-privileges:true",
                    "apparmor:unconfined",
                    _seccomp_profile(),
                ],
                mem_limit="512m",
                nano_cpus=1_000_000_000,
                pids_limit=128,
                log_config=docker_sdk.types.LogConfig(
                    type="json-file",
                    config={"max-size": "1m", "max-file": "1"},
                ),
                labels={"octop.isolated_execute": "true"},
            )
            if cancelled is not None:
                with self._active_lock:
                    self._active[cancelled] = container
                if cancelled.is_set():
                    container.kill()
            output = bytearray()
            truncated = False
            deadline = started + timeout
            timed_out = False
            try:
                result = container.wait(timeout=max(0.1, deadline - time.monotonic()))
                status = int(result.get("StatusCode", 125))
            except (TimeoutError, ReadTimeout, RequestsConnectionError) as exc:
                if isinstance(exc, RequestsConnectionError) and not (
                    exc.args and isinstance(exc.args[0], ReadTimeoutError)
                ):
                    raise
                try:
                    container.kill()
                except Exception as exc:
                    logger.warning(
                        "Could not kill timed-out isolated execute container (%s)",
                        type(exc).__name__,
                    )
                status = 124
                timed_out = True
            if cancelled is not None and cancelled.is_set():
                return ExecuteResponse(output="", exit_code=130)
            for chunk in container.logs(stream=True, follow=False, stdout=True, stderr=True):
                raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", "replace")
                remaining = _OUTPUT_LIMIT - len(output)
                if remaining > 0:
                    output.extend(raw[:remaining])
                if len(raw) > remaining:
                    truncated = True
            if timed_out:
                from octop.i18n import tr

                suffix = ("\n" + tr("execute.timeout", self._locale)).encode("utf-8")
                output.extend(suffix[: _OUTPUT_LIMIT - len(output)])
            return ExecuteResponse(
                output=_decode_output(output), exit_code=status, truncated=truncated
            )
        finally:
            if container is not None:
                if cancelled is not None:
                    with self._active_lock:
                        self._active.pop(cancelled, None)
                try:
                    container.remove(force=True, v=True)
                except Exception:
                    logger.warning("Could not remove isolated execute container")
            client.close()
            if work_dir is not None:
                work_dir.cleanup()


class IsolatedCompositeBackend(CompositeBackend, SandboxBackendProtocol):
    """Composite filesystem backend with execute routed only to its isolated default."""

    @property
    def id(self) -> str:
        return getattr(self.default, "id", "octop-isolated-composite")

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        from octop.i18n import tr

        if not isinstance(self.default, SandboxBackendProtocol):
            return ExecuteResponse(
                output=tr("execute.unavailable", getattr(self.default, "_locale", "en")),
                exit_code=125,
            )
        return self.default.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        from octop.i18n import tr

        if not isinstance(self.default, SandboxBackendProtocol):
            return ExecuteResponse(
                output=tr("execute.unavailable", getattr(self.default, "_locale", "en")),
                exit_code=125,
            )
        return await self.default.aexecute(command, timeout=timeout)


def _resolve_image_and_workspace(
    client: Any, workspace: Path, managed_root: Path
) -> tuple[str, Path]:
    workspace = workspace.resolve(strict=True)
    managed_root = managed_root.resolve(strict=True)
    if not workspace.is_relative_to(managed_root) or not workspace.is_dir():
        raise RuntimeError("agent workspace is outside the managed agent workspace root")
    hostname = socket.gethostname()
    try:
        parent = client.api.inspect_container(hostname)
    except Exception:
        if Path("/.dockerenv").exists():
            raise RuntimeError(
                "platform container cannot inspect its trusted runtime image or workspace mount"
            ) from None
        if not workspace.is_dir():
            raise RuntimeError("managed agent workspace is not accessible to Docker") from None
        return client.images.get(_IMAGE_NATIVE).id, workspace
    mounts = parent.get("Mounts", [])
    mappings: list[tuple[int, Path, Path, Path]] = []
    for mount in mounts:
        target_raw = str(mount.get("Destination") or "")
        source_raw = str(mount.get("Source") or "")
        target = Path(target_raw).resolve()
        if target.is_relative_to(workspace):
            raise RuntimeError("managed agent workspace has an unsafe nested volume mount")
        source = Path(source_raw).resolve()
        if target == Path("/") or source == Path("/"):
            continue
        try:
            relative = workspace.relative_to(target)
            managed_relative = managed_root.relative_to(target)
        except ValueError:
            continue
        mappings.append((len(target.parts), source, relative, managed_relative))
    if len(mappings) != 1:
        raise RuntimeError("agent workspace must map to exactly one safe platform data mount")
    _, source, relative, managed_relative = mappings[0]
    host_workspace = (source / relative).resolve()
    host_managed_root = (source / managed_relative).resolve()
    if not host_workspace.is_relative_to(host_managed_root):
        raise RuntimeError("mapped agent workspace is not within the managed workspace volume")
    return str(parent.get("Image") or ""), host_workspace


def _decode_output(output: bytearray) -> str:
    return bytes(output).decode("utf-8", "replace")
