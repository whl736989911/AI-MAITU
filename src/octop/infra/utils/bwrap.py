"""Provision bubblewrap and verify its namespace jail can actually start."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INSTALL_TIMEOUT_SEC = 90

_PKG_MANAGERS: tuple[tuple[str, str], ...] = (
    ("apt-get", "apt"),
    ("apt", "apt"),
    ("dnf", "dnf"),
    ("yum", "yum"),
    ("pacman", "pacman"),
    ("zypper", "zypper"),
)

# Shared diagnosis taxonomy for the filesystem sandbox API:
# ``missing_binary``           bwrap is not installed
# ``install_permission_denied`` package installation is not possible without credentials
# ``userns_denied``            the kernel / AppArmor / seccomp refuses the user namespace
# ``runtime_failure``          any other failure starting the jail
DIAGNOSIS_MISSING_BINARY = "missing_binary"
DIAGNOSIS_INSTALL_PERMISSION_DENIED = "install_permission_denied"
DIAGNOSIS_USERNS_DENIED = "userns_denied"
DIAGNOSIS_RUNTIME_FAILURE = "runtime_failure"

# Real bubblewrap failure strings (observed on Ubuntu 24.04): AppArmor's
# unprivileged-userns restriction denies the uid/gid map setup, while kernels
# with unprivileged userns disabled (or seccomp filters blocking ``clone``)
# reject namespace creation outright. Anything else is an unrelated failure.
_USERNS_DENY_MARKERS: tuple[str, ...] = (
    "uid map",
    "gid map",
    "user namespace",
    "new namespace",
    "unshare",
)


def _detect_package_manager() -> str | None:
    for binary, kind in _PKG_MANAGERS:
        if shutil.which(binary):
            return kind
    return None


def _can_install_without_password() -> bool:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    sudo = shutil.which("sudo")
    if sudo is None:
        return False
    try:
        result = subprocess.run(
            [sudo, "-n", "true"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run_install(argv: list[str]) -> bool:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        logger.warning("bubblewrap install timed out: %s", argv)
        return False
    except OSError as exc:
        logger.warning("bubblewrap install failed to start: %s", exc)
        return False
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        logger.warning(
            "bubblewrap install exited %s: %s",
            result.returncode,
            stderr[:500] if stderr else "(no stderr)",
        )
        return False
    return True


def _install_argv(manager: str, *, use_sudo: bool) -> list[str] | None:
    prefix: list[str] = []
    if use_sudo:
        sudo = shutil.which("sudo")
        if sudo is None:
            return None
        prefix = [sudo, "-n"]

    if manager == "apt":
        apt = shutil.which("apt-get") or shutil.which("apt")
        if apt is None:
            return None
        # update is best-effort; ignore failure by chaining separately in caller
        return [
            *prefix,
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            apt,
            "install",
            "-y",
            "-qq",
            "bubblewrap",
        ]
    if manager == "dnf":
        dnf = shutil.which("dnf")
        return None if dnf is None else [*prefix, dnf, "install", "-y", "bubblewrap"]
    if manager == "yum":
        yum = shutil.which("yum")
        return None if yum is None else [*prefix, yum, "install", "-y", "bubblewrap"]
    if manager == "pacman":
        pacman = shutil.which("pacman")
        return None if pacman is None else [*prefix, pacman, "-Sy", "--noconfirm", "bubblewrap"]
    if manager == "zypper":
        zypper = shutil.which("zypper")
        return None if zypper is None else [*prefix, zypper, "install", "-y", "bubblewrap"]
    return None


def _install_bubblewrap(manager: str) -> bool:
    use_sudo = not (hasattr(os, "geteuid") and os.geteuid() == 0)
    if manager == "apt":
        apt = shutil.which("apt-get") or shutil.which("apt")
        if apt is not None:
            if use_sudo:
                sudo = shutil.which("sudo")
                update_argv = (
                    None
                    if sudo is None
                    else [
                        sudo,
                        "-n",
                        "env",
                        "DEBIAN_FRONTEND=noninteractive",
                        apt,
                        "update",
                        "-qq",
                    ]
                )
            else:
                update_argv = [
                    "env",
                    "DEBIAN_FRONTEND=noninteractive",
                    apt,
                    "update",
                    "-qq",
                ]
            if update_argv is not None:
                # Best-effort index refresh; install may still succeed without it.
                _run_install(update_argv)

    argv = _install_argv(manager, use_sudo=use_sudo)
    if argv is None:
        return False
    return _run_install(argv)


def _classify_probe_failure(detail: str) -> str:
    """Map a real ``bwrap`` failure to the shared diagnosis taxonomy."""
    text = (detail or "").lower()
    if any(marker in text for marker in _USERNS_DENY_MARKERS):
        return DIAGNOSIS_USERNS_DENIED
    return DIAGNOSIS_RUNTIME_FAILURE


def _install_command(manager: str | None) -> str | None:
    return {
        "apt": "sudo apt-get install -y bubblewrap",
        "dnf": "sudo dnf install -y bubblewrap",
        "yum": "sudo yum install -y bubblewrap",
        "pacman": "sudo pacman -S --noconfirm bubblewrap",
        "zypper": "sudo zypper install -y bubblewrap",
    }.get(manager or "")


def _recovery_commands(diagnosis: str | None, manager: str | None) -> list[str]:
    """Minimal-privilege operator commands for a diagnosis (display only)."""
    if diagnosis in (DIAGNOSIS_MISSING_BINARY, DIAGNOSIS_INSTALL_PERMISSION_DENIED):
        command = _install_command(manager)
        return [command] if command else []
    if diagnosis == DIAGNOSIS_USERNS_DENIED:
        # The helper installs only the dedicated profile and refuses to
        # overwrite an existing distribution-managed bubblewrap policy.
        return ["sudo bash scripts/setup-host-sandbox.sh"]
    return []


def _status_result(
    *,
    status: str,
    reason: str,
    detail: str,
    diagnosis: str | None,
    manager: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "detail": detail,
        "diagnosis": diagnosis,
        "commands": _recovery_commands(diagnosis, manager),
    }


def _probe_runtime(
    binary: str, *, installed: bool = False, manager: str | None = None
) -> dict[str, Any]:
    from harness_agent.backends.bwrap_shell import build_bwrap_argv

    try:
        with tempfile.TemporaryDirectory(prefix="maitu-bwrap-check-") as root:
            result = subprocess.run(
                build_bwrap_argv(bwrap=binary, root_dir=Path(root), command="true"),
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
                env={"PATH": os.defpath, "LANG": "C"},
            )
    except FileNotFoundError as exc:
        return _status_result(
            status="degraded",
            reason="sandbox_unavailable",
            detail=str(exc),
            diagnosis=DIAGNOSIS_MISSING_BINARY,
            manager=manager,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _status_result(
            status="degraded",
            reason="sandbox_unavailable",
            detail=str(exc),
            diagnosis=DIAGNOSIS_RUNTIME_FAILURE,
            manager=manager,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "bubblewrap jail failed to start").strip()[
            :1000
        ]
        return _status_result(
            status="degraded",
            reason="sandbox_unavailable",
            detail=detail,
            diagnosis=_classify_probe_failure(detail),
            manager=manager,
        )
    return {
        "status": "installed" if installed else "ready",
        "reason": "install_ok" if installed else "already_present",
        "detail": "",
        "diagnosis": None,
        "commands": [],
    }


def bwrap_status() -> dict[str, Any]:
    """Read-only bubblewrap capability report; never installs anything.

    Companion to :func:`ensure_bubblewrap` for ``GET /api/filesystem/bwrap-status``:
    same response shape, but a missing binary is only classified (with the
    operator's install command), never installed.
    """
    if not sys.platform.startswith("linux"):
        return _status_result(
            status="skipped",
            reason="not_linux",
            detail="bubblewrap jail is Linux-only; execute uses plain local_shell",
            diagnosis=None,
        )
    binary = shutil.which("bwrap")
    if binary:
        return _probe_runtime(binary, manager=_detect_package_manager())
    return _status_result(
        status="degraded",
        reason="not_installed",
        detail="bwrap is not installed; use POST /api/filesystem/ensure-bwrap to install",
        diagnosis=DIAGNOSIS_MISSING_BINARY,
        manager=_detect_package_manager(),
    )


def ensure_bubblewrap() -> dict[str, Any]:
    """Ensure ``bwrap`` is installed and can start the execution namespace jail.

    Returns a status dict suitable for ``POST /api/filesystem/ensure-bwrap``.
    Never raises for missing packages / privilege — callers must not block save.
    ``status`` / ``reason`` keep their existing values; ``diagnosis`` classifies
    the failure for the dashboard and ``commands`` lists display-only operator
    commands.
    """
    if not sys.platform.startswith("linux"):
        return _status_result(
            status="skipped",
            reason="not_linux",
            detail="bubblewrap jail is Linux-only; execute uses plain local_shell",
            diagnosis=None,
        )

    binary = shutil.which("bwrap")
    if binary:
        return _probe_runtime(binary, manager=_detect_package_manager())

    manager = _detect_package_manager()
    if manager is None:
        return _status_result(
            status="degraded",
            reason="no_package_manager",
            detail="no apt/dnf/yum/pacman/zypper; install bubblewrap manually",
            diagnosis=DIAGNOSIS_MISSING_BINARY,
        )

    if not _can_install_without_password():
        return _status_result(
            status="degraded",
            reason="no_privilege",
            detail="need root or passwordless sudo to install bubblewrap",
            diagnosis=DIAGNOSIS_INSTALL_PERMISSION_DENIED,
            manager=manager,
        )

    if not _install_bubblewrap(manager):
        return _status_result(
            status="degraded",
            reason="install_failed",
            detail=f"failed to install bubblewrap via {manager}",
            diagnosis=DIAGNOSIS_RUNTIME_FAILURE,
            manager=manager,
        )

    binary = shutil.which("bwrap")
    if binary:
        return _probe_runtime(binary, installed=True, manager=manager)

    return _status_result(
        status="degraded",
        reason="install_failed",
        detail="package manager reported success but bwrap still missing",
        diagnosis=DIAGNOSIS_RUNTIME_FAILURE,
        manager=manager,
    )


__all__ = ["bwrap_status", "ensure_bubblewrap"]
