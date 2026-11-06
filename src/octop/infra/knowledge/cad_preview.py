"""Cached raster/PDF previews for CAD originals (DWG/DXF).

The platform does not render CAD itself: LibreDWG + ezdxf live in an external
converter script, and this module owns the contract around it — where the
converter is, where its output is cached, and when a cached preview is stale.

Three rules:

* **Optional, never fatal.** A host without the converter keeps the existing
  text preview and the original download; only the rendered preview is absent,
  and the caller learns that from :func:`converter` returning ``None``.
* **Cached by content.** The output file name carries the document's content
  hash, so an updated original produces a new preview instead of serving the
  old drawing under a new version.
* **Bounded.** Converter work uses the managed scratch area; only recognized,
  rebuildable PNG outputs are stored under knowledge-base ``previews`` folders.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from octop.infra.knowledge.files import knowledge_base_dir
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.tempfiles import directory_lock

CAD_PREVIEW_SUFFIXES = frozenset({".dwg", ".dxf"})
"""Formats the external converter understands."""

_ENV_SCRIPT = "OCTOP_DWG_PREVIEW_SCRIPT"
_ENV_PYTHON = "OCTOP_DWG_PREVIEW_PYTHON"
_DEFAULT_SCRIPT = "/opt/maitu/tools/dwg_preview.py"
_TIMEOUT_SECONDS = 300
_MAX_CACHE_BYTES = 1_073_741_824
_MAX_CACHE_FILES = 10_000
_MAX_CACHE_AGE_SECONDS = 7 * 24 * 60 * 60
_CACHE_NAME = re.compile(r"^[A-Za-z0-9_-]+-(?:[0-9a-f]{16}|[0-9a-f]{64}|current)\.png$")
_CACHE_STAGE_NAME = re.compile(r"^\.[A-Za-z0-9_-]+-[0-9a-f]{64}\.png\.[0-9a-f]{32}\.tmp$")


class CadPreviewUnavailable(RuntimeError):
    """No converter is configured on this host."""


class CadPreviewFailed(RuntimeError):
    """The converter ran but produced no usable image."""


def converter() -> tuple[str, str] | None:
    """``(python, script)`` for the external converter, or ``None`` if absent."""
    script = os.environ.get(_ENV_SCRIPT, _DEFAULT_SCRIPT).strip()
    if not script or not Path(script).is_file():
        return None
    python = os.environ.get(_ENV_PYTHON, "").strip() or sys.executable
    return python, script


def supports(filename: str) -> bool:
    return Path(filename).suffix.lower() in CAD_PREVIEW_SUFFIXES


def previews_dir(kb_id: str) -> Path:
    kb_dir = knowledge_base_dir(kb_id)
    knowledge_dir = kb_dir.parent
    root = knowledge_dir.parent
    root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(root):
        raise CadPreviewFailed("knowledge preview cache path traverses a link")
    if knowledge_dir.exists() and _is_link_or_reparse(knowledge_dir):
        raise CadPreviewFailed("knowledge preview cache path traverses a link")
    knowledge_dir.mkdir(exist_ok=True)
    if kb_dir.exists() and _is_link_or_reparse(kb_dir):
        raise CadPreviewFailed("knowledge preview cache path traverses a link")
    kb_dir.mkdir(exist_ok=True)
    path = kb_dir / "previews"
    if path.exists() and _is_link_or_reparse(path):
        raise CadPreviewFailed("knowledge preview cache path traverses a link")
    path.mkdir(exist_ok=True)
    return path


def _document_component(doc_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
        return doc_id
    return hashlib.sha256(doc_id.encode("utf-8", errors="replace")).hexdigest()


def _content_component(content_hash: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", content_hash or ""):
        return content_hash.lower()
    # Missing/unknown hashes are deliberately unique so they cannot pin stale
    # "current" output or collide with a later unknown version.
    return uuid.uuid4().hex + uuid.uuid4().hex


def preview_png_path(kb_id: str, doc_id: str, content_hash: str) -> Path:
    return previews_dir(kb_id) / (
        f"{_document_component(doc_id)}-{_content_component(content_hash)}.png"
    )


@contextlib.contextmanager
def _cache_lock(root: Path) -> Iterator[None]:
    """Serialize cache mutations without leaving a persistent lock artifact."""
    if _is_link_or_reparse(root) or not root.is_dir():
        raise CadPreviewFailed("preview cache home is not a plain directory")
    with directory_lock(root):
        yield


def _render_locked(
    *, kb_id: str, doc_id: str, original: Path, content_hash: str, filename: str
) -> Path:
    if not supports(filename):
        raise CadPreviewUnavailable(f"{filename} is not a CAD drawing")
    target = preview_png_path(kb_id, doc_id, content_hash)
    if not _is_link_or_reparse(target) and target.is_file():
        cached_size = target.stat().st_size
        if 0 < cached_size <= _MAX_CACHE_BYTES:
            os.utime(target, None)
            return target
        target.unlink(missing_ok=True)
    found = converter()
    if found is None:
        raise CadPreviewUnavailable(
            f"no CAD preview converter; set {_ENV_SCRIPT} to a dwg_preview.py path"
        )
    from octop.infra.utils.tempfiles import managed_tempdir

    python, script = found
    with managed_tempdir(prefix="cad-preview-") as workdir:
        try:
            result = subprocess.run(
                [python, script, str(original), str(workdir)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CadPreviewFailed(f"converter could not run: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-600:]
            raise CadPreviewFailed(f"converter exited with status {result.returncode}: {detail}")
        produced = next(iter(sorted(workdir.glob("*.png"))), None)
        if produced is None or produced.stat().st_size == 0:
            detail = (result.stderr or result.stdout).strip()[-600:]
            raise CadPreviewFailed(f"converter produced no image: {detail}")
        if produced.stat().st_size > _MAX_CACHE_BYTES:
            raise CadPreviewFailed(
                f"converter image exceeds the {_MAX_CACHE_BYTES}-byte preview limit"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with produced.open("rb") as source, staged.open("xb") as destination:
                while block := source.read(1024 * 1024):
                    destination.write(block)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
    return target


def render_preview(
    *, kb_id: str, doc_id: str, original: Path, content_hash: str, filename: str
) -> Path:
    """Return a content-keyed cached PNG, converting at most once at a time."""
    root = PathLayout.from_env().root
    root.mkdir(parents=True, exist_ok=True)
    with _cache_lock(root):
        result = _render_locked(
            kb_id=kb_id,
            doc_id=doc_id,
            original=original,
            content_hash=content_hash,
            filename=filename,
        )
        _prune_locked(
            root,
            _MAX_CACHE_BYTES,
            _MAX_CACHE_FILES,
            _MAX_CACHE_AGE_SECONDS,
            protected=result,
        )
        return result


def snapshot_preview(
    *,
    kb_id: str,
    doc_id: str,
    original: Path,
    content_hash: str,
    filename: str,
    snapshot: Path,
) -> Path:
    """Render and copy a response-owned snapshot before eviction can run."""
    root = PathLayout.from_env().root
    root.mkdir(parents=True, exist_ok=True)
    with _cache_lock(root):
        cached = _render_locked(
            kb_id=kb_id,
            doc_id=doc_id,
            original=original,
            content_hash=content_hash,
            filename=filename,
        )
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with cached.open("rb") as source, snapshot.open("xb") as destination:
            while block := source.read(1024 * 1024):
                destination.write(block)
        os.utime(cached, None)
        _prune_locked(
            root,
            _MAX_CACHE_BYTES,
            _MAX_CACHE_FILES,
            _MAX_CACHE_AGE_SECONDS,
            protected=cached,
        )
        return snapshot


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _prune_locked(
    root: Path,
    max_bytes: int,
    max_files: int,
    max_age_seconds: float,
    *,
    protected: Path | None = None,
) -> dict[str, int]:
    now = time.time()
    candidates: list[tuple[Path, int, float, bool]] = []
    if _is_link_or_reparse(root) or not root.is_dir():
        return {"deleted_files": 0, "deleted_bytes": 0}
    knowledge = root / "knowledge"
    if _is_link_or_reparse(knowledge) or not knowledge.is_dir():
        return {"deleted_files": 0, "deleted_bytes": 0}
    for kb_dir in knowledge.iterdir():
        if _is_link_or_reparse(kb_dir) or not kb_dir.is_dir():
            continue
        previews = kb_dir / "previews"
        if _is_link_or_reparse(previews) or not previews.is_dir():
            continue
        for item in previews.iterdir():
            abandoned_stage = bool(_CACHE_STAGE_NAME.fullmatch(item.name))
            if (
                not (abandoned_stage or _CACHE_NAME.fullmatch(item.name))
                or _is_link_or_reparse(item)
                or not item.is_file()
            ):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            candidates.append((item, stat.st_size, stat.st_mtime, abandoned_stage))
    # Holding the cross-process cache lock proves that no publisher can still
    # be writing these stages. Remove them before evicting reusable previews.
    candidates.sort(key=lambda row: (not row[3], row[2]))
    total_bytes = sum(row[1] for row in candidates)
    total_files = len(candidates)
    deleted_files = deleted_bytes = 0
    for path, size, modified, abandoned_stage in candidates:
        expired = abandoned_stage or max_age_seconds <= 0 or now - modified > max_age_seconds
        over_quota = total_bytes > max(0, max_bytes) or total_files > max(0, max_files)
        if path == protected:
            continue
        if not expired and not over_quota:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total_bytes -= size
        total_files -= 1
        deleted_files += 1
        deleted_bytes += size
    return {"deleted_files": deleted_files, "deleted_bytes": deleted_bytes}


def delete_document_previews(kb_id: str, doc_id: str) -> dict[str, int]:
    """Remove only recognized derived preview versions belonging to one document."""
    root = PathLayout.from_env().root
    previews = knowledge_base_dir(kb_id) / "previews"
    for directory in (root, previews.parent.parent, previews.parent, previews):
        if not directory.exists() and not directory.is_symlink():
            return {"deleted_files": 0, "deleted_bytes": 0}
        if _is_link_or_reparse(directory) or not directory.is_dir():
            return {"deleted_files": 0, "deleted_bytes": 0}
    prefix = re.escape(_document_component(doc_id))
    owned_name = re.compile(rf"^{prefix}-(?:[0-9a-f]{{16}}|[0-9a-f]{{64}}|current)\.png$")
    with _cache_lock(root):
        deleted_files = deleted_bytes = 0
        for item in previews.iterdir():
            if (
                not owned_name.fullmatch(item.name)
                or _is_link_or_reparse(item)
                or not item.is_file()
            ):
                continue
            try:
                size = item.stat().st_size
                item.unlink()
            except OSError:
                continue
            deleted_files += 1
            deleted_bytes += size
        return {"deleted_files": deleted_files, "deleted_bytes": deleted_bytes}


def prune_preview_cache(
    *,
    home: Path | None = None,
    max_bytes: int = _MAX_CACHE_BYTES,
    max_files: int = _MAX_CACHE_FILES,
    max_age_seconds: float = _MAX_CACHE_AGE_SECONDS,
) -> dict[str, int]:
    """Prune only recognized CAD preview PNGs under the selected platform home."""
    root = Path(home) if home is not None else PathLayout.from_env().root
    if not root.exists() or _is_link_or_reparse(root) or not root.is_dir():
        return {"deleted_files": 0, "deleted_bytes": 0}
    with _cache_lock(root):
        return _prune_locked(root, max_bytes, max_files, max_age_seconds)


__all__ = [
    "CAD_PREVIEW_SUFFIXES",
    "CadPreviewFailed",
    "delete_document_previews",
    "converter",
    "preview_png_path",
    "previews_dir",
    "prune_preview_cache",
    "render_preview",
    "snapshot_preview",
    "supports",
]
