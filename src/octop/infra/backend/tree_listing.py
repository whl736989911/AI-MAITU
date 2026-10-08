"""Single-level storage tree normalization and self-entry filtering."""

from __future__ import annotations

from typing import Any


def normalize_storage_path(path: str) -> str:
    """Normalize an Admin tree path and reject traversal above its virtual root."""
    raw = (path or "").strip() or "/"
    parts: list[str] = []
    for part in raw.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("invalid path")
        parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def is_listing_self(entry_path: str, *, parent: str) -> bool:
    """Return whether an entry refers to the directory currently being listed."""
    text = str(entry_path or "").strip().replace("\\", "/").rstrip("/")
    base = str(parent or "").strip().replace("\\", "/").rstrip("/")
    if not text or text in {".", "./"}:
        return True
    if base in {"", ".", "./", "/"}:
        return text in {".", "./", "/"}
    return text == base or text == base.lstrip("/")


def dedupe_tree_rows(rows: list[dict[str, Any]], *, parent: str) -> list[dict[str, Any]]:
    """Drop self rows, normalize directory suffixes, and keep one row per path."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        raw = str(row.get("path") or "").replace("\\", "/").strip()
        if is_listing_self(raw, parent=parent):
            continue
        is_dir = bool(row.get("is_dir")) or raw.endswith("/")
        norm = raw.rstrip("/") or ("." if parent in {"", "."} else parent)
        if is_listing_self(norm, parent=parent):
            continue
        existing = merged.get(norm)
        if existing is not None:
            if is_dir and not existing.get("is_dir"):
                existing["is_dir"] = True
            continue
        merged[norm] = {**row, "path": norm, "is_dir": is_dir}
        order.append(norm)
    return [merged[key] for key in order]
