"""Binary file I/O and dashboard media preview through ``agent.workspace``.

Dashboard / gateway code uses this module — **not**
:class:`~octop.infra.gateway.media.ingress.AgentBackedMediaBackend`,
which is only the harness-gateway ``MediaBackend`` adapter for IM ingress.

Path rule for ``BackendWorkspace``
----------------------------------
Workspace-relative ``outbound/`` / ``inbound/`` keys remain relative. Host-
absolute preview and media file paths are accepted only inside the selected
agent workspace; temporary and other agents' host paths are never fallback roots.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from octop.infra.gateway.media.attachment_hints import is_preview_media_type

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Host filesystem path guards
# ---------------------------------------------------------------------------

_BLOCKED_UNIX_PREFIXES = (
    "/users/",
    "/tmp/",
    "/home/",
    "/var/",
    "/private/",
    "/appdata/local/temp/",
    "/appdata/local/microsoft/windows/inetcache/",
)

_BLOCKED_WIN_DRIVE_PREFIXES = (
    "c:/users/",
    "c:/windows/temp/",
    "c:/program files/",
    "c:/program files (x86)/",
)


def _normalize_host_path(raw: str) -> str:
    """Lowercase path with forward slashes for prefix checks."""
    text = raw.strip().replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return text.lower()
    if text and not text.startswith("/"):
        text = "/" + text
    return text.lower()


def is_blocked_host_download_path(raw: str) -> bool:
    """True when *raw* looks like a host absolute path that must not be downloaded.

    Workspace-relative keys (``tmp/foo``, ``var/data.json``) must **not** be blocked —
    only paths that are already host-absolute (leading ``/``, ``file://``, or a drive
    letter on Windows).
    """
    text = raw.strip()
    if not text:
        return False

    lowered = text.replace("\\", "/").lower()
    if ".harness-browser" in lowered:
        return True

    if len(text) >= 2 and text[1] == ":":
        norm = _normalize_host_path(text)
        return norm.startswith(_BLOCKED_WIN_DRIVE_PREFIXES)

    if text.startswith("/"):
        norm = _normalize_host_path(text)
        return norm.startswith(_BLOCKED_UNIX_PREFIXES)

    return False


def file_url_to_abs_path(file_url: str) -> str:
    parsed = urllib.parse.urlparse(file_url)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        raise ValueError("remote file URLs are not supported")
    path = parsed.path
    # file:///C:/… — Windows drive in URL path
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        return str(Path(urllib.parse.unquote(path[1:])))
    # Unix absolute (file:///Users/…) — keep forward slashes; url2pathname
    # would produce \Users\… on Windows and break cross-platform semantics.
    if path.startswith("/"):
        return urllib.parse.unquote(path)
    return str(Path(urllib.parse.unquote(urllib.request.url2pathname(path))))


def extract_workspace_rel(path: str) -> str | None:
    """Return ``outbound/…`` or ``inbound/…`` when present in *path* (any common shape)."""
    raw = path.strip()
    fs_path = file_url_to_abs_path(raw) if raw.startswith("file://") else raw.lstrip("/")
    normalized = fs_path.replace("\\", "/")
    if normalized.startswith(("outbound/", "inbound/")):
        return normalized
    for marker in ("/outbound/", "/inbound/"):
        if marker in normalized:
            return normalized[normalized.index(marker) + 1 :]
    return None


def normalize_workspace_media_path(path: str) -> str:
    """Return ``outbound/…`` or ``inbound/…`` for tool/browser media URLs."""
    rel = extract_workspace_rel(path)
    if rel:
        return rel
    raise ValueError(f"not a workspace media path: {path.strip()!r}")


def normalize_workspace_download_path(path: str) -> str:
    """Backend-relative path for workspace download; rejects host absolute paths."""
    raw = path.strip()
    if not raw:
        raise ValueError("empty path")
    rel = extract_workspace_rel(raw)
    if rel:
        return rel
    if raw.startswith("file://"):
        raise ValueError(f"not a workspace file URL: {raw!r}")
    if is_blocked_host_download_path(raw):
        raise ValueError(f"host path not allowed: {raw!r}")
    return raw.lstrip("/")


def workspace_download_url(agent_id: str, workspace_path: str) -> str:
    """Build a URL for a workspace key or a caller-validated own-workspace path.

    Relative workspace keys are passed without a leading slash. Absolute paths
    remain absolute so the authenticated workspace route can enforce containment.
    """
    raw = workspace_path.strip()
    if (
        raw.startswith("file://")
        or raw.startswith("/")
        or (len(raw) >= 2 and raw[1] == ":")
        or raw.startswith("\\\\")
    ):
        path_param = raw
    else:
        rel = extract_workspace_rel(raw) or raw.lstrip("/")
        path_param = rel
    return (
        f"/api/agents/{agent_id}/workspace/download?path={urllib.parse.quote(path_param, safe='')}"
    )


def media_preview_url(agent_id: str, source: str, mime_hint: str = "") -> str:
    params: dict[str, str] = {"source": source}
    if mime_hint:
        params["mime_type"] = mime_hint
    return f"/api/agents/{agent_id}/media/preview?{urllib.parse.urlencode(params)}"


def backend_workspace_path(source: str) -> str | None:
    """Return a path only for local file URLs and workspace-relative candidates.

    Host-absolute values remain unchanged for callers that also enforce
    workspace containment. Remote file URLs and home/network paths are rejected.
    """
    raw = (source or "").strip()
    if not raw or raw.startswith("~") or raw.startswith("\\\\") or raw.startswith("//"):
        return None
    if raw.startswith("file://"):
        try:
            return file_url_to_abs_path(raw)
        except ValueError:
            return None
    return raw


def dashboard_media_url(agent_id: str, raw_url: str, mime: str = "") -> str | None:
    """Sync dashboard URL — preserve absolute tool paths as ``file://`` preview sources."""
    raw = raw_url.strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        return media_preview_url(agent_id, raw, mime)
    if raw.startswith("/"):
        return media_preview_url(agent_id, f"file://{raw}", mime)
    return media_preview_url(agent_id, raw, mime)


async def resolve_dashboard_media_url(
    workspace: BackendWorkspace,
    agent_id: str,
    raw_url: str,
    *,
    filename: str = "",
    mime: str = "",
) -> str | None:
    """Preview only an existing file in this agent's workspace."""
    rel = await ensure_workspace_media_path(workspace, raw_url, filename=filename, mime=mime)
    return media_preview_url(agent_id, rel, mime) if rel is not None else None


def _guess_mime(path: str, hint: str = "") -> str:
    if hint:
        return hint.split(";", 1)[0].strip().lower()
    guessed, _ = mimetypes.guess_type(path)
    return (guessed or "application/octet-stream").lower()


def is_previewable_mime(mime: str) -> bool:
    return is_preview_media_type(mime)


def _abs_path_allowed(abs_path: str, *, workspace: Path) -> bool:
    """Accept only local absolute paths resolving inside this agent workspace."""
    normalized = abs_path.replace("\\", "/")
    if normalized.startswith("//"):
        return False
    try:
        resolved = Path(abs_path).resolve()
        resolved.relative_to(workspace.resolve())
    except (OSError, ValueError):
        return False
    return ".harness-browser" not in str(resolved).lower()


def is_allowed_host_download_abs_path(path: str, *, workspace: Path) -> bool:
    """True only for a local host-absolute path resolving inside this workspace."""
    raw = path.strip()
    if not raw:
        return False
    if raw.startswith("file://"):
        try:
            raw = file_url_to_abs_path(raw)
        except ValueError:
            return False
    if not is_host_absolute_path(raw) or raw.replace("\\", "/").startswith("//"):
        return False
    return _abs_path_allowed(raw, workspace=workspace)


def is_host_absolute_path(path: str) -> bool:
    """True for host filesystem absolute paths (``/…``, ``file://``, drive letter).

    With ``from_workspace=false``, API leading ``/`` means host-absolute. Workspace
    keys must be passed without a leading slash (``outbound/…``) or with
    ``from_workspace=true``.
    """
    raw = path.strip().replace("\\", "/")
    if raw.startswith("file://"):
        return True
    if len(raw) >= 2 and raw[1] == ":":
        return True
    if raw.startswith("\\\\"):
        return True
    return raw.startswith("/")


def _is_host_absolute(path: str) -> bool:
    return is_host_absolute_path(path)


async def _download_via_workspace(workspace: BackendWorkspace, path: str) -> bytes | None:
    """``adownload_bytes`` that treats path-escape PermissionError as a miss."""
    try:
        return await workspace.adownload_bytes(path)
    except PermissionError:
        return None


async def _read_host_file_bytes(abs_path: str) -> bytes | None:
    """Best-effort host read when BackendWorkspace cannot open a Windows abs path."""
    try:
        return await asyncio.to_thread(Path(abs_path).read_bytes)
    except OSError as exc:
        logger.warning("host file read failed for %s: %s", abs_path, exc)
        return None


async def resolve_preview_payload(
    *,
    source: str,
    workspace: BackendWorkspace,
    mime_hint: str = "",
) -> tuple[bytes, str] | None:
    """Return preview bytes only for paths contained by this workspace."""
    path = backend_workspace_path(source)
    if path is None:
        return None

    root = workspace.workspace_dir.resolve()
    if _is_host_absolute(path):
        if not _abs_path_allowed(path, workspace=workspace.workspace_dir):
            return None
        resolved = Path(path).resolve()
        rel = resolved.relative_to(root).as_posix()
        data = await _download_via_workspace(workspace, rel)
        if data is None:
            data = await _read_host_file_bytes(path)
        used = path
    else:
        try:
            (root / path).resolve().relative_to(root)
        except (OSError, ValueError):
            return None
        read_path = extract_workspace_rel(path) or path
        data = await _download_via_workspace(workspace, read_path)
        used = read_path

    if data is None:
        return None
    mime = _guess_mime(used, mime_hint)
    if not is_previewable_mime(mime):
        return None
    return data, mime


async def read_file_url_bytes(
    workspace: BackendWorkspace,
    file_url: str,
    *,
    filename: str = "",
    mime: str = "",
) -> bytes | None:
    """Read workspace-contained file URLs; reject host paths outside this agent."""
    path = backend_workspace_path(file_url)
    if path is None:
        return None
    if _is_host_absolute(path):
        if not _abs_path_allowed(path, workspace=workspace.workspace_dir):
            return None
    else:
        try:
            (workspace.workspace_dir / path).resolve().relative_to(
                workspace.workspace_dir.resolve()
            )
        except (OSError, ValueError):
            return None

    rel = extract_workspace_rel(file_url)
    if rel:
        data = await _download_via_workspace(workspace, rel)
        if data is not None:
            return data
    data = await _download_via_workspace(workspace, path)
    if data is not None:
        return data
    if _is_host_absolute(path):
        return await _read_host_file_bytes(path)
    return None


async def ensure_workspace_media_path(
    workspace: BackendWorkspace,
    file_url: str,
    *,
    filename: str = "",
    mime: str = "",
) -> str | None:
    """Return the workspace-relative key for an existing contained media file."""
    path = backend_workspace_path(file_url)
    if path is None:
        return None
    if _is_host_absolute(path):
        if not _abs_path_allowed(path, workspace=workspace.workspace_dir):
            return None
        try:
            rel = Path(path).resolve().relative_to(workspace.workspace_dir.resolve()).as_posix()
        except (OSError, ValueError):
            return None
    else:
        try:
            rel = (
                (workspace.workspace_dir / path)
                .resolve()
                .relative_to(workspace.workspace_dir.resolve())
                .as_posix()
            )
        except (OSError, ValueError):
            return None

    existing = extract_workspace_rel(file_url)
    if existing:
        data = await _download_via_workspace(workspace, existing)
        if data is not None:
            return existing
    data = await _download_via_workspace(workspace, rel)
    return rel if data is not None else None


__all__ = [
    "backend_workspace_path",
    "dashboard_media_url",
    "ensure_workspace_media_path",
    "extract_workspace_rel",
    "file_url_to_abs_path",
    "is_allowed_host_download_abs_path",
    "is_blocked_host_download_path",
    "is_host_absolute_path",
    "is_previewable_mime",
    "media_preview_url",
    "normalize_workspace_download_path",
    "normalize_workspace_media_path",
    "read_file_url_bytes",
    "resolve_dashboard_media_url",
    "resolve_preview_payload",
    "workspace_download_url",
]
