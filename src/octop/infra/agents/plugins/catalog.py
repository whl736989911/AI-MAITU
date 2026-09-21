"""In-package plugin catalog backing the Admin plugin market.

The wheel ships a fixed plugin set under ``agents/plugins/bundled``. Seeding
copies those into ``~/.octop/plugins`` **globally disabled** and never re-copies
an id the user uninstalled, so this catalog — not the installed list — is what
keeps a shipped plugin reachable.

``plugin.yaml`` is the single source of truth. Octop-only extras
(``name_en`` / ``description_en``) are read the same way as ``icon`` and ``ui``:
the harness ignores unknown keys, Octop renders them in the Dashboard.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from harness_agent.plugins import PluginManifest

from octop.infra.agents.plugins.bundled import default_bundled_plugins_root
from octop.infra.agents.plugins.manager import (
    parse_plugin_icon,
    parse_plugin_requires,
    parse_plugin_ui_meta,
)
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)

# Plugin ids are directory names; keep them to what the installer accepts so a
# request can never walk out of the catalog root.
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class CatalogPlugin:
    """One shipped plugin, as the market lists it."""

    id: str
    version: str
    name: str
    description: str
    kind: str
    name_en: str
    description_en: str
    icon: str | None
    requires: tuple[str, ...]
    has_ui: bool
    source_dir: Path

    def label(self) -> dict[str, str]:
        """Localized name; ``en`` falls back to the manifest text when unset."""
        return {"zh": self.name, "en": self.name_en or self.name}

    def summary(self) -> dict[str, str]:
        """Localized description; ``en`` falls back to the manifest text."""
        return {"zh": self.description, "en": self.description_en or self.description}


def _read_locale_extras(plugin_dir: Path) -> tuple[str, str]:
    """Read ``name_en`` / ``description_en`` from ``plugin.yaml`` (Octop extras)."""
    import yaml

    try:
        raw = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
    except Exception as exc:  # unreadable manifest fails PluginManifest.load too
        logger.error("plugin catalog: cannot read %s: %s", plugin_dir / "plugin.yaml", exc)
        return "", ""
    if not isinstance(raw, dict):
        return "", ""
    return (
        str(raw.get("name_en") or "").strip(),
        str(raw.get("description_en") or "").strip(),
    )


def _entry_for(plugin_dir: Path) -> CatalogPlugin | None:
    """Build a catalog entry, or ``None`` when the dir is not a valid plugin.

    Mirrors seeding / ``load_installed``: an unreadable manifest is logged and
    skipped instead of failing the whole catalog.
    """
    try:
        manifest = PluginManifest.load(plugin_dir / "plugin.yaml")
    except Exception as exc:
        logger.error("plugin catalog: skip %s: %s", plugin_dir, exc)
        return None
    name_en, description_en = _read_locale_extras(plugin_dir)
    return CatalogPlugin(
        id=manifest.id,
        version=manifest.version,
        name=manifest.name,
        description=manifest.description,
        kind=manifest.kind,
        name_en=name_en,
        description_en=description_en,
        icon=parse_plugin_icon(plugin_dir),
        requires=tuple(parse_plugin_requires(plugin_dir)),
        has_ui=parse_plugin_ui_meta(plugin_dir) is not None,
        source_dir=plugin_dir,
    )


def list_catalog_plugins() -> list[CatalogPlugin]:
    """Every shipped plugin, ordered by id (the order the market renders)."""
    root = default_bundled_plugins_root()
    if not root.is_dir():
        return []
    entries: list[CatalogPlugin] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if not (child / "plugin.yaml").is_file():
            continue
        entry = _entry_for(child)
        if entry is not None:
            entries.append(entry)
    return entries


def get_catalog_plugin(plugin_id: str) -> CatalogPlugin:
    """One shipped plugin by id; ``NOT_FOUND`` for anything not in the catalog."""
    cleaned = plugin_id.strip()
    if not _PLUGIN_ID_RE.match(cleaned):
        raise OctopError(ErrorCode.NOT_FOUND, f"plugin {plugin_id!r} is not in the catalog")
    for entry in list_catalog_plugins():
        if entry.id == cleaned:
            return entry
    raise OctopError(ErrorCode.NOT_FOUND, f"plugin {cleaned!r} is not in the catalog")
