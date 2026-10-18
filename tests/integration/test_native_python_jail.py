"""Real-jail verification that the platform Python is visible to ``execute``.

Complements ``test_bwrap_jail.py`` (harness factory path): these build the
native ``SourceShellBackend`` directly and need real ``bwrap`` plus a platform
venv outside the default system binds (the source-install layout).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from octop.infra.backend.managed_shell import SourceShellBackend, _platform_python_runtime
from tests.support.bwrap_marks import linux_bwrap_only


def _native_backend(tmp_path: Path) -> SourceShellBackend:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return SourceShellBackend(
        root_dir=workspace,
        workspace_dir=workspace,
        virtual_mode=True,
        bwrap_path=shutil.which("bwrap"),
    )


@linux_bwrap_only
def test_jail_runs_platform_python_with_packages(tmp_path: Path) -> None:
    runtime = _platform_python_runtime()
    if runtime is None or not runtime.binds:
        pytest.skip("platform interpreter lives inside the default system binds")
    backend = _native_backend(tmp_path)
    result = backend.execute("python3 -c 'import pypdf; print(\"platform-python-ok\")'")
    assert result.exit_code == 0
    assert "platform-python-ok" in result.output


@linux_bwrap_only
def test_jail_resolves_platform_interpreter_via_path(tmp_path: Path) -> None:
    runtime = _platform_python_runtime()
    if runtime is None or runtime.bin_dir is None:
        pytest.skip("platform interpreter needs no PATH overlay")
    backend = _native_backend(tmp_path)
    result = backend.execute("command -v python3")
    assert result.exit_code == 0
    assert runtime.bin_dir in result.output


@linux_bwrap_only
def test_jail_keeps_platform_binds_read_only(tmp_path: Path) -> None:
    runtime = _platform_python_runtime()
    if runtime is None or not runtime.binds:
        pytest.skip("platform interpreter lives inside the default system binds")
    backend = _native_backend(tmp_path)
    bind_dir = Path(runtime.binds[0][0])
    probe = bind_dir / "octop-ro-probe.txt"
    probe.write_text("untouched", encoding="utf-8")
    try:
        result = backend.execute(f"touch '{bind_dir}/octop-ro-probe.txt'")
        assert result.exit_code != 0
        assert probe.read_text(encoding="utf-8") == "untouched"
    finally:
        probe.unlink(missing_ok=True)
