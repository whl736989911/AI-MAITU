"""Regression tests for installed backend compatibility patches."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend

from octop.infra.agents.backend_compat import (
    _bounded_pathless_grep,
    _decode_output,
    install_backend_compatibility_patches,
)


def test_pathless_python_grep_is_bounded_to_workspace_and_skips_pseudo_fs(
    tmp_path: Path, monkeypatch
) -> None:
    from deepagents.backends import filesystem as filesystem_module

    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "src").mkdir()
    (root / ".git" / "hidden.py").write_text("needle\n", encoding="utf-8")
    (root / "node_modules" / "hidden.py").write_text("needle\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("你好 needle\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("needle\n", encoding="utf-8")
    with suppress(OSError):
        (root / "escape.py").symlink_to(outside)

    install_backend_compatibility_patches()
    monkeypatch.setattr(filesystem_module, "_resolve_ripgrep_path", lambda: None)
    backend = FilesystemBackend(root_dir=root)
    result = backend.grep("needle")
    assert result.error is None
    assert result.matches == [{"path": "/src/main.py", "line": 1, "text": "你好 needle"}]
    assert result.truncated is False


def test_pathless_grep_reports_result_cap(tmp_path: Path, monkeypatch) -> None:
    from octop.infra.agents import backend_compat as compat

    (tmp_path / "one.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(compat, "_PATHLESS_GREP_MAX_RESULTS", 1)
    result = _bounded_pathless_grep(FilesystemBackend(root_dir=tmp_path), "needle", None)
    assert len(result.matches or []) == 1
    assert result.truncated is True


def test_output_decoder_preserves_chinese_gb18030_bytes() -> None:
    raw = "中文输出".encode("gb18030")
    assert _decode_output(raw, {"PYTHONIOENCODING": "gb18030"}) == "中文输出"


def test_installed_local_shell_decodes_real_gb18030_subprocess(tmp_path: Path) -> None:
    from harness_agent.backends.local_shell import HarnessLocalShellBackend

    install_backend_compatibility_patches()
    payload = "文件完成".encode("gb18030").hex()
    code = f"import sys;sys.stdout.buffer.write(bytes.fromhex('{payload}'))"
    command = (
        f'"{sys.executable}" -c "{code}"'
        if os.name == "nt"
        else f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    )
    backend = HarnessLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    response = backend.execute(command)
    assert response.exit_code == 0
    assert response.output == "文件完成"


def test_installer_patches_exact_backend_classes_once() -> None:
    install_backend_compatibility_patches()
    install_backend_compatibility_patches()
    assert FilesystemBackend._octop_bounded_grep_installed is True


def test_binary_result_format_reliably_decodes_gb18030() -> None:
    from octop.infra.agents.backend_compat import _format_binary_result

    result = subprocess.CompletedProcess(
        args="shell", returncode=0, stdout="文件完成".encode("gb18030"), stderr=b""
    )
    response = _format_binary_result(
        result, max_output_bytes=1000, env={"PYTHONIOENCODING": "gb18030"}
    )
    assert response.output == "文件完成"
    assert response.exit_code == 0
