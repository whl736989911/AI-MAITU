"""Tests for best-effort bubblewrap (``bwrap``) provisioning — all platforms."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from octop.infra.utils import bwrap as bwrap_mod
from octop.infra.utils.bwrap import bwrap_status, ensure_bubblewrap


def test_ensure_skips_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "darwin")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    result = ensure_bubblewrap()
    assert result["status"] == "skipped"
    assert result["reason"] == "not_linux"


def test_ensure_skips_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "win32")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    result = ensure_bubblewrap()
    assert result["status"] == "skipped"
    assert result["reason"] == "not_linux"
    assert "Linux-only" in result["detail"]


def test_ensure_ready_when_bwrap_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: "/usr/bin/bwrap")
    monkeypatch.setattr(
        bwrap_mod.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    result = ensure_bubblewrap()
    assert result["status"] == "ready"
    assert result["reason"] == "already_present"


def test_present_bwrap_is_not_ready_when_namespace_mapping_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        bwrap_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout="", stderr="bwrap: setting up uid map: Permission denied"
        ),
    )
    result = ensure_bubblewrap()
    assert result["status"] == "degraded"
    assert result["reason"] == "sandbox_unavailable"
    assert "uid map" in result["detail"]


def test_ensure_degraded_when_no_package_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: None)
    result = ensure_bubblewrap()
    assert result["status"] == "degraded"
    assert result["reason"] == "no_package_manager"


def test_ensure_degraded_when_no_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: "apt")
    monkeypatch.setattr("octop.infra.utils.bwrap._can_install_without_password", lambda: False)
    result = ensure_bubblewrap()
    assert result["status"] == "degraded"
    assert result["reason"] == "no_privilege"


def test_ensure_installed_after_successful_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    calls = {"n": 0}

    def which(name: str) -> str | None:
        if name != "bwrap":
            return None
        calls["n"] += 1
        return None if calls["n"] == 1 else "/usr/bin/bwrap"

    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", which)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: "apt")
    monkeypatch.setattr("octop.infra.utils.bwrap._can_install_without_password", lambda: True)
    install = MagicMock(return_value=True)
    monkeypatch.setattr("octop.infra.utils.bwrap._install_bubblewrap", install)
    monkeypatch.setattr(
        bwrap_mod.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    result = ensure_bubblewrap()
    assert result["status"] == "installed"
    assert result["reason"] == "install_ok"
    install.assert_called_once_with("apt")


def test_ensure_degraded_when_install_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: "dnf")
    monkeypatch.setattr("octop.infra.utils.bwrap._can_install_without_password", lambda: True)
    monkeypatch.setattr("octop.infra.utils.bwrap._install_bubblewrap", lambda _mgr: False)
    result = ensure_bubblewrap()
    assert result["status"] == "degraded"
    assert result["reason"] == "install_failed"


@pytest.mark.parametrize(
    ("manager", "binary", "expected_tail"),
    [
        ("apt", "apt-get", ["install", "-y", "-qq", "bubblewrap"]),
        ("dnf", "dnf", ["install", "-y", "bubblewrap"]),
        ("yum", "yum", ["install", "-y", "bubblewrap"]),
        ("pacman", "pacman", ["-Sy", "--noconfirm", "bubblewrap"]),
        ("zypper", "zypper", ["install", "-y", "bubblewrap"]),
    ],
)
def test_install_argv_shapes(
    monkeypatch: pytest.MonkeyPatch,
    manager: str,
    binary: str,
    expected_tail: list[str],
) -> None:
    """Argv tail is stable on every platform (uses mocked ``shutil.which``)."""
    monkeypatch.setattr(
        "octop.infra.utils.bwrap.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == binary else None,
    )
    argv = bwrap_mod._install_argv(manager, use_sudo=False)
    assert argv is not None
    assert argv[-len(expected_tail) :] == expected_tail


def test_install_argv_uses_passwordless_sudo_prefix_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "octop.infra.utils.bwrap.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"sudo", "dnf"} else None,
    )
    argv = bwrap_mod._install_argv("dnf", use_sudo=True)
    assert argv is not None
    assert argv[0].endswith("sudo")
    assert argv[1] == "-n"


def test_install_argv_returns_none_for_unknown_manager() -> None:
    assert bwrap_mod._install_argv("unknown", use_sudo=False) is None


# ---------------------------------------------------------------------------
# B4: diagnosis taxonomy + read-only status endpoint data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("bwrap: setting up uid map: Permission denied", "userns_denied"),
        ("bwrap: setting up gid map: Operation not permitted", "userns_denied"),
        ("bwrap: Creating new namespace failed: Operation not permitted", "userns_denied"),
        ("bwrap: unshare(CLONE_NEWUSER): Permission denied", "userns_denied"),
        ("bwrap: Can't find mount point /oldroot: No such file or directory", "runtime_failure"),
        ("bwrap: locking new mount: No space left on device", "runtime_failure"),
        ("", "runtime_failure"),
    ],
)
def test_classify_probe_failure_boundary(stderr: str, expected: str) -> None:
    assert bwrap_mod._classify_probe_failure(stderr) == expected


def _present_bwrap_with_stderr(monkeypatch: pytest.MonkeyPatch, stderr: str) -> dict[str, object]:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        bwrap_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="", stderr=stderr),
    )
    return ensure_bubblewrap()


def test_apparmor_uid_map_denial_diagnoses_userns_with_profile_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _present_bwrap_with_stderr(monkeypatch, "bwrap: setting up uid map: Permission denied")
    assert result["status"] == "degraded"
    assert result["reason"] == "sandbox_unavailable"
    assert result["diagnosis"] == "userns_denied"


def test_other_probe_failures_diagnose_runtime_failure_without_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _present_bwrap_with_stderr(
        monkeypatch, "bwrap: Can't find mount point /oldroot: No such file or directory"
    )
    assert result["diagnosis"] == "runtime_failure"
    assert result["commands"] == []


def test_probe_timeout_diagnoses_runtime_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")

    def raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["bwrap"], timeout=10)

    monkeypatch.setattr(bwrap_mod.subprocess, "run", raise_timeout)
    result = ensure_bubblewrap()
    assert result["status"] == "degraded"
    assert result["diagnosis"] == "runtime_failure"


def test_probe_missing_binary_diagnoses_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")

    def raise_missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/usr/bin/bwrap")

    monkeypatch.setattr(bwrap_mod.subprocess, "run", raise_missing)
    result = ensure_bubblewrap()
    assert result["diagnosis"] == "missing_binary"
    assert result["commands"] == ["sudo apt-get install -y bubblewrap"]


def test_no_package_manager_diagnoses_missing_binary_without_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: None)
    result = ensure_bubblewrap()
    assert result["reason"] == "no_package_manager"
    assert result["diagnosis"] == "missing_binary"
    assert result["commands"] == []


def test_install_failed_diagnoses_runtime_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octop.infra.utils.bwrap.sys.platform", "linux")
    monkeypatch.setattr("octop.infra.utils.bwrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: "dnf")
    monkeypatch.setattr("octop.infra.utils.bwrap._can_install_without_password", lambda: True)
    monkeypatch.setattr("octop.infra.utils.bwrap._install_bubblewrap", lambda _mgr: False)
    result = ensure_bubblewrap()
    assert result["diagnosis"] == "runtime_failure"
    assert result["commands"] == []


def test_bwrap_status_never_installs_and_reports_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr("octop.infra.utils.bwrap._detect_package_manager", lambda: "apt")
    result = bwrap_status()
    assert result["status"] == "degraded"
    assert result["reason"] == "not_installed"
    assert result["diagnosis"] == "missing_binary"
    assert result["commands"] == ["sudo apt-get install -y bubblewrap"]


def test_bwrap_status_ready_when_jail_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "linux")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        bwrap_mod.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    result = bwrap_status()
    assert result["status"] == "ready"
    assert result["reason"] == "already_present"
    assert result["diagnosis"] is None
    assert result["commands"] == []


def test_bwrap_status_skips_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bwrap_mod.sys, "platform", "darwin")
    monkeypatch.setattr(bwrap_mod.shutil, "which", lambda _name: None)
    result = bwrap_status()
    assert result["status"] == "skipped"
    assert result["reason"] == "not_linux"
    assert result["diagnosis"] is None
    assert result["commands"] == []
