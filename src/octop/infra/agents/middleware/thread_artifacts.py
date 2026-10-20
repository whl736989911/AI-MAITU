"""Record workspace files produced by tools on their owning conversation thread."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.conversation_mode import (
    parse_conversation_mode,
    plan_relpath_from_artifact,
)
from octop.infra.gateway.media.backend_files import (
    extract_workspace_rel,
    file_url_to_abs_path,
    is_host_absolute_path,
)

logger = logging.getLogger(__name__)
ARTIFACT_TOOL_BASES = frozenset(
    {"write_file", "edit_file", "send_file", "send_file_to_user", "desktop_screenshot"}
)
_PATH_KEYS = ("path", "file_path", "filepath", "dest", "target_path", "output_path")
_PATH_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9._+-]{0,11}$")
_MAX_SNAPSHOT_FILES = 5000
_SNAPSHOT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".octop",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "inbound",
    }
)


class ArtifactThreadStore(Protocol):
    def append_artifacts(self, thread_id: str, paths: Sequence[str]) -> None: ...
    def update_composer(
        self, thread_id: str, *, pending_plan_path: str | None | object = ...
    ) -> None: ...


def tool_name_base(name: str) -> str:
    return (name or "").strip().rsplit("/", 1)[-1]


def is_artifact_tool_name(name: str | None) -> bool:
    return tool_name_base(name or "").lower() in ARTIFACT_TOOL_BASES


def _path(value: object, workspace_dir: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip().replace("\\", "/")
    if raw.startswith("file://"):
        raw = file_url_to_abs_path(raw).replace("\\", "/")
    if is_host_absolute_path(raw):
        try:
            if os.path.commonpath((str(workspace_dir.resolve()), str(Path(raw).resolve()))) != str(
                workspace_dir.resolve()
            ):
                return ""
        except (OSError, ValueError):
            return ""
        if not _artifact_path_allowed(raw):
            return ""
        return raw
    rel = extract_workspace_rel(raw) or raw.lstrip("/")
    if rel.startswith("workspace/"):
        rel = rel.removeprefix("workspace/")
    if not rel or ".." in Path(rel).parts or not _artifact_path_allowed(rel):
        return ""
    return str((workspace_dir / rel).as_posix())


def current_thread_id() -> str:
    try:
        raw = (get_config().get("configurable") or {}).get("thread_id")
    except RuntimeError:
        return ""
    return raw.strip() if isinstance(raw, str) else ""


def extract_artifact_paths(
    *,
    tool_name: str = "",
    args: str | Mapping[str, Any] | None = None,
    result: Any = None,
    workspace_dir: Path | None = None,
) -> list[str]:
    if not is_artifact_tool_name(tool_name) or workspace_dir is None:
        return []
    parsed: Any = args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (ValueError, TypeError):
            parsed = {}
    found: list[str] = []
    if isinstance(parsed, Mapping):
        found.extend(_path(parsed.get(key), workspace_dir) for key in _PATH_KEYS)
    if not any(found) and isinstance(result, str):
        # Tool results may report a file URL or a workspace-relative artifact.
        for token in result.split():
            candidate = token.strip("`'\" ,;()")
            resolved = _path(candidate, workspace_dir)
            if resolved and Path(resolved).suffix:
                found.append(resolved)
    return list(dict.fromkeys(p for p in found if p))


def artifacts_for_response(paths: Sequence[str], workspace_dir: Path) -> list[str]:
    return list(dict.fromkeys(p for raw in paths if (p := _path(raw, workspace_dir))))


def _workspace_snapshot(workspace_dir: Path) -> dict[str, tuple[int, int]]:
    """Cheap stat snapshot used to catch files written by scripts/commands.

    The artifact-tool path extractor cannot see a ``python gen_docx.py`` call
    write ten output files. Comparing the workspace before and after the agent
    run gives every produced user file the same downloadable artifact treatment
    as an explicit ``write_file`` result, without trusting text in the answer.
    """
    root = workspace_dir.expanduser().resolve()
    if not root.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SNAPSHOT_SKIP_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            absolute = Path(dirpath) / filename
            try:
                rel = absolute.relative_to(root).as_posix()
            except ValueError:
                continue
            if not _artifact_path_allowed(rel):
                continue
            try:
                stat = absolute.stat()
            except OSError:
                continue
            snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
            if len(snapshot) >= _MAX_SNAPSHOT_FILES:
                return snapshot
    return snapshot


def _changed_workspace_paths(
    before: Mapping[str, tuple[int, int]],
    after: Mapping[str, tuple[int, int]],
    workspace_dir: Path,
) -> list[str]:
    changed: list[str] = []
    for rel, state in after.items():
        previous = before.get(rel)
        if previous == state:
            continue
        # New and modified files are both deliverables; unchanged files are
        # deliberately excluded so a turn that only reads does not create noise.
        changed.append((workspace_dir.expanduser().resolve() / rel).as_posix())
    return changed


class ThreadArtifactsMiddleware(AgentMiddleware[Any, Any]):
    """Persist successful artifact paths and Plan-mode pending plan path."""

    def __init__(self, *, thread_repo: ArtifactThreadStore, workspace_dir: Path) -> None:
        super().__init__()
        self._threads = thread_repo
        self._workspace_dir = workspace_dir.expanduser().resolve()
        self._snapshots: dict[str, dict[str, tuple[int, int]]] = {}
        self._snapshot_lock = threading.Lock()

    def before_agent(self, state: Any, runtime: Any) -> None:
        del state, runtime
        tid = current_thread_id()
        if not tid:
            return
        snapshot = _workspace_snapshot(self._workspace_dir)
        with self._snapshot_lock:
            self._snapshots[tid] = snapshot

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        del state, runtime
        tid = current_thread_id()
        if not tid:
            return
        snapshot = await asyncio.to_thread(_workspace_snapshot, self._workspace_dir)
        with self._snapshot_lock:
            self._snapshots[tid] = snapshot

    def after_agent(self, state: Any, runtime: Any) -> None:
        del state, runtime
        self._record_workspace_changes(current_thread_id())

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        del state, runtime
        tid = current_thread_id()
        if not tid:
            return
        with self._snapshot_lock:
            before = self._snapshots.pop(tid, None)
        if before is None:
            return
        after = await asyncio.to_thread(_workspace_snapshot, self._workspace_dir)
        changed = _changed_workspace_paths(before, after, self._workspace_dir)
        if changed:
            self._append(tid, changed)

    def _record_workspace_changes(self, tid: str) -> None:
        if not tid:
            return
        with self._snapshot_lock:
            before = self._snapshots.pop(tid, None)
        if before is None:
            return
        after = _workspace_snapshot(self._workspace_dir)
        changed = _changed_workspace_paths(before, after, self._workspace_dir)
        if changed:
            self._append(tid, changed)

    def _append(self, tid: str, paths: Sequence[str]) -> None:
        try:
            self._threads.append_artifacts(tid, list(paths))
        except Exception:
            logger.warning("Failed to persist thread artifacts for %s", tid, exc_info=True)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        self._record(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        self._record(request, result)
        return result

    def _record(self, request: ToolCallRequest, result: ToolMessage | Command[Any]) -> None:
        if (
            isinstance(result, Command)
            or not isinstance(result, ToolMessage)
            or getattr(result, "status", None) == "error"
        ):
            return
        call = request.tool_call
        name = str(call.get("name") or "")
        tid = current_thread_id()
        if not tid or not is_artifact_tool_name(name):
            return
        paths = extract_artifact_paths(
            tool_name=name,
            args=call.get("args") if isinstance(call.get("args"), (str, Mapping)) else None,
            result=result.content,
            workspace_dir=self._workspace_dir,
        )
        if not paths:
            return
        try:
            self._threads.append_artifacts(tid, paths)
            try:
                mode = parse_conversation_mode(
                    (get_config().get("configurable") or {}).get("conversation_mode")
                )
            except RuntimeError:
                mode = "craft"
            if mode == "plan":
                pending = next(
                    (rel for path in paths if (rel := plan_relpath_from_artifact(path))), ""
                )
                if pending:
                    self._threads.update_composer(tid, pending_plan_path=pending)
        except Exception:
            logger.warning("Failed to persist thread artifacts for %s", tid, exc_info=True)


def _artifact_path_allowed(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if "/_builtin_skills/" in f"/{normalized}/":
        return False
    name = normalized.rstrip("/").rsplit("/", 1)[-1]
    if re.fullmatch(r"[\d.]+", name):
        return False
    return bool(_PATH_EXT_RE.search(name))


__all__ = [
    "ARTIFACT_TOOL_BASES",
    "ThreadArtifactsMiddleware",
    "artifacts_for_response",
    "current_thread_id",
    "extract_artifact_paths",
    "is_artifact_tool_name",
    "tool_name_base",
]
