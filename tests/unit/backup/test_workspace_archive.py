"""Unit tests for workspace zip archives."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends import resolve_backend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.backup.workspace_archive import export_workspace_zip, import_workspace_zip


@pytest.mark.asyncio
async def test_export_and_merge_import_includes_hidden_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_bytes(b"hello")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "note.md").write_bytes(b"note")
    (tmp_path / ".env").write_bytes(b"SECRET=value")
    (tmp_path / ".octop" / "sessions").mkdir(parents=True)
    (tmp_path / ".octop" / "sessions" / "history.jsonl").write_bytes(b"session")
    (tmp_path / "inbound").mkdir()
    (tmp_path / "inbound" / "request.json").write_bytes(b"inbound")
    (tmp_path / "outbound").mkdir()
    (tmp_path / "outbound" / "response.json").write_bytes(b"outbound")
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ignored").write_bytes(b"git")
    (tmp_path / ".venv" / "ignored").mkdir(parents=True)
    (tmp_path / ".venv" / "ignored" / "module.py").write_bytes(b"venv")
    (tmp_path / "node_modules" / "ignored").mkdir(parents=True)
    (tmp_path / "node_modules" / "ignored" / "package.js").write_bytes(b"node")
    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=False)
    workspace = BackendWorkspace(backend, tmp_path)

    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        names = set(zf.namelist())
        assert zf.read(".env") == b"SECRET=value"
        assert zf.read(".octop/sessions/history.jsonl") == b"session"
        assert zf.read("inbound/request.json") == b"inbound"
        assert zf.read("outbound/response.json") == b"outbound"
        assert not any(name.startswith((".git/", ".venv/", "node_modules/")) for name in names)
    assert names == {
        "hello.txt",
        "dir/note.md",
        ".env",
        ".octop/sessions/history.jsonl",
        "inbound/request.json",
        "outbound/response.json",
    }

    destination = tmp_path / "imported"
    destination.mkdir()
    imported = BackendWorkspace(
        LocalShellBackend(root_dir=str(destination), virtual_mode=False), destination
    )
    result = await import_workspace_zip(imported, blob, mode="merge", local_workspace_dir=None)
    assert result["imported"] == 6
    assert (destination / ".octop" / "sessions" / "history.jsonl").read_bytes() == b"session"
    assert (destination / ".env").read_bytes() == b"SECRET=value"


@pytest.mark.asyncio
async def test_replace_clears_local_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "old.txt").write_text("old", encoding="utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("new.txt", "new")
    data = buf.getvalue()

    backend = LocalShellBackend(root_dir=str(ws), virtual_mode=False)
    workspace = BackendWorkspace(backend, ws)
    result = await import_workspace_zip(workspace, data, mode="replace", local_workspace_dir=ws)
    assert result["imported"] == 1
    assert not (ws / "old.txt").exists()
    assert (ws / "new.txt").read_bytes() == b"new"


@pytest.mark.asyncio
async def test_export_ignores_same_named_file_at_backend_root(tmp_path: Path) -> None:
    """A same-named file at the backend root must not shadow the workspace's own file.

    Regression: under ``virtual_mode`` with a scoped ``root_dir`` that contains the
    workspace, ``BackendWorkspace`` probed ``{root_dir}/{rel}`` before
    ``{workspace_dir}/{rel}``, so the archive carried the backend-root ``AGENTS.md``
    while the entry name still said ``AGENTS.md``.
    """
    home = tmp_path / "home"
    workspace_dir = home / ".octop" / "workspaces" / "AGT1"
    workspace_dir.mkdir(parents=True)

    (home / "AGENTS.md").write_bytes(b"backend-root AGENTS.md")
    (workspace_dir / "AGENTS.md").write_bytes(b"workspace AGENTS.md")
    (workspace_dir / "SOUL.md").write_bytes(b"workspace SOUL.md")

    backend = resolve_backend(
        {"type": "filesystem", "root_dir": str(home), "virtual_mode": True},
        workspace_dir=workspace_dir,
    )
    workspace = BackendWorkspace(backend, workspace_dir, system_files_path=".octop")

    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        assert zf.read("AGENTS.md") == b"workspace AGENTS.md"
        assert zf.read("SOUL.md") == b"workspace SOUL.md"


@pytest.mark.asyncio
async def test_replace_import_preserves_hidden_state_omitted_by_old_archive(tmp_path: Path) -> None:
    """Replacing from an old archive must preserve hidden state it cannot restore."""
    ws = tmp_path / "ws"
    (ws / ".octop" / "sessions").mkdir(parents=True)
    (ws / ".octop" / "sessions" / "state.db").write_bytes(b"SESSION DB")
    (ws / ".octop" / "auth").mkdir(parents=True)
    (ws / ".octop" / "auth" / "token.json").write_bytes(b"TOKEN")
    (ws / ".env").write_bytes(b"KEY=kept")
    (ws / "old.txt").write_text("old", encoding="utf-8")

    # This mirrors pre-hidden-file archives: the ordinary file is present, while
    # the hidden state was never included.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("new.txt", "new")
    data = buf.getvalue()

    backend = LocalShellBackend(root_dir=str(ws), virtual_mode=False)
    workspace = BackendWorkspace(backend, ws)
    result = await import_workspace_zip(workspace, data, mode="replace", local_workspace_dir=ws)

    assert result["imported"] == 1
    assert not (ws / "old.txt").exists()
    assert (ws / "new.txt").read_bytes() == b"new"
    assert (ws / ".octop" / "sessions" / "state.db").read_bytes() == b"SESSION DB"
    assert (ws / ".octop" / "auth" / "token.json").read_bytes() == b"TOKEN"
    assert (ws / ".env").read_bytes() == b"KEY=kept"


@pytest.mark.asyncio
async def test_export_reads_legacy_root_system_dir_from_workspace(tmp_path: Path) -> None:
    """A legacy root ``skills/`` must not be shadowed by the backend root either.

    ``workspace.resolve_path()`` re-maps ``skills/`` under ``system_files_path``
    (``.octop``), so probing it for a workspace that still keeps legacy root
    ``skills/`` misses and falls back to the shadowed relative read.
    """
    home = tmp_path / "home"
    workspace_dir = home / ".octop" / "workspaces" / "AGT1"
    (workspace_dir / "skills" / "demo").mkdir(parents=True)
    (home / "skills" / "demo").mkdir(parents=True)

    (home / "skills" / "demo" / "SKILL.md").write_bytes(b"backend-root SKILL")
    (workspace_dir / "skills" / "demo" / "SKILL.md").write_bytes(b"workspace SKILL")

    backend = resolve_backend(
        {"type": "filesystem", "root_dir": str(home), "virtual_mode": True},
        workspace_dir=workspace_dir,
    )
    workspace = BackendWorkspace(backend, workspace_dir, system_files_path=".octop")

    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        assert zf.read("skills/demo/SKILL.md") == b"workspace SKILL"


class _MountlessBackend:
    """Backend exposing no host mount, so its files live outside the local disk."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    async def aglob(self, pattern: str, path: str = "/") -> Any:  # noqa: ARG002
        return SimpleNamespace(
            error=None,
            matches=[{"path": name, "is_dir": False} for name in sorted(self._files)],
        )

    def download_files(self, paths: list[str]) -> list[Any]:
        return [
            SimpleNamespace(path=name, content=self._files.get(name), error=None) for name in paths
        ]


@pytest.mark.asyncio
async def test_export_reads_remote_backend_over_stale_local_copy(tmp_path: Path) -> None:
    """A mountless backend must be read via the protocol, not from a stale local file."""
    (tmp_path / "AGENTS.md").write_bytes(b"stale local AGENTS.md")
    workspace = BackendWorkspace(_MountlessBackend({"/AGENTS.md": b"remote AGENTS.md"}), tmp_path)

    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        assert zf.read("AGENTS.md") == b"remote AGENTS.md"


@pytest.mark.asyncio
async def test_export_skips_symlinks_escaping_workspace(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace_dir.mkdir()
    outside.write_bytes(b"outside secret")
    try:
        (workspace_dir / "escape.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(workspace_dir), virtual_mode=False), workspace_dir
    )
    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        assert "escape.txt" not in zf.namelist()


@pytest.mark.asyncio
async def test_export_includes_hidden_files_from_mountless_backend(tmp_path: Path) -> None:
    workspace = BackendWorkspace(
        _MountlessBackend(
            {
                "/.env": b"REMOTE=value",
                "/.octop/sessions/history.jsonl": b"remote session",
                "/inbound/input.json": b"input",
                "/outbound/output.json": b"output",
                "/.git/config": b"excluded",
                "/node_modules/pkg/index.js": b"excluded",
            }
        ),
        tmp_path,
    )

    blob = await export_workspace_zip(workspace)
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        assert zf.read(".env") == b"REMOTE=value"
        assert zf.read(".octop/sessions/history.jsonl") == b"remote session"
        assert zf.read("inbound/input.json") == b"input"
        assert zf.read("outbound/output.json") == b"output"
        assert ".git/config" not in zf.namelist()
        assert "node_modules/pkg/index.js" not in zf.namelist()
