"""Copy packaged plugins into the user plugins directory, globally disabled."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from octop.infra.errors import corrupt_config_error
from octop.infra.utils.json_file import (
    JsonFileCorruptError,
    read_json_object,
    write_json_atomic,
)


def _read_config(config_path: Path) -> dict[str, Any]:
    """Load ``config.json`` for merging; ``{}`` when absent.

    A corrupt file raises instead of reading as empty: ``seed_bundled_plugins``
    always writes back, so merging into ``{}`` would destroy every other setting
    (issue #730).
    """
    try:
        data = read_json_object(config_path)
    except JsonFileCorruptError as exc:
        raise corrupt_config_error(exc.path, exc.detail) from exc
    return data if data is not None else {}


def _write_config(config_path: Path, data: dict[str, Any]) -> None:
    write_json_atomic(config_path, data)


def _plugin_version(plugin_dir: Path) -> tuple[int, ...]:
    path = plugin_dir / "plugin.yaml"
    if not path.is_file():
        return (0,)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return (0,)
    ver = str((raw or {}).get("version") or "0") if isinstance(raw, dict) else "0"
    parts: list[int] = []
    for bit in ver.split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def seed_bundled_plugins(
    *,
    bundled_root: Path,
    plugins_dir: Path,
    config_path: Path,
) -> list[str]:
    """Copy missing bundled plugins into ``plugins_dir``, globally disabled.

    An id listed in ``bundled_plugins_seeded`` is never re-created after
    uninstall. Existing dest dirs are overwritten only when the bundled
    ``plugin.yaml`` version is newer (enabled flag is preserved).
    """
    if not bundled_root.is_dir():
        return []
    data = _read_config(config_path)
    seeded_raw = data.get("bundled_plugins_seeded")
    seeded: list[str] = [str(x) for x in seeded_raw] if isinstance(seeded_raw, list) else []
    seeded_set = set(seeded)
    plugins_cfg = data.get("plugins")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
        data["plugins"] = plugins_cfg

    copied: list[str] = []
    plugins_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".bundled-seed-", dir=plugins_dir))
    staged: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path | None]] = []
    clean_work_dir = True
    try:
        incoming = work_dir / "incoming"
        previous = work_dir / "previous"
        incoming.mkdir()
        previous.mkdir()
        for child in sorted(bundled_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if not (child / "plugin.yaml").is_file():
                continue
            plugin_id = child.name
            dest = plugins_dir / plugin_id
            if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
                raise NotADirectoryError(dest)
            if dest.exists():
                should_copy = _plugin_version(child) > _plugin_version(dest)
                if plugin_id not in seeded_set:
                    seeded.append(plugin_id)
                    seeded_set.add(plugin_id)
                    entry = plugins_cfg.get(plugin_id)
                    if not isinstance(entry, dict):
                        plugins_cfg[plugin_id] = {"enabled": False}
            else:
                if plugin_id in seeded_set:
                    continue
                should_copy = True
                existing = plugins_cfg.get(plugin_id)
                entry = dict(existing) if isinstance(existing, dict) else {}
                entry["enabled"] = False
                plugins_cfg[plugin_id] = entry
                seeded.append(plugin_id)
                seeded_set.add(plugin_id)
            if should_copy:
                staged_dir = incoming / plugin_id
                shutil.copytree(child, staged_dir)
                staged.append((staged_dir, dest))
                copied.append(plugin_id)

        for staged_dir, dest in staged:
            backup = previous / dest.name if dest.exists() else None
            if backup is not None:
                os.replace(dest, backup)
            published.append((dest, backup))
            os.replace(staged_dir, dest)
        data["bundled_plugins_seeded"] = seeded
        data["plugins"] = plugins_cfg
        _write_config(config_path, data)
    except BaseException:
        try:
            for dest, backup in reversed(published):
                if dest.exists():
                    shutil.rmtree(dest)
                if backup is not None:
                    os.replace(backup, dest)
        except BaseException:
            clean_work_dir = False
            raise
        raise
    finally:
        if clean_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    return copied
