"""A missing jail or a workspace loader override must never execute on the host."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.support.fakes import fake_bin_path

from octop.infra.backend import managed_shell
from octop.infra.backend.managed_shell import ManagedShellBackend, SourceShellBackend
from octop.infra.errors import ErrorCode, OctopError

posix_only = pytest.mark.skipif(os.name != "posix", reason="bubblewrap execution is Linux-only")


def _unmanaged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)


def _bwrap_on_path(monkeypatch: pytest.MonkeyPatch, path: str = "/usr/bin/bwrap") -> None:
    monkeypatch.setattr(
        "harness_agent.backends.bwrap_shell.resolve_bubbled_bwrap",
        lambda **_kwargs: path,
    )


def _bwrap_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "harness_agent.backends.bwrap_shell.resolve_bubbled_bwrap",
        lambda **_kwargs: None,
    )


def _runtime_mounts(tmp_path: Path) -> Any:
    venv = tmp_path / "plat" / ".venv"
    (venv / "bin").mkdir(parents=True)
    return managed_shell._PythonRuntimeMounts(
        binds=((str(venv), str(venv)),),
        bin_dir=str(venv / "bin"),
        site_packages=None,
    )


@posix_only
def test_missing_jail_does_not_execute_host_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "license-trusted-keys.json")
    )
    sentinel = tmp_path / "host-execution.txt"
    backend = ManagedShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=str(tmp_path / "missing-bwrap"),
    )
    with pytest.raises(OctopError) as caught:
        backend.execute("touch host-execution.txt")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not sentinel.exists()


@posix_only
def test_workspace_dotenv_cannot_inject_host_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "license-trusted-keys.json")
    )
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    (tmp_path / ".env").write_text("LD_PRELOAD=/workspace/attacker.so\n", encoding="utf-8")
    backend = ManagedShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=str(tmp_path / "missing-bwrap"),
    )
    with pytest.raises(ValueError, match="LD_PRELOAD"):
        backend.execute("true")


# ---------------------------------------------------------------------------
# Native (unmanaged) source installs: conversion + fail-closed boundaries
# ---------------------------------------------------------------------------


@posix_only
def test_unmanaged_local_shell_requires_bwrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured sandbox never silently degrades to host execution."""
    _unmanaged(monkeypatch)
    _bwrap_missing(monkeypatch)
    workspace = tmp_path / "ws"
    spec = {"type": "local_shell", "root_dir": str(workspace), "virtual_mode": True}
    with pytest.raises(OctopError) as caught:
        managed_shell.managed_shell_spec(spec, workspace_dir=workspace, system_files_path=".octop")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_native_shell_never_uses_host_execution_without_linux_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    _unmanaged(monkeypatch)
    monkeypatch.setattr(managed_shell.sys, "platform", platform)
    with pytest.raises(OctopError) as caught:
        managed_shell.managed_shell_spec(
            {"type": "local_shell", "root_dir": str(tmp_path), "virtual_mode": True},
            workspace_dir=tmp_path,
            system_files_path=".octop",
        )
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


@posix_only
@pytest.mark.parametrize(
    "spec",
    [
        {"type": "local_shell", "root_dir": "/", "virtual_mode": True},
        {"type": "local_shell", "virtual_mode": False},
    ],
)
def test_native_shell_rejects_unconfined_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: dict[str, Any]
) -> None:
    _unmanaged(monkeypatch)
    _bwrap_on_path(monkeypatch)
    with pytest.raises(OctopError) as caught:
        managed_shell.managed_shell_spec(spec, workspace_dir=tmp_path, system_files_path=".octop")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


@posix_only
def test_unmanaged_composite_converts_only_local_shell_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unmanaged(monkeypatch)
    _bwrap_on_path(monkeypatch)
    workspace = tmp_path / "ws"
    default = {"type": "local_shell", "root_dir": str(workspace), "virtual_mode": True}
    files = {"type": "filesystem", "root_dir": str(workspace), "virtual_mode": True}
    spec = {"type": "composite", "default": default, "routes": {"files": files}}
    result = managed_shell.managed_shell_spec(
        spec, workspace_dir=workspace, system_files_path=".octop"
    )
    assert result is not spec
    assert not isinstance(result["default"], dict)
    assert result["routes"]["files"] == files


@posix_only
def test_source_execute_binds_platform_python_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "plat" / ".venv"
    (venv / "bin").mkdir(parents=True)
    site = venv / "lib" / "python3.12" / "site-packages"
    mounts = managed_shell._PythonRuntimeMounts(
        binds=((str(venv), str(venv)),),
        bin_dir=str(venv / "bin"),
        site_packages=str(site),
    )
    monkeypatch.setattr(managed_shell, "_platform_python_runtime", lambda: mounts)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    monkeypatch.setattr(managed_shell.subprocess, "run", fake_run)
    backend = SourceShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path="/usr/bin/bwrap",
    )
    response = backend.execute("true")
    assert getattr(response, "exit_code", None) == 0
    argv = captured["argv"]
    pair = [str(venv), str(venv)]
    flipped = [
        index
        for index, token in enumerate(argv)
        if token == "--ro-bind" and argv[index + 1 : index + 3] == pair
    ]
    assert flipped, argv
    env = captured["env"]
    assert env["PATH"].split(os.pathsep)[0] == str(venv / "bin")
    assert str(site) in env["PYTHONPATH"].split(os.pathsep)


@posix_only
def test_source_execute_refuses_host_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unmanaged(monkeypatch)
    monkeypatch.setattr(managed_shell, "_platform_python_runtime", lambda: None)
    backend = SourceShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=str(tmp_path / "missing-bwrap"),
    )
    sentinel = tmp_path / "host-execution.txt"
    with pytest.raises(OctopError) as caught:
        backend.execute("touch host-execution.txt")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not sentinel.exists()


@posix_only
def test_source_execute_with_runtime_still_refuses_host_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unmanaged(monkeypatch)
    monkeypatch.setattr(
        managed_shell, "_platform_python_runtime", lambda: _runtime_mounts(tmp_path)
    )
    backend = SourceShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=str(tmp_path / "missing-bwrap"),
    )
    sentinel = tmp_path / "host-execution.txt"
    with pytest.raises(OctopError) as caught:
        backend.execute("touch host-execution.txt")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not sentinel.exists()


@posix_only
def test_managed_with_runtime_still_refuses_host_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    monkeypatch.setattr(
        managed_shell, "_platform_python_runtime", lambda: _runtime_mounts(tmp_path)
    )
    backend = ManagedShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=str(tmp_path / "missing-bwrap"),
    )
    sentinel = tmp_path / "host-execution.txt"
    with pytest.raises(OctopError) as caught:
        backend.execute("touch host-execution.txt")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not sentinel.exists()


def test_managed_execute_accepts_trusted_platform_python_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octop.infra.utils.host_dirs.license_managed_install", lambda: True)
    site = str(tmp_path / "plat" / ".venv" / "site-packages")
    runtime_root = str(tmp_path / "plat" / ".venv")
    mounts = managed_shell._PythonRuntimeMounts(
        binds=((runtime_root, runtime_root),),
        bin_dir=str(tmp_path / "plat" / ".venv" / "bin"),
        site_packages=site,
    )
    monkeypatch.setattr(managed_shell, "_platform_python_runtime", lambda: mounts)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    monkeypatch.setattr(managed_shell.subprocess, "run", fake_run)
    backend = ManagedShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=fake_bin_path("bwrap"),
        env={},
        inherit_env=False,
    )

    response = backend.execute("python -c 'import sys; print(sys.executable)'")

    assert getattr(response, "exit_code", None) == 0
    assert captured["env"]["PATH"].split(os.pathsep)[0] == mounts.bin_dir
    assert captured["env"]["PYTHONPATH"] == site
    assert captured["argv"][0] == fake_bin_path("bwrap")
    argv = captured["argv"]
    runtime_bind = argv.index(runtime_root)
    assert argv[runtime_bind - 1 : runtime_bind + 2] == [
        "--ro-bind",
        runtime_root,
        runtime_root,
    ]


@pytest.mark.parametrize("name", ["PATH", "PYTHONPATH"])
def test_managed_execute_still_rejects_caller_runtime_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr("octop.infra.utils.host_dirs.license_managed_install", lambda: True)
    monkeypatch.setenv(name, fake_bin_path("trusted-host-runtime"))
    monkeypatch.setattr(
        managed_shell,
        "_platform_python_runtime",
        lambda: managed_shell._PythonRuntimeMounts(
            binds=(), bin_dir=None, site_packages=fake_bin_path("platform-site-packages")
        ),
    )
    backend = ManagedShellBackend(
        root_dir=tmp_path,
        workspace_dir=tmp_path,
        virtual_mode=True,
        bwrap_path=fake_bin_path("bwrap"),
        env={name: fake_bin_path("caller-runtime")},
    )

    with pytest.raises(ValueError, match=name):
        backend.execute("python -c 'print(1)'")


@posix_only
def test_managed_local_shell_builds_hard_closed_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    monkeypatch.setattr(managed_shell.shutil, "which", lambda *_a, **_k: "/usr/bin/bwrap")
    created: dict[str, Any] = {}
    real_cls = managed_shell.ManagedShellBackend

    def factory(**kwargs: Any) -> Any:
        created.update(kwargs)
        return real_cls(**kwargs)

    monkeypatch.setattr(managed_shell, "ManagedShellBackend", factory)
    workspace = tmp_path / "ws"
    spec = {"type": "local_shell", "root_dir": str(workspace), "virtual_mode": True}
    result = managed_shell.managed_shell_spec(
        spec, workspace_dir=workspace, system_files_path=".octop"
    )
    assert created["bwrap_path"] == "/usr/bin/bwrap"
    assert result is not None


@posix_only
def test_managed_local_shell_requires_trusted_bwrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    monkeypatch.setattr(managed_shell.shutil, "which", lambda *_a, **_k: None)
    workspace = tmp_path / "ws"
    spec = {"type": "local_shell", "root_dir": str(workspace), "virtual_mode": True}
    with pytest.raises(OctopError):
        managed_shell.managed_shell_spec(spec, workspace_dir=workspace, system_files_path=".octop")


@posix_only
def test_managed_local_shell_rejects_unconfined_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    monkeypatch.setattr(managed_shell.shutil, "which", lambda *_a, **_k: "/usr/bin/bwrap")
    spec = {"type": "local_shell", "root_dir": "/", "virtual_mode": True}
    with pytest.raises(OctopError):
        managed_shell.managed_shell_spec(
            spec, workspace_dir=tmp_path / "ws", system_files_path=".octop"
        )
