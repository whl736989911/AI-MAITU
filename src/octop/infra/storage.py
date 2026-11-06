"""Read-only storage inventory and conservative maintenance."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from octop.infra.utils.env_file import parse_env_text
from octop.infra.utils.paths import PathLayout

_CATEGORIES = (
    "temp",
    "previews",
    "logs",
    "backups",
    "knowledge",
    "workspaces",
    "databases",
    "models",
    "other",
)
_SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")
_INDEX_DATABASE_NAMES = frozenset(f"index.sqlite{suffix}" for suffix in _SQLITE_SUFFIXES)
_WORKSPACE_DATABASE_NAMES = frozenset(
    f"{name}{suffix}"
    for name in ("memory.sqlite", "checkpoints.sqlite", "sessions.sqlite")
    for suffix in _SQLITE_SUFFIXES
)


def _has_link_ancestor(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _category(relative: Path, *, db_files: set[Path], absolute: Path) -> str:
    parts = relative.parts
    if absolute in db_files:
        return "databases"
    if len(parts) == 3 and parts[0] == "knowledge" and parts[2] in _INDEX_DATABASE_NAMES:
        return "databases"
    if parts and parts[0] in ("agents", "workspaces") and parts[-1] in _WORKSPACE_DATABASE_NAMES:
        return "databases"
    if parts and parts[0] == "tmp":
        return "temp"
    if len(parts) >= 2 and parts[0] == "agents" and parts[1] == ".execute-tmp":
        return "temp"
    if len(parts) == 4 and parts[0] == "knowledge" and parts[2] == "previews":
        return "previews"
    if parts and parts[0] == "logs":
        return "logs"
    if len(parts) == 1 and relative.suffix == ".log":
        return "logs"
    if parts and parts[0] == "backups":
        return "backups"
    if parts and parts[0] in ("knowledge", "knowledge-collections"):
        return "knowledge"
    if parts and parts[0] in ("workspaces", "agents"):
        return "workspaces"
    if parts and parts[0] == "embedding_models":
        return "models"
    return "other"


def _configured_sqlite_paths(
    paths: PathLayout, errors: list[str]
) -> tuple[list[Path], dict[str, str]]:
    dbs = [paths.db, paths.root / "history_v2.sqlite"]
    database: dict[str, Any] = {}
    scope = {
        "kind": "sqlite",
    }
    if _has_link_ancestor(paths.config):
        errors.append("config.json is a link/reparse path; configuration skipped")
        return dbs, {"kind": "unknown"}
    if paths.config.exists():
        try:
            config = json.loads(paths.config.read_text(encoding="utf-8"))
            value = config.get("database", {}) if isinstance(config, dict) else {}
            if isinstance(value, dict):
                database = value
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"config.json: {type(exc).__name__}: {exc}")
            return dbs, {"kind": "unknown"}
    env_path = paths.root / "env"
    if _has_link_ancestor(env_path):
        errors.append("env file is a link/reparse path; database environment skipped")
        return dbs, {"kind": "unknown"}
    db_environment = {
        key: os.environ[key]
        for key in ("OCTOP_DATABASE_URL", "OCTOP_DATABASE_DRIVER", "OCTOP_DATABASE_SQLITE_PATH")
        if key in os.environ
    }
    if env_path.is_file():
        try:
            env_values = parse_env_text(env_path.read_text(encoding="utf-8"))
            db_environment.update(
                {
                    key: env_values[key]
                    for key in (
                        "OCTOP_DATABASE_URL",
                        "OCTOP_DATABASE_DRIVER",
                        "OCTOP_DATABASE_SQLITE_PATH",
                    )
                    if key in env_values
                }
            )
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"env: {type(exc).__name__}: {exc}")
            return dbs, {"kind": "unknown"}
    driver = db_environment.get(
        "OCTOP_DATABASE_DRIVER", str(database.get("driver", "sqlite"))
    ).lower()
    if db_environment.get("OCTOP_DATABASE_URL"):
        return dbs, {
            "kind": "external",
        }
    if driver not in ("sqlite", "sqlite3"):
        return dbs, {
            "kind": "external",
        }
    raw = db_environment.get("OCTOP_DATABASE_SQLITE_PATH", database.get("sqlite_path", "maitu.db"))
    if not isinstance(raw, str) or not raw:
        errors.append("database.sqlite_path is not a usable string")
        return dbs, {
            "kind": "unknown",
        }
    configured = Path(raw)
    if not configured.is_absolute():
        configured = paths.root / configured
    configured = Path(os.path.abspath(configured))
    try:
        configured.relative_to(Path(os.path.abspath(paths.root)))
    except ValueError:
        return dbs, {
            "kind": "outside",
        }
    if configured not in dbs:
        dbs.append(configured)
    return dbs, scope


def storage_report(paths: PathLayout) -> dict[str, Any]:
    """Inventory files without creating, opening writable, or following links."""
    errors: list[str] = []
    db_paths, database_scope = _configured_sqlite_paths(paths, errors)
    db_paths = list(dict.fromkeys(Path(os.path.abspath(path)) for path in db_paths))
    db_files = set(db_paths)
    for database in db_paths:
        db_files.update(Path(f"{database}-{suffix}") for suffix in ("wal", "shm", "journal"))
    totals = {name: {"bytes": 0, "files": 0} for name in _CATEGORIES}
    root = paths.root
    if _has_link_ancestor(root):
        errors.append("storage root is a link/reparse path; inventory skipped")
    elif root.exists():
        if not root.is_dir():
            errors.append("storage root exists but is not a directory")
        else:
            stack = [(root, Path())]
            while stack:
                directory, relative_dir = stack.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            rel = relative_dir / entry.name
                            try:
                                if entry.is_symlink():
                                    continue
                                if entry.is_dir(follow_symlinks=False):
                                    # Windows junctions/reparse points are not traversed.
                                    attrs = getattr(
                                        entry.stat(follow_symlinks=False), "st_file_attributes", 0
                                    )
                                    if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                                        continue
                                    stack.append((Path(entry.path), rel))
                                elif entry.is_file(follow_symlinks=False):
                                    st = entry.stat(follow_symlinks=False)
                                    absolute = Path(os.path.abspath(entry.path))
                                    category = _category(rel, db_files=db_files, absolute=absolute)
                                    totals[category]["bytes"] += st.st_size
                                    totals[category]["files"] += 1
                            except OSError as exc:
                                errors.append(f"{rel.as_posix()}: {type(exc).__name__}: {exc}")
                except OSError as exc:
                    errors.append(f"{relative_dir.as_posix() or '.'}: {type(exc).__name__}: {exc}")
    else:
        errors.append("storage root does not exist")
    probe = root if root.exists() and not _has_link_ancestor(root) else root.parent
    while (not probe.exists() or _has_link_ancestor(probe)) and probe != probe.parent:
        probe = probe.parent
    try:
        disk = shutil.disk_usage(probe)
        disk_info: dict[str, int] | None = {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
            "used_bytes": disk.used,
        }
    except OSError as exc:
        disk_info = None
        errors.append(f"disk usage: {type(exc).__name__}: {exc}")
    return {
        "root": str(root),
        "disk": disk_info,
        "categories": totals,
        "database_scope": database_scope,
        "inventory_scope": {"root_only": True, "follows_links": False, "remote_storage": False},
        "sqlite_page_metrics": {
            "available": False,
            "reason": "read_only_filesystem_inventory",
        },
        "retention": {
            "preserved": [
                "originals",
                "attachments",
                "chat_content",
                "embedding_models",
                "manual_backups",
            ],
            "temp_grace_seconds": 86400,
            "preview_defaults": {"max_bytes": 1073741824, "max_files": 10000, "idle_days": 7},
            "log_defaults": {"archive_max_bytes": 1073741824, "retention_days": 14},
            "log_retention_days_configurable": True,
            "trajectory_user_turns": 50,
            "chat_history_age_limit_days": None,
            "attachment_orphan_deletion": False,
        },
        "reclamation": {
            "opens_databases": False,
            "automatic_full_vacuum": False,
            "deletes_live_sqlite_sidecars": False,
        },
        "errors": sorted(errors),
    }


def maintain_storage(paths: PathLayout) -> dict[str, int]:
    """Remove only managed temporary directories and bounded preview cache."""
    from octop.infra.knowledge.cad_preview import prune_preview_cache
    from octop.infra.utils.tempfiles import cleanup_stale_tempdirs

    temp_dirs = cleanup_stale_tempdirs(root=paths.root / "tmp")
    execute_root = paths.agents_dir / ".execute-tmp"
    execute_dirs = (
        cleanup_stale_tempdirs(root=execute_root)
        if execute_root.exists() and not execute_root.is_symlink()
        else 0
    )
    preview = prune_preview_cache(home=paths.root)
    return {
        "deleted_temp_dirs": temp_dirs + execute_dirs,
        "deleted_execute_temp_dirs": execute_dirs,
        **{f"preview_{key}": int(value) for key, value in preview.items()},
    }
