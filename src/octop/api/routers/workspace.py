"""Workspace router — read/write into a running agent's workspace."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from harness_agent.backends.utils import BackendOperationNotSupportedError
from pydantic import BaseModel

from octop.api.common.agent import AgentCapability, user_owns_agent
from octop.api.common.agent_workspace import resolve_agent_workspace_dir
from octop.api.common.content_disposition import content_disposition
from octop.api.common.memory_client import feature_memory_reason
from octop.api.common.workspace import (
    coerce_read_content,
    file_info_to_dict,
    reanchor_entry_path,
    require_running_workspace,
    workspace_api_path,
)
from octop.api.deps import current_user, get_server
from octop.infra.agents.feature_workflow import STATUS_ACTIVE, feature_memory_stage
from octop.infra.agents.kinds import is_feature_agent
from octop.infra.agents.memory_backend import open_memory_kwargs
from octop.infra.agents.workspace_dir import (
    LEGACY_AUTH_DIRNAME,
    LEGACY_SYSTEM_FILES_DIRNAME,
    MIGRATED_AUTH_DIRNAME,
    SYSTEM_FILES_DIRNAME,
    agent_facing_workspace_dir_from_config,
)
from octop.infra.backup.workspace_archive import export_workspace_zip, import_workspace_zip
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.media.backend_files import (
    backend_workspace_path,
    is_host_absolute_path,
    resolve_preview_payload,
)
from octop.infra.utils.doc_edit import DocConverter, get_doc_converter

logger = logging.getLogger(__name__)

_PROTECTED_PREFIX = "_builtin_skills"
_SHARED_MEMORY_FILES = frozenset({"memory.md"})

# Both layouts are named: the current one is what this build writes, and the old
# one still exists in an unmigrated workspace (the migration is on first touch,
# not a boot-time sweep).
_SYSTEM_DIR_NAMES = frozenset({SYSTEM_FILES_DIRNAME, LEGACY_SYSTEM_FILES_DIRNAME})
_SHARED_SQLITE_FILES = frozenset(
    {
        "memory.sqlite",
        f"{SYSTEM_FILES_DIRNAME}/memory.sqlite",
        f"{LEGACY_SYSTEM_FILES_DIRNAME}/memory.sqlite",
    }
)

# Read-side system paths stay owner/admin-only even on a shared agent. The
# harness stores the logical names (``memory.sqlite``, ``sessions/…``) under the
# system directory via ``BackendWorkspace``, and legacy workspaces keep some of
# them at the workspace root, so both forms are rejected. Matching is
# workspace-relative and case-insensitive.
_OWNER_ONLY_PREFIXES = frozenset(
    {
        SYSTEM_FILES_DIRNAME,
        LEGACY_SYSTEM_FILES_DIRNAME,
        MIGRATED_AUTH_DIRNAME,
        LEGACY_AUTH_DIRNAME,
    }
)
_OWNER_ONLY_DIRS = frozenset({"sessions"})
_OWNER_ONLY_FILES = frozenset({".env"})
_OWNER_ONLY_SQLITE_STEMS = ("memory.sqlite", "checkpoints.sqlite")


async def _protect_published_memory(
    workspace: Any,
    agent_id: str,
    server: Any,
    *paths: str,
    user: Any,
    replace_archive: bool = False,
    destructive: bool = False,
) -> None:
    """Block alternate workspace write routes into published shared memory."""
    row = server.services.agent_repo.get(agent_id)
    if row is None or not is_feature_agent(row.kind):
        return
    if await feature_memory_stage(workspace) != STATUS_ACTIVE:
        return
    if replace_archive:
        # Replacement erases existing shared files even if omitted from the zip.
        reason = feature_memory_reason("shared_read_only", user=user, server=server)
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    root = workspace.workspace_dir.resolve()
    cfg: dict[str, Any] = {}
    if row.config_json:
        try:
            parsed = json.loads(row.config_json)
            if isinstance(parsed, dict):
                cfg = parsed
        except json.JSONDecodeError:
            pass
    _namespace, backend, backend_cfg = open_memory_kwargs(
        agent_id=agent_id,
        cfg=cfg,
        octop_config=server.services.config,
        workspace_dir=root,
    )
    sqlite_files = set(_SHARED_SQLITE_FILES)
    if backend == "sqlite" and backend_cfg:
        db_path = Path(backend_cfg["db_path"]).resolve()
        if db_path.is_relative_to(root):
            sqlite_files.add(db_path.relative_to(root).as_posix().casefold())
    for path in paths:
        candidate = Path(path)
        relative = (
            (candidate if is_host_absolute_path(path) else root / path).resolve().relative_to(root)
        )
        target = relative.as_posix().casefold()
        if (
            target in _SHARED_MEMORY_FILES
            or any(target == db or target.startswith(f"{db}-") for db in sqlite_files)
            or target == "daily"
            or target.startswith("daily/")
            or destructive
            and target.split("/", 1)[0] in _SYSTEM_DIR_NAMES
        ):
            reason = feature_memory_reason("shared_read_only", user=user, server=server)
            raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})


def _assert_workspace_mutable(path: str, *, workspace_dir: Path) -> str:
    """Resolve a mutable path and reject root/protected entries."""
    io_path = _workspace_io_path(path, from_workspace=True, workspace_dir=workspace_dir)
    rel = io_path
    if is_host_absolute_path(io_path):
        rel = Path(io_path).resolve().relative_to(workspace_dir.resolve()).as_posix()
    if rel == ".":
        raise OctopError(ErrorCode.FORBIDDEN, "cannot modify workspace root")
    posix = rel.replace("\\", "/").strip("/")
    protected = (
        _PROTECTED_PREFIX,
        *(f"{name}/{_PROTECTED_PREFIX}" for name in sorted(_SYSTEM_DIR_NAMES)),
    )
    if any(posix == prefix or posix.startswith(f"{prefix}/") for prefix in protected):
        raise OctopError(ErrorCode.FORBIDDEN, f"cannot modify {_PROTECTED_PREFIX!r} paths")
    return io_path


def _workspace_relative_path(path: str, *, workspace_dir: Path) -> str:
    """Return *path* as a case-folded workspace-relative POSIX path."""
    root = workspace_dir.resolve()
    candidate = Path(path)
    if not is_host_absolute_path(path):
        candidate = root / path
    try:
        return candidate.resolve().relative_to(root).as_posix().casefold()
    except (OSError, ValueError):
        raise OctopError(
            ErrorCode.FORBIDDEN,
            f"path is outside agent workspace: {path!r}",
        ) from None


def _is_owner_only_workspace_path(rel: str) -> bool:
    # Windows ignores trailing dots/spaces and resolves NTFS stream suffixes
    # (``memory.sqlite.`` / ``memory.sqlite::$DATA`` both open the file), so
    # normalize the first segment before the reserved-name comparison.
    first = rel.split("/", 1)[0].split(":", 1)[0].rstrip(" .")
    if first in _OWNER_ONLY_PREFIXES or first in _OWNER_ONLY_DIRS or first in _OWNER_ONLY_FILES:
        return True
    return any(first == stem or first.startswith(f"{stem}-") for stem in _OWNER_ONLY_SQLITE_STEMS)


def _assert_workspace_readable(
    path: str,
    *,
    agent_id: str,
    user: Any,
    server: Any,
    workspace_dir: Path,
) -> None:
    """Reject non-owner reads of workspace system files.

    A shared agent stays readable for ordinary files (``inbound/``,
    ``outbound/``, user documents), but its memory databases, session logs,
    checkpoints, and credentials belong to the owner (or an admin).
    *path* is the value returned by :func:`_workspace_io_path`.
    """
    if not _is_owner_only_workspace_path(
        _workspace_relative_path(path, workspace_dir=workspace_dir)
    ):
        return
    if user.is_admin:
        return
    row = server.services.agent_repo.get(agent_id)
    if row is not None and user_owns_agent(row, user):
        return
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"workspace system path is owner-only: {path!r}",
    )


def _map_workspace_fs_error(exc: Exception, *, operation: str, path: str) -> OctopError:
    if isinstance(exc, BackendOperationNotSupportedError):
        return OctopError(ErrorCode.WORKSPACE_OP_UNSUPPORTED, str(exc))
    if isinstance(exc, FileNotFoundError):
        return OctopError(ErrorCode.NOT_FOUND, f"cannot {operation} {path!r}: not found")
    if isinstance(exc, FileExistsError):
        return OctopError(ErrorCode.SLASH_BAD_ARGS, str(exc))
    if isinstance(exc, PermissionError):
        return OctopError(ErrorCode.FORBIDDEN, str(exc))
    if isinstance(exc, ValueError):
        return OctopError(ErrorCode.SLASH_BAD_ARGS, str(exc))
    return OctopError(ErrorCode.INTERNAL_ERROR, f"cannot {operation} {path!r}: {exc}")


def _ensure_editable_doc(path: str) -> DocConverter:
    """Return the registered converter for *path* or reject with a 400."""
    converter = get_doc_converter(path)
    if converter is None:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"unsupported editable document {path!r}",
        )
    return converter


def _workspace_io_path(
    path: str,
    *,
    from_workspace: bool = False,
    workspace_dir: Path | None = None,
) -> str:
    """Resolve an API path and, when supplied, constrain it to this workspace."""
    raw = path.strip()
    if raw.startswith("~"):
        raise OctopError(ErrorCode.FORBIDDEN, "home-relative paths are not allowed")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise OctopError(ErrorCode.FORBIDDEN, "network paths are not allowed")
    if raw.startswith("file://"):
        try:
            io_path = backend_workspace_path(raw)
        except ValueError:
            raise OctopError(ErrorCode.FORBIDDEN, "remote file URLs are not allowed") from None
        if io_path is None:
            raise OctopError(ErrorCode.FORBIDDEN, "remote file URLs are not allowed")
    elif is_host_absolute_path(raw) and not (raw.startswith("/") and from_workspace):
        io_path = raw
    else:
        io_path = workspace_api_path(raw)
    if io_path.replace("\\", "/").startswith("//"):
        raise OctopError(ErrorCode.FORBIDDEN, "network paths are not allowed")

    if workspace_dir is not None:
        root = workspace_dir.resolve()
        candidate = Path(io_path)
        if not is_host_absolute_path(io_path):
            candidate = root / io_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                f"path is outside agent workspace: {path!r}",
            ) from None
        if ".harness-browser" in str(resolved).lower():
            raise OctopError(ErrorCode.FORBIDDEN, "browser profile paths are not allowed")
    return io_path


def _agent_facing_virtual_workspace_rel(path: str, *, server: Any, agent_id: str) -> str | None:
    """Map only this agent's configured virtual workspace root to a relative key."""
    if path.strip().lower().startswith("file://"):
        return None
    registry = getattr(getattr(server, "app_runtime", None), "agent_registry", None)
    get_config = getattr(registry, "get_config", None)
    if not callable(get_config):
        return None
    cfg = get_config(agent_id)
    facing_root = agent_facing_workspace_dir_from_config(cfg).replace("\\", "/").rstrip("/")
    root_parts = facing_root.split("/")
    if (
        len(root_parts) != 4
        or root_parts[0] != ""
        or root_parts[1] not in {SYSTEM_FILES_DIRNAME, LEGACY_SYSTEM_FILES_DIRNAME}
        or root_parts[2] != "workspaces"
        or not root_parts[3]
    ):
        return None
    raw = path.strip().replace("\\", "/")
    if raw == facing_root:
        return "."
    if raw.startswith(f"{facing_root}/"):
        return raw[len(facing_root) + 1 :]
    return None


_FROM_WORKSPACE_DESC = (
    "When true, leading '/' paths are workspace-relative (workspace UI). "
    "When false (default), leading '/' is host-absolute but must resolve inside this agent workspace."
)


router = APIRouter()


@router.get("/agents/{agent_id}/workspace/tree")
async def list_tree(
    agent_id: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    """Single-level directory listing under ``path`` (agent must be running)."""
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    io_path = _workspace_io_path(
        path,
        from_workspace=from_workspace or path.strip() == "/",
        workspace_dir=ws.workspace_dir,
    )
    result = await ws.als(io_path)
    if result is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot list {path!r}")
    entries = getattr(result, "entries", None) or []
    rows = [file_info_to_dict(f) for f in entries]
    if not is_host_absolute_path(io_path):
        for row in rows:
            row["path"] = reanchor_entry_path(str(row.get("path") or ""), parent=io_path)
    return rows


class WriteFileBody(BaseModel):
    content: str
    """UTF-8 text content. Use ``/upload`` for binary."""


class WriteDocBody(BaseModel):
    content: str
    """Markdown content to convert back into the document format."""


@router.get("/agents/{agent_id}/workspace/file")
async def read_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Read a UTF-8 text file."""
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    io_path = _workspace_io_path(
        path, from_workspace=from_workspace, workspace_dir=ws.workspace_dir
    )
    _assert_workspace_readable(
        io_path,
        agent_id=agent_id,
        user=user,
        server=server,
        workspace_dir=ws.workspace_dir,
    )
    content = await ws.aread_text(io_path)
    if content is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}")
    return {"path": path, "content": coerce_read_content(content)}


@router.put("/agents/{agent_id}/workspace/file")
async def write_file(
    agent_id: str,
    body: WriteFileBody,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Overwrite ``path`` with ``body.content`` (text)."""
    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    io_path = _workspace_io_path(
        path, from_workspace=from_workspace, workspace_dir=ws.workspace_dir
    )
    await _protect_published_memory(ws, agent_id, server, io_path, user=user)
    converter = get_doc_converter(path)
    if converter is not None:
        try:
            data = converter.from_markdown(body.content)
        except Exception as exc:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                f"cannot build document for {path!r}: {exc}",
            ) from exc
    else:
        data = body.content.encode("utf-8")
    try:
        await ws.aupload_bytes(io_path, data)
    except Exception as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot write {path!r}: {exc}") from exc
    return {"path": path, "size": len(data)}


class MoveFileBody(BaseModel):
    destination: str
    """Workspace-relative destination path (e.g. ``/sub/file.txt``)."""


@router.post(
    "/agents/{agent_id}/workspace/mkdir",
    status_code=201,
    summary="Create workspace directory",
)
async def mkdir_workspace_dir(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create a directory (and parents) under the agent workspace."""
    _ = from_workspace  # API surface; mutations always use workspace-relative paths.
    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    rel = _assert_workspace_mutable(path, workspace_dir=ws.workspace_dir)
    try:
        await ws.amkdir(rel)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="mkdir", path=path) from exc
    api_path = path if path.startswith("/") else f"/{path}"
    return {"path": api_path, "is_dir": True}


@router.delete(
    "/agents/{agent_id}/workspace/file",
    status_code=204,
    summary="Delete workspace file or directory",
)
async def delete_workspace_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> Response:
    """Remove a file or directory tree from the agent workspace."""
    _ = from_workspace
    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    rel = _assert_workspace_mutable(path, workspace_dir=ws.workspace_dir)
    await _protect_published_memory(ws, agent_id, server, rel, user=user, destructive=True)
    try:
        await ws.adelete(rel)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="delete", path=path) from exc
    return Response(status_code=204)


@router.post(
    "/agents/{agent_id}/workspace/move",
    summary="Move or rename a workspace file or directory",
)
async def move_workspace_file(
    agent_id: str,
    body: MoveFileBody,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Move ``path`` to ``body.destination`` (rename when the parent directory is unchanged)."""
    _ = from_workspace
    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    src = _assert_workspace_mutable(path, workspace_dir=ws.workspace_dir)
    dest = _assert_workspace_mutable(body.destination, workspace_dir=ws.workspace_dir)
    await _protect_published_memory(ws, agent_id, server, src, dest, user=user, destructive=True)
    try:
        await ws.amove(src, dest)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="move", path=path) from exc
    dest_api = body.destination if body.destination.startswith("/") else f"/{body.destination}"
    return {"path": dest_api}


@router.post("/agents/{agent_id}/workspace/upload")
async def upload_file(
    agent_id: str,
    file: UploadFile = File(...),  # noqa: B008
    path: str | None = Query(default=None),
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Upload a binary file via multipart ``file=@...``."""
    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    target = path or f"/{file.filename or 'upload.bin'}"
    io_path = _workspace_io_path(
        target,
        from_workspace=from_workspace or path is None,
        workspace_dir=ws.workspace_dir,
    )
    await _protect_published_memory(ws, agent_id, server, io_path, user=user)
    data = await file.read()
    try:
        await ws.aupload_bytes(io_path, data)
    except Exception as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot upload to {target!r}: {exc}") from exc
    return {"path": target, "size": len(data)}


@router.get(
    "/agents/{agent_id}/workspace/download",
    summary="Download a workspace file",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Binary workspace file download.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def download_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Download within this workspace, including its configured agent-facing virtual root.

    Recognizes only the current agent's configured ``/.maitu/workspaces/<id>``
    (or legacy ``/.octop/workspaces/<id>``) prefix. Explicit ``file://`` paths keep
    host-path semantics. All paths retain workspace confinement and system-file checks.
    """
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    virtual_rel = _agent_facing_virtual_workspace_rel(path, server=server, agent_id=agent_id)
    download_path = virtual_rel if virtual_rel is not None else path
    io_path = _workspace_io_path(
        download_path, from_workspace=from_workspace, workspace_dir=ws.workspace_dir
    )
    _assert_workspace_readable(
        io_path,
        agent_id=agent_id,
        user=user,
        server=server,
        workspace_dir=ws.workspace_dir,
    )
    try:
        file_blob = await ws.adownload_bytes(io_path)
    except PermissionError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot download {path!r}") from exc
    if file_blob is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot download {path!r}") from None
    fname = io_path.rsplit("/", 1)[-1] or "download.bin"
    return StreamingResponse(
        iter([file_blob]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition(fname)},
    )


@router.get("/agents/{agent_id}/workspace/doc")
async def read_doc(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Read an editable document as Markdown for online editing."""
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    io_path = _workspace_io_path(
        path, from_workspace=from_workspace, workspace_dir=ws.workspace_dir
    )
    _assert_workspace_readable(
        io_path,
        agent_id=agent_id,
        user=user,
        server=server,
        workspace_dir=ws.workspace_dir,
    )
    converter = _ensure_editable_doc(path)
    try:
        blob = await ws.adownload_bytes(io_path)
    except PermissionError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}") from exc
    if blob is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}")
    try:
        content = converter.to_markdown(blob)
    except Exception as exc:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"cannot parse {path!r}: {exc}") from exc
    return {"path": path, "content": content}


@router.put("/agents/{agent_id}/workspace/doc")
async def write_doc(
    agent_id: str,
    body: WriteDocBody,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Convert Markdown *content* back to the document format and overwrite *path*."""
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    rel = _assert_workspace_mutable(path, workspace_dir=ws.workspace_dir)
    converter = _ensure_editable_doc(path)
    try:
        data = converter.from_markdown(body.content)
    except Exception as exc:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"cannot build document for {path!r}: {exc}",
        ) from exc
    try:
        await ws.aupload_bytes(rel, data)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="write", path=path) from exc
    return {"path": path, "size": len(data)}


@router.get(
    "/agents/{agent_id}/media/preview",
    summary="Preview image or video",
    response_class=StreamingResponse,
)
async def preview_media(
    agent_id: str,
    source: str = Query(..., description="``file://`` URL or workspace-relative path"),
    mime_type: str | None = Query(default=None, alias="mime_type"),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Stream an image or video inline for dashboard tool-result previews."""
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    io_path = _workspace_io_path(source, workspace_dir=ws.workspace_dir)
    _assert_workspace_readable(
        io_path,
        agent_id=agent_id,
        user=user,
        server=server,
        workspace_dir=ws.workspace_dir,
    )
    payload = await resolve_preview_payload(
        source=source,
        workspace=ws,
        mime_hint=mime_type or "",
    )
    if payload is None:
        raise OctopError(ErrorCode.NOT_FOUND, "preview not available for this source")
    data, mime = payload

    return StreamingResponse(
        iter([data]),
        media_type=mime,
        headers={"Content-Disposition": "inline"},
    )


@router.get("/agents/{agent_id}/workspace/glob")
async def glob_files(
    agent_id: str,
    pattern: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    normalized_pattern = pattern.replace("\\", "/")
    if (
        normalized_pattern.startswith("/")
        or (len(normalized_pattern) >= 2 and normalized_pattern[1] == ":")
        or normalized_pattern.startswith("~")
        or ".." in normalized_pattern.split("/")
    ):
        raise OctopError(ErrorCode.FORBIDDEN, "glob pattern must stay within the workspace")
    root = _workspace_io_path(
        path,
        from_workspace=from_workspace or path.strip() == "/",
        workspace_dir=ws.workspace_dir,
    )
    if pattern in ("**/*.md", "*.md") and root == ".":
        ls_result = await ws.als(".")
        if ls_result is None:
            raise OctopError(ErrorCode.NOT_FOUND, "glob failed")
        entries = getattr(ls_result, "entries", None) or []
        matches = []
        for f in entries:
            row = file_info_to_dict(f)
            if row.get("is_dir"):
                continue
            entry_path = str(row.get("path") or "")
            if entry_path.endswith(".md"):
                matches.append(row)
        return matches
    glob_result = await ws.aglob(pattern, root)
    if glob_result is None:
        raise OctopError(ErrorCode.NOT_FOUND, "glob failed")
    matches = getattr(glob_result, "matches", None) or []
    return [file_info_to_dict(item) for item in matches]


@router.get("/agents/{agent_id}/workspace/grep")
async def grep_files(
    agent_id: str,
    pattern: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    io_path = _workspace_io_path(
        path,
        from_workspace=from_workspace or path.strip() == "/",
        workspace_dir=ws.workspace_dir,
    )
    result = await ws.agrep(pattern, io_path)
    if result is None:
        raise OctopError(ErrorCode.NOT_FOUND, "grep failed")
    matches = getattr(result, "matches", None) or []
    return [dict(m) for m in matches]


_MAX_WORKSPACE_ARCHIVE_BYTES = 200 * 1024 * 1024


@router.get(
    "/agents/{agent_id}/workspace/archive",
    summary="Download workspace as zip",
    response_class=StreamingResponse,
)
async def export_workspace_archive(
    agent_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Pack workspace files into a zip archive."""
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    data = await export_workspace_zip(ws)
    filename = f"workspace-{agent_id}.zip"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post(
    "/agents/{agent_id}/workspace/archive",
    summary="Import workspace zip",
)
async def import_workspace_archive(
    agent_id: str,
    file: UploadFile = File(...),  # noqa: B008
    mode: Literal["merge", "replace"] = Query(default="merge"),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Import a zip archive into the workspace (merge or replace)."""
    raw = await file.read()
    if len(raw) > _MAX_WORKSPACE_ARCHIVE_BYTES:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "workspace archive too large (max 200MB)")
    if not raw:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "empty archive")

    ws = await require_running_workspace(
        agent_id,
        user=user,
        as_user=as_user,
        server=server,
        capability=AgentCapability.PERSONA_FILES,
    )
    if mode == "replace":
        await _protect_published_memory(ws, agent_id, server, user=user, replace_archive=True)
    else:
        try:
            with ZipFile(BytesIO(raw)) as archive:
                await _protect_published_memory(
                    ws, agent_id, server, *archive.namelist(), user=user
                )
        except BadZipFile:
            pass  # Keep the existing import validator's error for malformed archives.
    local_ws = resolve_agent_workspace_dir(server, agent_id)
    result = await import_workspace_zip(
        ws,
        raw,
        mode=mode,
        local_workspace_dir=local_ws,
    )
    return {"ok": True, **result}
