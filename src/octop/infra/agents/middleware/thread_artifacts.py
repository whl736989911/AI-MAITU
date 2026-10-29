"""Record workspace files produced by tools on their owning conversation thread."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlsplit

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
    file_url_to_abs_path,
    is_host_absolute_path,
)

logger = logging.getLogger(__name__)
ARTIFACT_TOOL_BASES = frozenset(
    {"write_file", "edit_file", "send_file", "send_file_to_user", "desktop_screenshot"}
)
_PATH_KEYS = ("path", "file_path", "filepath", "dest", "target_path", "output_path")
_PATH_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9._+-]{0,11}$")
_SEND_INTENT_RE = re.compile(
    r"发(?:给|到|我|送)|发送|传给我|打包|压缩包|下载|附件|给我|"
    r"send\s+(?:me|these|the|all)|attach|zip|share|email",
    re.IGNORECASE,
)
_CODE_INTENT_RE = re.compile(r"代码|脚本|源码|程序|source\s*code|script|\.py\b", re.IGNORECASE)
_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".sh",
        ".bat",
        ".ps1",
        ".cmd",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
    }
)
_NON_DELIVERABLE_SUFFIXES = _SOURCE_SUFFIXES | frozenset(
    {
        ".log",
        ".tmp",
        ".bak",
        ".swp",
        ".pyc",
        ".pyo",
        ".lock",
        ".cache",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)
_DELIVERABLE_SUFFIX_HINTS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"word|文档|docx?|\.docx?\b", re.I), frozenset({".doc", ".docx"})),
    (re.compile(r"excel|表格|xlsx?|\.xlsx?\b", re.I), frozenset({".xls", ".xlsx", ".csv"})),
    (re.compile(r"ppt|幻灯片|演示|pptx?|\.pptx?\b", re.I), frozenset({".ppt", ".pptx"})),
    (re.compile(r"pdf|\.pdf\b", re.I), frozenset({".pdf"})),
    (re.compile(r"markdown|\.md\b", re.I), frozenset({".md", ".markdown"})),
    (
        re.compile(r"图片|截图|照片|image|photo|\.png\b", re.I),
        frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}),
    ),
    (
        re.compile(r"压缩包|打包|zip|archive", re.I),
        frozenset({".zip", ".tar", ".gz", ".7z"}),
    ),
)
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
    # ``/outbound`` and ``/inbound`` are virtual workspace roots emitted by
    # media tools, not host filesystem paths. Match only these root prefixes.
    if raw.startswith(("/outbound/", "/inbound/")):
        raw = raw[1:]
    elif raw.startswith("file://"):
        try:
            raw = file_url_to_abs_path(raw).replace("\\", "/")
        except ValueError:
            return ""
    if is_host_absolute_path(raw):
        try:
            resolved = Path(raw).resolve()
            resolved.relative_to(workspace_dir.resolve())
        except (OSError, ValueError):
            return ""
        if not _artifact_path_allowed(str(resolved)):
            return ""
        return resolved.as_posix()
    rel = raw.removeprefix("/")
    if rel.startswith("workspace/"):
        rel = rel.removeprefix("workspace/")
    rel_path = Path(rel)
    if not rel or ".." in rel_path.parts or not _artifact_path_allowed(rel):
        return ""
    try:
        resolved = (workspace_dir / rel_path).resolve()
        resolved.relative_to(workspace_dir.resolve())
    except (OSError, ValueError):
        return ""
    if not _artifact_path_allowed(str(resolved)):
        return ""
    return resolved.as_posix()


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


def encode_team_artifact_ref(producer_agent_id: str, path: str) -> str:
    """Encode a trusted producer/path pair into the existing string artifact store."""
    producer = producer_agent_id.strip()
    raw_path = path.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", producer) or not raw_path:
        return ""
    return f"team-artifact://{producer}/{quote(raw_path, safe='')}"


def artifacts_for_response(
    paths: Sequence[str],
    workspace_dir: Path,
    *,
    producer_workspace: Callable[[str], Path | None] | None = None,
) -> list[str | dict[str, str]]:
    """Resolve stored artifacts, validating producer paths against that workspace."""
    out: list[str | dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in paths:
        if not isinstance(raw, str):
            continue
        if raw.startswith("team-artifact://"):
            parsed = urlsplit(raw)
            producer = parsed.netloc
            if not producer or producer_workspace is None:
                continue
            try:
                source_workspace = producer_workspace(producer)
            except Exception:
                source_workspace = None
            if source_workspace is None:
                continue
            candidate = unquote(parsed.path.lstrip("/"))
            path = _path(candidate, source_workspace)
            key = (producer, path)
            if path and key not in seen:
                seen.add(key)
                out.append({"producer_agent_id": producer, "path": path})
            continue
        path = _path(raw, workspace_dir)
        key = ("", path)
        if path and key not in seen:
            seen.add(key)
            out.append(path)
    return out


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
        # New and modified files are both candidates; whether they belong to the
        # user is decided from the conversation below, not from the change alone.
        changed.append((workspace_dir.expanduser().resolve() / rel).as_posix())
    return changed


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _message_role(message: Any) -> str:
    role = getattr(message, "type", None)
    if isinstance(role, str):
        return role
    if isinstance(message, Mapping):
        value = message.get("role") or message.get("type")
        return value if isinstance(value, str) else ""
    return ""


def _conversation_messages(state: Any) -> list[Any]:
    if isinstance(state, Mapping):
        messages = state.get("messages")
    else:
        messages = getattr(state, "messages", None)
    return list(messages) if isinstance(messages, (list, tuple)) else []


def _referenced_workspace_paths(
    texts: Sequence[str], candidates: Sequence[str], workspace_dir: Path
) -> list[str]:
    text = "\n".join(texts).replace("\\", "/").casefold()
    if not text:
        return []
    root = workspace_dir.expanduser().resolve()
    found: list[str] = []
    for raw in candidates:
        path = Path(raw)
        names = {path.name, path.as_posix()}
        with suppress(OSError, ValueError):
            names.add(path.resolve().relative_to(root).as_posix())
        if any(name and name.casefold() in text for name in names):
            found.append(path.as_posix())
    return list(dict.fromkeys(found))


def _requested_suffixes(text: str) -> frozenset[str]:
    suffixes: set[str] = set()
    for pattern, values in _DELIVERABLE_SUFFIX_HINTS:
        if pattern.search(text):
            suffixes.update(values)
    return frozenset(suffixes)


def _is_auto_deliverable(path: str, user_text: str) -> bool:
    """Keep source/scripts/runtime noise out of automatic deliverables."""
    suffix = Path(path).suffix.casefold()
    return not (suffix in _NON_DELIVERABLE_SUFFIXES and not _CODE_INTENT_RE.search(user_text))


def select_turn_artifacts(
    *,
    changed: Sequence[str],
    after: Mapping[str, tuple[int, int]],
    state: Any,
    workspace_dir: Path,
) -> list[str]:
    """Pick files the user asked for, not every file a script happened to touch.

    Two signals are intentional:
    * the final assistant answer naming files (the model's declared outputs);
    * an explicit user "send/attach/zip these files" request, which may refer to
      files produced on an earlier turn, so the whole workspace is searched.
    """
    messages = _conversation_messages(state)
    user_text = ""
    last_user_index = -1
    for index in range(len(messages) - 1, -1, -1):
        if _message_role(messages[index]) in {"human", "user"}:
            user_text = _message_text(messages[index])
            last_user_index = index
            break
    assistant_texts = [
        _message_text(message)
        for message in messages
        if _message_role(message) in {"ai", "assistant"} and _message_text(message)
    ]
    assistant_texts = assistant_texts[-5:]
    current_assistant_texts = [
        _message_text(message)
        for message in messages[last_user_index + 1 :]
        if _message_role(message) in {"ai", "assistant"} and _message_text(message)
    ]
    send_intent = bool(_SEND_INTENT_RE.search(user_text))
    if send_intent:
        all_paths = [(workspace_dir.expanduser().resolve() / rel).as_posix() for rel in after]
        referenced = _referenced_workspace_paths(
            [user_text, *assistant_texts], all_paths, workspace_dir
        )
        if referenced:
            return referenced
        # The user explicitly asked for files. Without a name to match, the
        # current turn's produced files are safe; otherwise use a file-type
        # hint such as "把刚才的 Word 文件发给我".
        if changed:
            return list(changed)
        suffixes = _requested_suffixes(user_text)
        if suffixes:
            return [path for path in all_paths if Path(path).suffix.casefold() in suffixes]
        return []
    auto_changed = [path for path in changed if _is_auto_deliverable(path, user_text)]
    referenced = _referenced_workspace_paths(
        [current_assistant_texts[-1]] if current_assistant_texts else [],
        auto_changed,
        workspace_dir,
    )
    return referenced


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
        del runtime
        self._record_workspace_changes(current_thread_id(), state)

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        del runtime
        tid = current_thread_id()
        if not tid:
            return
        with self._snapshot_lock:
            before = self._snapshots.pop(tid, None)
        if before is None:
            return
        after = await asyncio.to_thread(_workspace_snapshot, self._workspace_dir)
        changed = _changed_workspace_paths(before, after, self._workspace_dir)
        selected = select_turn_artifacts(
            changed=changed,
            after=after,
            state=state,
            workspace_dir=self._workspace_dir,
        )
        if selected:
            self._append(tid, selected)

    def _record_workspace_changes(self, tid: str, state: Any) -> None:
        if not tid:
            return
        with self._snapshot_lock:
            before = self._snapshots.pop(tid, None)
        if before is None:
            return
        after = _workspace_snapshot(self._workspace_dir)
        changed = _changed_workspace_paths(before, after, self._workspace_dir)
        selected = select_turn_artifacts(
            changed=changed,
            after=after,
            state=state,
            workspace_dir=self._workspace_dir,
        )
        if selected:
            self._append(tid, selected)

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
    "encode_team_artifact_ref",
    "extract_artifact_paths",
    "is_artifact_tool_name",
    "tool_name_base",
]
