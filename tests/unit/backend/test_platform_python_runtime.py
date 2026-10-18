"""Platform Python runtime discovery for the native-install bwrap jail (B2).

The helpers under test monkeypatch ``sys.prefix`` / ``sys.base_prefix`` /
``sys.executable``, so these tests run on every OS.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from octop.infra.backend import managed_shell
from octop.infra.backend.managed_shell import (
    _mark_binds_read_only,
    _platform_python_runtime,
    _python_runtime_env,
    _PythonRuntimeMounts,
)

_MAJOR_MINOR = f"python{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture(autouse=True)
def _reset_runtime_cache() -> Iterator[None]:
    _platform_python_runtime.cache_clear()
    yield
    _platform_python_runtime.cache_clear()


def _make_venv(tmp_path: Path) -> tuple[Path, Path]:
    venv = tmp_path / "platform" / ".venv"
    base = tmp_path / "uv" / "cpython-3.12.7"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / _MAJOR_MINOR / "site-packages").mkdir(parents=True)
    (base / "bin").mkdir(parents=True)
    return venv, base


def _patch_interpreter(monkeypatch: pytest.MonkeyPatch, venv: Path, base: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "executable", str(venv / "bin" / "python3"))


def test_binds_venv_and_real_interpreter_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """uv-style layout: venv outside system dirs, real interpreter elsewhere."""
    venv, base = _make_venv(tmp_path)
    _patch_interpreter(monkeypatch, venv, base)
    assert _platform_python_runtime() == _PythonRuntimeMounts(
        binds=((str(venv), str(venv)), (str(base), str(base))),
        bin_dir=str(venv / "bin"),
        site_packages=str(venv / "lib" / _MAJOR_MINOR / "site-packages"),
    )


def test_system_dir_prefix_needs_no_binds_but_keeps_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Container layout: prefix beneath a default ro-bind — env overlay only."""
    venv, base = _make_venv(tmp_path)
    _patch_interpreter(monkeypatch, venv, base)
    monkeypatch.setattr(managed_shell, "_SYSTEM_RO_BINDS", (str(tmp_path),))
    assert _platform_python_runtime() == _PythonRuntimeMounts(
        binds=(),
        bin_dir=str(venv / "bin"),
        site_packages=str(venv / "lib" / _MAJOR_MINOR / "site-packages"),
    )


def test_returns_none_when_platform_env_already_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    venv = tmp_path / ".venv"  # no bin/ or site-packages created
    base = tmp_path / "base"
    _patch_interpreter(monkeypatch, venv, base)
    monkeypatch.setattr(managed_shell, "_SYSTEM_RO_BINDS", (str(tmp_path),))
    assert _platform_python_runtime() is None


def test_home_is_never_bound(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    venv, base = _make_venv(tmp_path)
    _patch_interpreter(monkeypatch, venv, base)
    monkeypatch.setattr(Path, "home", lambda: venv)
    runtime = _platform_python_runtime()
    assert runtime is not None
    assert (str(venv), str(venv)) not in runtime.binds
    assert (str(base), str(base)) in runtime.binds


def test_non_linux_needs_no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert _platform_python_runtime() is None


# ---------------------------------------------------------------------------
# env overlay: PATH / PYTHONPATH
# ---------------------------------------------------------------------------


def test_env_overlay_prepends_bin_and_appends_site_packages(tmp_path: Path) -> None:
    binary = str(tmp_path / "venv" / "bin")
    packages = str(tmp_path / "venv" / "site-packages")
    original = [str(tmp_path / "system-bin"), str(tmp_path / "other-bin")]
    runtime = _PythonRuntimeMounts(binds=(), bin_dir=binary, site_packages=packages)
    env = _python_runtime_env({"PATH": os.pathsep.join(original)}, runtime)
    assert env["PATH"].split(os.pathsep) == [binary, *original]
    assert env["PYTHONPATH"] == packages


def test_env_overlay_is_idempotent_and_keeps_existing_pythonpath(tmp_path: Path) -> None:
    binary = str(tmp_path / "venv" / "bin")
    packages = str(tmp_path / "venv" / "site-packages")
    paths = os.pathsep.join([binary, str(tmp_path / "system-bin")])
    user_packages = str(tmp_path / "workspace" / "lib")
    runtime = _PythonRuntimeMounts(binds=(), bin_dir=binary, site_packages=packages)
    env = _python_runtime_env({"PATH": paths, "PYTHONPATH": user_packages}, runtime)
    assert env["PATH"] == paths
    assert env["PYTHONPATH"].split(os.pathsep) == [user_packages, packages]


def test_env_overlay_without_dirs_is_noop() -> None:
    runtime = _PythonRuntimeMounts(binds=(), bin_dir=None, site_packages=None)
    assert _python_runtime_env({"PATH": "/usr/bin"}, runtime) == {"PATH": "/usr/bin"}


# ---------------------------------------------------------------------------
# read-only bind flipping inside the assembled bwrap argv
# ---------------------------------------------------------------------------


def test_mark_binds_read_only_flips_only_runtime_pairs() -> None:
    argv = [
        "bwrap",
        "--bind",
        "/ws",
        "/",
        "--ro-bind",
        "/usr",
        "/usr",
        "--bind",
        "/plat/venv",
        "/plat/venv",
        "--dev",
        "/dev",
    ]
    _mark_binds_read_only(argv, (("/plat/venv", "/plat/venv"),))
    assert argv[1] == "--bind"  # jail root stays writable
    assert argv[7] == "--ro-bind"
    assert argv.count("--bind") == 1


def test_built_argv_marks_platform_runtime_read_only(tmp_path: Path) -> None:
    from harness_agent.backends.bwrap_shell import build_bwrap_argv

    argv = build_bwrap_argv(
        bwrap="/usr/bin/bwrap",
        root_dir=tmp_path,
        command="true",
        extra_binds=[("/ws/skills", "/skills"), ("/plat/venv", "/plat/venv")],
    )
    _mark_binds_read_only(argv, (("/plat/venv", "/plat/venv"),))
    assert argv[argv.index("/skills") - 2] == "--bind"
    assert argv[argv.index("/plat/venv") - 1] == "--ro-bind"
