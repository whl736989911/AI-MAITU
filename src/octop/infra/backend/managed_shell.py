"""Managed local-shell integration: never downgrade the jail to host execution.

Also makes the bubblewrap jail see the platform Python environment on native
(source) installs: precise read-only binds for ``sys.prefix`` and the real
interpreter runtime behind it (uv keeps venv interpreters under
``~/.local/share/uv``), plus ``PATH`` / ``PYTHONPATH`` so jail processes call
the platform interpreter instead of the system one. Home, project and
license directories are never mounted.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from deepagents.backends.protocol import ExecuteResponse
from harness_agent.backends import _maybe_wrap_workspace_artifacts
from harness_agent.backends.bwrap_shell import BubbledLocalShellBackend, build_bwrap_argv
from harness_agent.backends.local_shell import format_execute_result

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.env_file import validate_managed_environment
from octop.infra.utils.host_dirs import (
    assert_backend_root_dirs_allowed,
    license_managed_install,
    license_protected_dir_covered_by,
)

logger = logging.getLogger(__name__)

# Mirrors harness ``bwrap_shell._RO_BINDS``: paths the jail already mounts
# read-only, so anything beneath them needs no extra bind.
_SYSTEM_RO_BINDS: tuple[str, ...] = ("/usr", "/bin", "/lib", "/lib64")


@dataclass(frozen=True)
class _PythonRuntimeMounts:
    """Read-only jail binds plus env additions for the platform interpreter."""

    binds: tuple[tuple[str, str], ...]
    bin_dir: str | None
    site_packages: str | None


def _covered_by_system_ro_binds(path: Path) -> bool:
    for bound in _SYSTEM_RO_BINDS:
        bound_path = Path(bound)
        if not bound_path.exists():
            continue
        resolved = bound_path.resolve()
        if path == resolved or path.is_relative_to(resolved):
            return True
    return False


def _bind_candidate(path: Path) -> str | None:
    """Absolute directory worth binding read-only, else ``None``."""
    if not path.is_dir():
        return None
    if _covered_by_system_ro_binds(path):
        return None
    if path == Path(path.anchor) or str(path) in {"/home", "/root"} or path == Path.home():
        return None  # never widen a bind to a filesystem or home root
    return str(path)


@lru_cache(maxsize=1)
def _platform_python_runtime() -> _PythonRuntimeMounts | None:
    """Compute precise read-only mounts for the running platform interpreter.

    ``sys.prefix`` (the venv) is a symlink farm over the real interpreter on
    uv-managed hosts, so the base prefix — and, defensively, the resolved
    interpreter directory — are bound too when they live outside the jail's
    default system binds. ``None`` means nothing extra is needed (system-dir
    venvs, containers, non-Linux); env additions still apply when useful.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        prefix = Path(sys.prefix).resolve()
        base = Path(sys.base_prefix).resolve()
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return None
    binds: list[tuple[str, str]] = []
    bound: list[Path] = []
    for candidate in (prefix, base, exe_dir):
        if any(candidate == added or candidate.is_relative_to(added) for added in bound):
            continue
        text = _bind_candidate(candidate)
        if text is None:
            continue
        binds.append((text, text))
        bound.append(candidate)
    site_packages = (
        prefix
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    bin_dir = prefix / "bin"
    if not binds and not bin_dir.is_dir() and not site_packages.is_dir():
        return None
    return _PythonRuntimeMounts(
        binds=tuple(binds),
        bin_dir=str(bin_dir) if bin_dir.is_dir() else None,
        site_packages=str(site_packages) if site_packages.is_dir() else None,
    )


def _python_runtime_env(env: dict[str, str], runtime: _PythonRuntimeMounts) -> dict[str, str]:
    """Overlay ``PATH``/``PYTHONPATH`` so jail processes resolve the platform env."""
    if runtime.bin_dir:
        parts = (env.get("PATH") or os.defpath).split(os.pathsep)
        if parts[0] != runtime.bin_dir:
            env["PATH"] = os.pathsep.join(
                [runtime.bin_dir, *(part for part in parts if part != runtime.bin_dir)]
            )
    if runtime.site_packages:
        parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
        if runtime.site_packages not in parts:
            parts.append(runtime.site_packages)
            env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _mark_binds_read_only(argv: list[str], binds: tuple[tuple[str, str], ...]) -> None:
    """Flip harness's ``--bind`` entries for *binds* to ``--ro-bind`` in place."""
    read_only = set(binds)
    for index in range(len(argv) - 2):
        if argv[index] == "--bind" and (argv[index + 1], argv[index + 2]) in read_only:
            argv[index] = "--ro-bind"


class _PlatformPythonShellBackend(BubbledLocalShellBackend):
    """Bubblewrap execute support: see and call the platform Python environment."""

    def _skill_extra_binds(self) -> list[tuple[str, str]]:
        binds = super()._skill_extra_binds()
        runtime = _platform_python_runtime()
        if runtime is not None and runtime.binds:
            binds = [*binds, *runtime.binds]
        return binds

    def _apply_platform_python_env(self) -> None:
        runtime = _platform_python_runtime()
        if runtime is not None:
            self._env = _python_runtime_env(dict(self._env), runtime)

    def _refresh_execute_env(self) -> None:
        super()._refresh_execute_env()
        self._apply_platform_python_env()

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        runtime = _platform_python_runtime()
        if runtime is None or not command or not isinstance(command, str):
            return super().execute(command, timeout=timeout)
        self._refresh_execute_env()
        bwrap = self._bwrap_path
        if bwrap is None:
            return super().execute(command, timeout=timeout)
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)
        argv = build_bwrap_argv(
            bwrap=bwrap,
            root_dir=self.cwd,
            command=command,
            work_dir=self._virtual_workspace_cwd(),
            # Mirrors BubbledLocalShellBackend.execute (pinned harness); the
            # only delta is marking platform-runtime binds read-only.
            extra_binds=self._skill_extra_binds(),
        )
        _mark_binds_read_only(argv, runtime.binds)
        try:
            result = subprocess.run(
                argv,
                check=False,
                shell=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=effective_timeout,
                env=self._env,
            )
            return format_execute_result(result, max_output_bytes=self._max_output_bytes)
        except FileNotFoundError as exc:
            self._bwrap_path = None
            raise OctopError(
                ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                "Sandbox executable disappeared; host execution is forbidden",
            ) from exc


class ManagedShellBackend(_PlatformPythonShellBackend):
    """Preserve harness filesystem behavior; disallow its host-shell fallback."""

    def _refresh_execute_env(self) -> None:
        # Validate the environment produced by Harness (host + constructor +
        # workspace dotenv) before adding trusted platform-runtime paths.
        BubbledLocalShellBackend._refresh_execute_env(self)
        validate_managed_environment(self._env)
        self._apply_platform_python_env()

    def _execute_on_host(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Managed shell isolation is unavailable; host execution is forbidden",
        )


class SourceShellBackend(_PlatformPythonShellBackend):
    """Unmanaged native-install local shell: jail with the platform Python visible.

    Fail-closed like the managed shell — a missing or broken ``bwrap`` is an
    explicit error, never a silent host-shell downgrade.
    """

    def _execute_on_host(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Native sandbox is unavailable; host execution is forbidden",
        )


def _managed_local_shell(spec: Any, *, workspace_dir: Path, system_files_path: str) -> Any:
    if not sys.platform.startswith("linux"):
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED, "Managed local shells require Linux isolation"
        )
    bwrap = shutil.which("bwrap", path=os.defpath)
    if bwrap is None:
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED, "Trusted bubblewrap is unavailable"
        )
    kwargs = {key: value for key, value in spec.items() if key != "type"}
    root = kwargs.pop("root_dir", workspace_dir)
    effective_system = str(kwargs.pop("system_files_path", system_files_path))
    resolved_root = Path(os.path.expandvars(str(root))).expanduser().resolve()
    if (
        resolved_root == Path(resolved_root.anchor)
        or license_protected_dir_covered_by(str(resolved_root))
        or not kwargs.get("virtual_mode", True)
    ):
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED, "Managed shell root is not confined"
        )
    try:
        assert_backend_root_dirs_allowed(spec, restrict_to_home=False)
    except ValueError as exc:
        raise OctopError(ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED, str(exc)) from exc
    backend = ManagedShellBackend(
        root_dir=root,
        workspace_dir=workspace_dir,
        bwrap_path=bwrap,
        system_files_path=effective_system,
        **kwargs,
    )
    # Keep the pinned harness factory's workspace-artifact routing unchanged.
    return _maybe_wrap_workspace_artifacts(
        backend,
        root_dir=root,
        workspace_dir=workspace_dir,
        system_files_path=effective_system,
        scope_host_artifacts=True,
    )


def _source_local_shell(spec: Any, *, workspace_dir: Path, system_files_path: str) -> Any:
    """Jail native local shells; never pass through an unconfined host shell.

    Linux and bubblewrap are required. The manager normalizes legacy host
    roots to its managed workspace before reaching this boundary.
    """
    if not sys.platform.startswith("linux"):
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Native local shells require Linux isolation.",
        )
    if not spec.get("virtual_mode", True):
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Native local shell root is not confined.",
        )
    root = spec.get("root_dir")
    if root is None:
        root = workspace_dir
    resolved_root = Path(os.path.expandvars(str(root))).expanduser().resolve()
    if resolved_root == Path(resolved_root.anchor):
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Native local shell root is not confined.",
        )
    from harness_agent.backends.bwrap_shell import resolve_bubbled_bwrap  # noqa: PLC0415

    bwrap = resolve_bubbled_bwrap(virtual_mode=bool(spec.get("virtual_mode", True)), root_dir=root)
    if bwrap is None:
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "local_shell sandbox requires bubblewrap; run scripts/setup-host-sandbox.sh "
            "or POST /api/filesystem/ensure-bwrap",
        )
    kwargs = {key: value for key, value in spec.items() if key != "type"}
    root = kwargs.pop("root_dir", None)
    virtual_mode = kwargs.pop("virtual_mode", True)
    effective_system = str(kwargs.pop("system_files_path", system_files_path))
    kwargs.setdefault("inherit_env", True)
    backend = SourceShellBackend(
        root_dir=root,
        virtual_mode=virtual_mode,
        workspace_dir=workspace_dir,
        bwrap_path=bwrap,
        system_files_path=effective_system,
        **kwargs,
    )
    return _maybe_wrap_workspace_artifacts(
        backend,
        root_dir=root,
        workspace_dir=workspace_dir,
        system_files_path=effective_system,
        scope_host_artifacts=True,
    )


def managed_shell_spec(spec: Any, *, workspace_dir: Path, system_files_path: str) -> Any:
    """Convert only local-shell leaves; preserve all other backend choices."""
    if not isinstance(spec, dict):
        return spec
    kind = str(spec.get("type") or "").lower()
    if kind == "composite":
        result = dict(spec)
        result["default"] = managed_shell_spec(
            spec.get("default"), workspace_dir=workspace_dir, system_files_path=system_files_path
        )
        if isinstance(spec.get("routes"), dict):
            result["routes"] = {
                key: managed_shell_spec(
                    value, workspace_dir=workspace_dir, system_files_path=system_files_path
                )
                for key, value in spec["routes"].items()
            }
        return result
    if kind != "local_shell":
        return spec
    if license_managed_install():
        return _managed_local_shell(
            spec, workspace_dir=workspace_dir, system_files_path=system_files_path
        )
    return _source_local_shell(
        spec, workspace_dir=workspace_dir, system_files_path=system_files_path
    )
