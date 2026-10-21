"""Manual USER/MEMORY context documents for an agent.

Two storages, one editing contract:

* **Workspace documents** (experts and a feature's shared corpus) live at the
  workspace root as ``USER.md`` / ``MEMORY.md``. All content flows through the
  harness :class:`BackendWorkspace` adapter, so system-files mapping and
  virtual backends behave exactly as the agent runtime sees them. Saves use an
  explicit snapshot token and an exclusive cross-process lock around
  *read current → verify → replace*, so two dashboard writers can never
  interleave and a stale editor is always rejected.

* **Feature private documents** (an active-stage caller's own profile and
  memory notes) cannot be workspace files — one feature workspace serves every
  caller, so a per-user file there would leak across callers. They are stored
  as structured entries in the caller's own private memory namespace (works
  identically on SQLite and PostgreSQL) and injected per session by
  :mod:`octop.infra.agents.middleware.manual_memory`.

The generated clinical profile ``USER.md`` is script-owned: the editor reports
it as read-only and refuses writes.

Conflict review is intentionally explicit: callers must submit the reviewed
current snapshot and the proposed complete text. Stale edits are rejected and
must be reviewed against a fresh read before retrying.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

MANUAL_CONTEXT_FILES: tuple[str, ...] = ("USER.md", "MEMORY.md")
"""The documents the manual editor may address, canonical spellings."""

_CANONICAL_NAMES: dict[str, str] = {name.casefold(): name for name in MANUAL_CONTEXT_FILES}

PRIVATE_DOC_META_PREFIX = "octop_manual_doc:"
"""Meta key prefix for feature-private documents inside a memory namespace."""

_PRIVATE_DOC_SCHEMA = 1


class MemoryContextConflict(RuntimeError):
    """The document changed since the caller's snapshot was read."""


class ManagedMemoryContext(RuntimeError):
    """A script-managed context file cannot be edited manually."""


class UnknownMemoryDocument(ValueError):
    """The requested document name is not part of the manual editor surface."""


@dataclass(frozen=True)
class MemoryDocument:
    """One manual context document plus its CAS token."""

    name: str
    text: str
    snapshot: str
    editable: bool
    #: Monotonic per-document counter. ``None`` for workspace files (their
    #: snapshot token already covers content); private documents use it so a
    #: review candidate can prove which revision it was generated against.
    revision: int | None = None


def canonical_manual_document(name: str) -> str | None:
    """Canonical spelling of a manual document file name, or ``None``."""
    stripped = name.strip()
    if "/" in stripped or "\\" in stripped:
        return None
    return _CANONICAL_NAMES.get(stripped.casefold())


def _snapshot(name: str, text: str, revision: int = 0) -> str:
    payload = (
        f"{revision}".encode("ascii") + b"\0" + name.encode("utf-8") + b"\0" + text.encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Cross-process document lock (dashboard writers only; the model's file-tool
# path is guarded separately by the manual-memory middleware).
# ---------------------------------------------------------------------------

_LOCK_DIRNAME = "locks"
_LOCK_TOPIC = "memory-documents"


def document_lock_path(
    paths: Any,
    agent_id: str,
    *,
    scope: str,
    user_id: int | None,
    name: str,
) -> Path:
    """Host-side lock file for one document save/read-modify-write cycle.

    Lives in the server's own state directory — never inside an agent
    workspace, so the adapter surface stays clean for the model.
    """
    key = f"{agent_id}__{scope}__{int(user_id or 0)}__{name}".replace("/", "_")
    return Path(str(paths.root)) / _LOCK_DIRNAME / _LOCK_TOPIC / f"{key}.lock"


_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on *path* across processes and threads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path)
    with _PROCESS_LOCKS_GUARD:
        lock_thread = _PROCESS_LOCKS.setdefault(key, threading.Lock())
    with lock_thread:  # same-process mutual exclusion; OS lock covers cross-process
        handle = path.open("a+")
        try:
            _lock_handle(handle)
            yield
        finally:
            try:
                _unlock_handle(handle)
            finally:
                handle.close()


def _lock_handle(handle: Any) -> None:
    import sys

    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: Any) -> None:
    import sys

    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Workspace documents (experts and feature shared)
# ---------------------------------------------------------------------------


def _workspace_text(ws: Any, name: str) -> str:
    text = ws.read_text(name)
    if text is None and ws.exists(name):
        raise OSError(f"manual memory document could not be read: {name}")
    return text or ""


def managed_workspace_user_file(ws: Any) -> bool:
    """True when the workspace carries the clinical profile generator.

    Its ``USER.md`` is generated from structured state, so the generic editor
    must never claim it.
    """
    return bool(
        ws.exists("scripts/clinical_profile.py") or ws.exists("clinical_learning_state.json")
    )


def read_workspace_document(ws: Any, name: str) -> MemoryDocument:
    """Read a workspace document, returning an opaque content snapshot token."""
    canonical = canonical_manual_document(name)
    if canonical is None:
        raise UnknownMemoryDocument(f"only USER.md and MEMORY.md are supported, got {name!r}")
    text = _workspace_text(ws, canonical)
    managed = canonical == "USER.md" and managed_workspace_user_file(ws)
    return MemoryDocument(
        name=canonical,
        text=text,
        snapshot=_snapshot(canonical, text),
        editable=not managed,
    )


def save_workspace_document(
    ws: Any,
    name: str,
    text: str,
    *,
    expected_snapshot: str,
) -> MemoryDocument:
    """Atomically save workspace text iff the caller reviewed the latest snapshot.

    The whole *read → verify → replace* cycle runs under an exclusive
    cross-process lock, so concurrent dashboard saves serialize instead of
    racing between the CAS check and the write. No overwrite/merge mode
    exists: stale edits are rejected and must be reviewed against a fresh
    read before retrying.
    """
    canonical = canonical_manual_document(name)
    if canonical is None:
        raise UnknownMemoryDocument(f"only USER.md and MEMORY.md are supported, got {name!r}")
    if not isinstance(text, str):
        raise TypeError("memory document text must be a string")
    current = read_workspace_document(ws, canonical)
    if not current.editable:
        raise ManagedMemoryContext(f"{canonical} is generated by the clinical profile script")
    if current.snapshot != expected_snapshot:
        raise MemoryContextConflict(canonical)
    ws.write_text(canonical, text, force=True)
    saved = read_workspace_document(ws, canonical)
    if saved.snapshot != _snapshot(canonical, text):
        raise MemoryContextConflict(canonical)
    return saved


# ---------------------------------------------------------------------------
# Feature private documents (structured entries in the caller's namespace)
# ---------------------------------------------------------------------------


def _private_doc_key(name: str) -> str:
    canonical = canonical_manual_document(name)
    if canonical is None:
        raise UnknownMemoryDocument(f"only USER.md and MEMORY.md are supported, got {name!r}")
    return f"{PRIVATE_DOC_META_PREFIX}{canonical}"


def _decode_private_doc(name: str, raw: str | None) -> tuple[str, int]:
    if not raw:
        return "", 0
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("private manual document %s has unreadable meta; treating as empty", name)
        return "", 0
    if not isinstance(parsed, dict):
        return "", 0
    text = parsed.get("text")
    revision = parsed.get("revision")
    return (
        text if isinstance(text, str) else "",
        revision if isinstance(revision, int) and revision > 0 else 0,
    )


def read_private_document(memory: Any, name: str) -> MemoryDocument:
    """Read a caller's private document from their memory namespace."""
    canonical = canonical_manual_document(name)
    if canonical is None:
        raise UnknownMemoryDocument(f"only USER.md and MEMORY.md are supported, got {name!r}")
    try:
        raw = memory.get_meta(_private_doc_key(canonical))
    except Exception:
        logger.warning("private document read failed for %s", canonical, exc_info=True)
        raw = None
    text, revision = _decode_private_doc(canonical, raw)
    return MemoryDocument(
        name=canonical,
        text=text,
        snapshot=_snapshot(canonical, text, revision),
        editable=True,
        revision=revision,
    )


def save_private_document(
    memory: Any,
    name: str,
    text: str,
    *,
    expected_snapshot: str,
) -> MemoryDocument:
    """Save a caller's private document, bumping its revision on every write."""
    canonical = canonical_manual_document(name)
    if canonical is None:
        raise UnknownMemoryDocument(f"only USER.md and MEMORY.md are supported, got {name!r}")
    if not isinstance(text, str):
        raise TypeError("memory document text must be a string")
    current = read_private_document(memory, canonical)
    if current.snapshot != expected_snapshot:
        raise MemoryContextConflict(canonical)
    assert current.revision is not None
    revision = current.revision + 1
    payload = json.dumps(
        {"schema": _PRIVATE_DOC_SCHEMA, "revision": revision, "text": text},
        ensure_ascii=False,
    )
    memory.set_meta(_private_doc_key(canonical), payload)
    saved = read_private_document(memory, canonical)
    if saved.revision != revision:
        raise MemoryContextConflict(canonical)
    return saved


__all__ = [
    "MANUAL_CONTEXT_FILES",
    "ManagedMemoryContext",
    "MemoryContextConflict",
    "MemoryDocument",
    "UnknownMemoryDocument",
    "PRIVATE_DOC_META_PREFIX",
    "canonical_manual_document",
    "document_lock_path",
    "exclusive_file_lock",
    "managed_workspace_user_file",
    "read_private_document",
    "read_workspace_document",
    "save_private_document",
    "save_workspace_document",
]
