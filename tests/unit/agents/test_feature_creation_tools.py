from __future__ import annotations

import json
import stat
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_creation_tools as module
from octop.infra.agents.feature_creation_tools import (
    MAX_ARCHIVE_FILE_BYTES,
    _validate_archive,
    build_feature_creation_tools,
)


def _zip(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.create_system = 3
                info.external_attr = mode << 16
            archive.writestr(info, content)
    return out.getvalue()


def _workspace(root: Path) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


def _setup(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    permission: bool = True,
    owner: int = 7,
    kind: str = "agent",
) -> tuple[dict[str, Any], BackendWorkspace, list[Any]]:
    ws = _workspace(tmp_path / "workspace")
    ws_dir = ws.workspace_dir
    (ws_dir / "inbound").mkdir(parents=True, exist_ok=True)
    caller = SimpleNamespace(agent_id="personal", name="My agent", kind=kind, user_id=7)

    created: list[Any] = []

    async def create(spec):
        created.append(spec)
        return SimpleNamespace(agent_id=spec.agent_id, name=spec.name, last_state="running")

    registry = SimpleNamespace(
        get_row=lambda agent_id: caller if agent_id == "personal" and owner == 7 else None,
        workspace_for_agent=lambda agent_id: ws if agent_id == "personal" else None,
        create=create,
    )
    user = SimpleNamespace(
        id=7,
        role="user",
        permissions=["features"] if permission else [],
        denied_permissions=[],
        org_unit=None,
        is_admin=False,
        disabled=0,
    )
    repos = SimpleNamespace(
        user_repo=SimpleNamespace(get=lambda user_id: user if user_id == 7 else None)
    )
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {"configurable": {"agent_id": "personal", "user": "7"}},
    )
    return (
        {tool.name: tool for tool in build_feature_creation_tools(registry=registry, repos=repos)},
        ws,
        created,
    )


@pytest.mark.asyncio
async def test_feature_create_creates_owned_agent_and_returns_conversation_and_workspace(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, _ws, created = _setup(monkeypatch, tmp_path)
    result = json.loads(await tools["feature_create"].ainvoke({"name": "Invoice helper"}))
    assert result["created"] is True
    assert result["feature_id"] == "invoice-helper"
    assert result["agent_id"] == "feat-invoice-helper"
    assert "conversation" in result and "workspace" in result
    assert created[0].user_id == 7
    assert created[0].kind == "feature"


@pytest.mark.asyncio
async def test_feature_create_refuses_missing_features_permission(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, _ws, created = _setup(monkeypatch, tmp_path, permission=False)
    result = json.loads(await tools["feature_create"].ainvoke({"name": "No access"}))
    assert "features" in result["error"]
    assert created == []


@pytest.mark.asyncio
async def test_feature_agent_cannot_create_nested_feature(tmp_path: Path, monkeypatch: Any) -> None:
    tools, _ws, created = _setup(monkeypatch, tmp_path, kind="feature")
    result = json.loads(await tools["feature_create"].ainvoke({"name": "Nested"}))
    assert "cannot create another feature" in result["error"]
    assert created == []


@pytest.mark.asyncio
async def test_archive_extract_refuses_another_users_workspace(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, ws, _created = _setup(monkeypatch, tmp_path, owner=99)
    await ws.aupload_bytes("inbound/source.zip", _zip([("file.txt", b"x", None)]))
    result = json.loads(
        await tools["archive_extract"].ainvoke({"archive_path": "inbound/source.zip"})
    )
    assert "owned by the current user" in result["error"]
    assert not (ws.workspace_dir / "inbound/source/file.txt").exists()


@pytest.mark.asyncio
async def test_feature_create_refuses_agent_owned_by_someone_else(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, _ws, created = _setup(monkeypatch, tmp_path, owner=99)
    result = json.loads(await tools["feature_create"].ainvoke({"name": "No access"}))
    assert "owned by the current user" in result["error"]
    assert created == []


@pytest.mark.asyncio
async def test_archive_extract_writes_only_to_inbound_and_returns_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, ws, _created = _setup(monkeypatch, tmp_path)
    await ws.aupload_bytes(
        "inbound/source.zip",
        _zip([("project/README.md", b"read me", None), ("project/src/main.py", b"pass", None)]),
    )
    result = json.loads(
        await tools["archive_extract"].ainvoke({"archive_path": "inbound/source.zip"})
    )
    assert result["extracted_to"] == "inbound/source"
    assert result["files"] == [
        "inbound/source/project/README.md",
        "inbound/source/project/src/main.py",
    ]
    assert (ws.workspace_dir / "inbound/source/project/README.md").read_text() == "read me"
    assert "feature_workflow_save" in result["next"]


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "nested/../../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "\\\\server\\share\\x",
    ],
)
def test_validate_archive_rejects_traversal_and_absolute_paths(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe path|absolute path"):
        _validate_archive(_zip([(name, b"x", None)]))


def test_validate_archive_rejects_symbolic_links() -> None:
    symlink_mode = stat.S_IFLNK | 0o777
    with pytest.raises(ValueError, match="symbolic links"):
        _validate_archive(_zip([("link", b"target", symlink_mode)]))


def test_validate_archive_rejects_too_many_entries(monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "MAX_ARCHIVE_ENTRIES", 2)
    entries = [(f"f{i}.txt", b"x", None) for i in range(3)]
    with pytest.raises(ValueError, match="entries"):
        _validate_archive(_zip(entries))


def test_validate_archive_rejects_oversized_single_file(monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "MAX_ARCHIVE_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="per-file limit"):
        _validate_archive(_zip([("large.bin", b"12345", None)]))


def test_validate_archive_rejects_oversized_total_uncompressed_size(monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "MAX_ARCHIVE_FILE_BYTES", 10)
    monkeypatch.setattr(module, "MAX_ARCHIVE_TOTAL_BYTES", 25)
    entries = [(f"part-{index}", b"x" * 10, None) for index in range(3)]
    with pytest.raises(ValueError, match="total limit"):
        _validate_archive(_zip(entries))


@pytest.mark.asyncio
async def test_archive_extract_rejects_non_zip_and_workspace_escape(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tools, ws, _created = _setup(monkeypatch, tmp_path)
    await ws.aupload_bytes("inbound/not.zip", b"not a zip")
    result = json.loads(await tools["archive_extract"].ainvoke({"archive_path": "inbound/not.zip"}))
    assert "readable ZIP" in result["error"]
    result = json.loads(
        await tools["archive_extract"].ainvoke({"archive_path": "../inbound/not.zip"})
    )
    assert "unsafe path" in result["error"]
    assert not (ws.workspace_dir.parent / "outside.txt").exists()


def test_red_path_traversal_validation_prevents_escape(tmp_path: Path) -> None:
    """The accepted-path invariant fails if the traversal validator is removed."""
    malicious = _zip([("../../escape.txt", b"escaped", None)])
    with pytest.raises(ValueError, match="unsafe path"):
        _validate_archive(malicious)
    # A naive extraction (the RED baseline) writes outside the chosen root.
    root = tmp_path / "red-baseline"
    root.mkdir()
    with zipfile.ZipFile(BytesIO(malicious)) as archive:
        info = archive.infolist()[0]
        naive_target = (root / info.filename).resolve()
        naive_target.parent.mkdir(parents=True, exist_ok=True)
        naive_target.write_bytes(archive.read(info))
    assert naive_target.is_file()
    assert root.resolve() not in naive_target.parents


def test_red_zip_bomb_validation_prevents_expansion(tmp_path: Path) -> None:
    """The size-limit assertion goes red when validation is omitted."""
    oversized = _zip([("bomb.bin", b"z" * (MAX_ARCHIVE_FILE_BYTES + 1), None)])
    with pytest.raises(ValueError, match="per-file limit"):
        _validate_archive(oversized)
    # The vulnerable baseline accepts the same archive and would materialize it.
    with zipfile.ZipFile(BytesIO(oversized)) as archive:
        info = archive.infolist()[0]
        assert info.file_size == MAX_ARCHIVE_FILE_BYTES + 1
        assert len(archive.read(info)) > MAX_ARCHIVE_FILE_BYTES
