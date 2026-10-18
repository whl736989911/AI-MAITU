"""Install, load, and expose plugins under ``~/.octop/plugins/``."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from harness_agent.plugins import (
    LoadedPlugin,
    PluginManifest,
    PluginRegistry,
    discover_plugin_dirs,
    load_plugin_dir,
    unload_plugin,
)

from octop.infra.errors import ErrorCode, OctopError, corrupt_config_error
from octop.infra.utils.json_file import (
    JsonFileCorruptError,
    read_json_object,
    write_json_atomic,
)

logger = logging.getLogger(__name__)


def _require_business_access() -> None:
    from octop.infra.licensing import current_license

    current_license().require_business_access()


def _is_shipped_plugin(plugin_dir: Path, plugin_id: str) -> bool:
    from octop.infra.agents.plugins.catalog import get_catalog_plugin

    try:
        source = get_catalog_plugin(plugin_id).source_dir
    except OctopError:
        return False
    if not plugin_dir.is_dir() or not source.is_dir():
        return False
    source_files: set[str] = set()
    installed_files: set[str] = set()
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        files = [name for name in files if not name.endswith(".pyc")]
        if any((Path(root) / name).is_symlink() for name in dirs + files):
            return False
        source_files.update((Path(root) / name).relative_to(source).as_posix() for name in files)
    for root, dirs, files in os.walk(plugin_dir, followlinks=False):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        files = [name for name in files if not name.endswith(".pyc")]
        if any((Path(root) / name).is_symlink() for name in dirs + files):
            return False
        installed_files.update(
            (Path(root) / name).relative_to(plugin_dir).as_posix() for name in files
        )
    if source_files != installed_files:
        return False
    return all(
        (source / name).read_bytes() == (plugin_dir / name).read_bytes() for name in source_files
    )


def _read_global_plugins(config_path: Path) -> dict[str, bool]:
    if not config_path.is_file():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    plugins = raw.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    out: dict[str, bool] = {}
    for plugin_id, entry in plugins.items():
        if isinstance(entry, dict):
            out[str(plugin_id)] = bool(entry.get("enabled", True))
        else:
            out[str(plugin_id)] = True
    return out


def _write_global_plugin_enabled(config_path: Path, plugin_id: str, enabled: bool) -> None:
    """Merge ``plugins.<id>.enabled`` into ``config.json`` without dropping other keys.

    A corrupt config.json raises instead of being treated as empty: merging into
    ``{}`` and writing back destroys every other setting, including the
    ``database`` section (issue #730).
    """
    try:
        data = read_json_object(config_path)
    except JsonFileCorruptError as exc:
        raise corrupt_config_error(exc.path, exc.detail) from exc
    if data is None:
        data = {}
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        data["plugins"] = plugins
    entry = plugins.get(plugin_id)
    if not isinstance(entry, dict):
        entry = {}
    entry["enabled"] = bool(enabled)
    plugins[plugin_id] = entry
    write_json_atomic(config_path, data)


def _read_plugin_yaml(plugin_dir: Path) -> dict[str, Any]:
    raw = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def parse_plugin_ui_meta(plugin_dir: Path) -> dict[str, str] | None:
    """Return ``{entry, manifest}`` relative paths when ``plugin.yaml`` declares ``ui``.

    Missing entry file → ``None`` (treat as backend-only). Harness ignores the
    ``ui`` key; Octop surfaces it for Dashboard dynamic loading.
    """
    try:
        data = _read_plugin_yaml(plugin_dir)
    except Exception:
        return None
    ui = data.get("ui")
    if not isinstance(ui, dict):
        return None
    entry = str(ui.get("entry") or "ui/dist/index.js").strip()
    manifest = str(ui.get("manifest") or "ui/dist/manifest.json").strip()
    if not entry or ".." in entry.replace("\\", "/").split("/"):
        return None
    if ".." in manifest.replace("\\", "/").split("/"):
        return None
    if not (plugin_dir / entry).is_file():
        logger.warning(
            "plugin %s declares ui.entry=%s but file is missing",
            plugin_dir.name,
            entry,
        )
        return None
    return {"entry": entry, "manifest": manifest}


_ICON_FILE_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MARKET_ASSET_SUFFIXES = frozenset(_ICON_FILE_SUFFIXES)


def parse_plugin_group(plugin_dir: Path) -> str | None:
    """Read a normalized optional group slug from ``plugin.yaml``."""
    try:
        raw = _read_plugin_yaml(plugin_dir).get("group")
    except Exception:
        return None
    if raw is None:
        return None
    group = str(raw).strip().lower().replace("_", "-")
    if not group or len(group) > 64 or not all(ch.isalnum() or ch == "-" for ch in group):
        return None
    return group


def parse_plugin_icon(
    plugin_dir: Path,
    *,
    asset_prefix: str | None = None,
) -> str | None:
    """Return an icon value, resolving an existing package-relative image path."""
    try:
        data = _read_plugin_yaml(plugin_dir)
    except Exception:
        return None
    raw = data.get("icon")
    if raw is None:
        return None
    icon = str(raw).strip()
    if not icon or len(icon) > 2048:
        return None
    if icon.startswith(("http://", "https://", "/api/", "data:image/")) or icon.startswith("//"):
        return icon
    cleaned = icon.lstrip("/").replace("\\", "/")
    if (
        cleaned
        and ".." not in cleaned.split("/")
        and Path(cleaned).suffix.lower() in _ICON_FILE_SUFFIXES
    ):
        target = (plugin_dir / cleaned).resolve()
        try:
            target.relative_to(plugin_dir.resolve())
        except ValueError:
            return icon
        if target.is_file():
            plugin_id = str(data.get("id") or plugin_dir.name).strip() or plugin_dir.name
            prefix = (asset_prefix or f"/api/plugins/{plugin_id}/ui").rstrip("/")
            return f"{prefix}/{cleaned}"
    return icon


def parse_plugin_requires(plugin_dir: Path) -> list[str]:
    try:
        data = _read_plugin_yaml(plugin_dir)
    except Exception:
        return []
    raw = data.get("requires") or []
    if not isinstance(raw, list):
        return []
    return [str(r).strip() for r in raw if str(r).strip()]


class PluginManager:
    def __init__(self, *, plugins_dir: Path, config_path: Path) -> None:
        self._plugins_dir = plugins_dir
        self._config_path = config_path
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        # Last-known tool metadata so Admin / Experts can still list tools after
        # a global disable unloads the plugin from the process registry.
        self._tool_catalog: dict[str, list[dict[str, Any]]] = {}
        self._skill_catalog: dict[str, set[str]] = {}

    @property
    def plugins_dir(self) -> Path:
        return self._plugins_dir

    def global_enabled_map(self) -> dict[str, bool]:
        return _read_global_plugins(self._config_path)

    def seed_bundled(self) -> list[str]:
        """Copy packaged plugins into ``plugins_dir`` with ``enabled: false``."""
        from octop.infra.agents.plugins.bundled import default_bundled_plugins_root
        from octop.infra.agents.plugins.seed import seed_bundled_plugins

        return seed_bundled_plugins(
            bundled_root=default_bundled_plugins_root(),
            plugins_dir=self._plugins_dir,
            config_path=self._config_path,
        )

    def load_installed(self, *, install_deps: bool = False) -> list[LoadedPlugin]:
        _require_business_access()
        if install_deps:
            raise OctopError(
                ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                "Installing plugin dependencies is disabled.",
            )
        enabled = self.global_enabled_map()
        loaded: list[LoadedPlugin] = []
        for plugin_dir in discover_plugin_dirs(self._plugins_dir):
            try:
                manifest = PluginManifest.load(plugin_dir / "plugin.yaml")
            except Exception as exc:
                logger.error("skip plugin dir %s: %s", plugin_dir, exc)
                continue
            if enabled.get(manifest.id, True) is False:
                continue
            if not _is_shipped_plugin(plugin_dir, manifest.id):
                logger.warning("skip unshipped or modified plugin code: %s", plugin_dir)
                continue
            try:
                loaded.append(load_plugin_dir(plugin_dir, install_deps=False))
            except Exception as exc:
                logger.error("failed to load plugin from %s: %s", plugin_dir, exc, exc_info=True)

        loaded_ids = {plugin.manifest.id for plugin in loaded}
        for plugin in PluginRegistry().list_plugins():
            if plugin.manifest.id not in loaded_ids:
                unload_plugin(plugin.manifest.id)
        for plugin_id, is_on in enabled.items():
            if not is_on:
                unload_plugin(plugin_id)
        return loaded

    def load_missing(self, *, install_deps: bool = False) -> list[LoadedPlugin]:
        _require_business_access()
        if install_deps:
            raise OctopError(
                ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                "Installing plugin dependencies is disabled.",
            )
        enabled = self.global_enabled_map()
        newly: list[LoadedPlugin] = []
        for plugin_dir in discover_plugin_dirs(self._plugins_dir):
            try:
                manifest = PluginManifest.load(plugin_dir / "plugin.yaml")
            except Exception as exc:
                logger.error("skip plugin dir %s: %s", plugin_dir, exc)
                continue
            if enabled.get(manifest.id) is False or not _is_shipped_plugin(plugin_dir, manifest.id):
                continue
            if PluginRegistry().get(manifest.id) is not None:
                continue
            try:
                newly.append(load_plugin_dir(plugin_dir, install_deps=False))
            except Exception as exc:
                logger.error("failed to load plugin from %s: %s", plugin_dir, exc, exc_info=True)
        return newly

    def list_installed(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        enabled_map = self.global_enabled_map()
        for plugin_dir in discover_plugin_dirs(self._plugins_dir):
            try:
                manifest = PluginManifest.load(plugin_dir / "plugin.yaml")
            except Exception as exc:
                out.append(
                    {
                        "id": plugin_dir.name,
                        "error": str(exc),
                        "path": str(plugin_dir),
                        "enabled": enabled_map.get(plugin_dir.name, True),
                    },
                )
                continue
            loaded = PluginRegistry().get(manifest.id)
            ui_meta = parse_plugin_ui_meta(plugin_dir)
            if loaded is not None:
                tools_meta = [
                    {
                        "name": t.name,
                        "description": t.description,
                        "config_fields": t.config_fields,
                    }
                    for t in loaded.tools
                ]
                self._tool_catalog[manifest.id] = tools_meta
                if loaded.skills_dir is not None:
                    self._skill_catalog[manifest.id] = {
                        path.name
                        for path in loaded.skills_dir.iterdir()
                        if path.is_dir() and (path / "SKILL.md").is_file()
                    }
            else:
                tools_meta = list(self._tool_catalog.get(manifest.id) or [])
            out.append(
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "name": manifest.name,
                    "kind": manifest.kind,
                    "description": manifest.description,
                    "icon": parse_plugin_icon(plugin_dir),
                    "group": parse_plugin_group(plugin_dir),
                    "path": str(plugin_dir),
                    "loaded": loaded is not None,
                    "enabled": enabled_map.get(manifest.id, True),
                    "ui": ui_meta,
                    "tools": tools_meta,
                },
            )
        return out

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Toggle a shipped plugin, requiring a valid license to enable code."""
        plugin_dir = self.plugin_dir(plugin_id)
        if plugin_dir is None:
            raise OctopError(ErrorCode.NOT_FOUND, f"plugin {plugin_id!r} not found")
        if enabled:
            _require_business_access()
            if not _is_shipped_plugin(plugin_dir, plugin_id):
                raise OctopError(
                    ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                    "Only unchanged plugins shipped in this application may be enabled.",
                )
        try:
            PluginManifest.load(plugin_dir / "plugin.yaml")
        except Exception as exc:
            raise OctopError(
                ErrorCode.PLUGIN_INVALID_ARCHIVE,
                f"invalid plugin manifest: {exc}",
            ) from exc
        _write_global_plugin_enabled(self._config_path, plugin_id, enabled)
        if enabled:
            if PluginRegistry().get(plugin_id) is None:
                load_plugin_dir(plugin_dir, install_deps=False)
        else:
            loaded = PluginRegistry().get(plugin_id)
            if loaded is not None:
                self._tool_catalog[plugin_id] = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "config_fields": tool.config_fields,
                    }
                    for tool in loaded.tools
                ]
            unload_plugin(plugin_id)
        for item in self.list_installed():
            if item.get("id") == plugin_id:
                return item
        return {"id": plugin_id, "enabled": enabled}

    def market_plugin_dir(self, plugin_id: str) -> Path | None:
        """Return a shipped plugin directory for market-only asset requests."""
        cleaned = plugin_id.strip()
        if not cleaned or any(part in cleaned for part in ("/", "\\", "..")):
            return None
        from octop.infra.agents.plugins.bundled import default_bundled_plugins_root

        dest = default_bundled_plugins_root() / cleaned
        if dest.is_dir() and (dest / "plugin.yaml").is_file():
            return dest
        return None

    def resolve_market_ui_file(self, plugin_id: str, rel_path: str) -> Path:
        """Resolve a shipped image asset without exposing arbitrary package files."""
        plugin_dir = self.market_plugin_dir(plugin_id)
        cleaned = rel_path.strip().lstrip("/").replace("\\", "/")
        if (
            plugin_dir is None
            or not cleaned
            or any(part in {"", ".", ".."} for part in cleaned.split("/"))
            or Path(cleaned).suffix.lower() not in _MARKET_ASSET_SUFFIXES
        ):
            raise OctopError(ErrorCode.NOT_FOUND, "invalid marketplace asset path")
        target = (plugin_dir / cleaned).resolve()
        try:
            target.relative_to(plugin_dir.resolve())
        except ValueError as exc:
            raise OctopError(ErrorCode.NOT_FOUND, "invalid marketplace asset path") from exc
        if not target.is_file():
            raise OctopError(ErrorCode.NOT_FOUND, "marketplace asset not found")
        return target

    def plugin_dir(self, plugin_id: str) -> Path | None:
        """Return the on-disk plugin directory when it exists."""
        dest = self._plugins_dir / plugin_id
        if dest.is_dir() and (dest / "plugin.yaml").is_file():
            return dest
        return None

    def resolve_ui_file(self, plugin_id: str, rel_path: str) -> Path:
        """Resolve a UI asset under the plugin tree with path-traversal checks.

        ``rel_path`` is typically relative to ``<plugin>/ui/`` (e.g. ``dist/index.js``)
        as served by ``GET /api/plugins/{id}/ui/{path}``. Full paths from the
        plugin root (``ui/dist/index.js``) are also accepted.
        """
        plugin_dir = self.plugin_dir(plugin_id)
        if plugin_dir is None:
            raise OctopError(
                ErrorCode.NOT_FOUND,
                f"plugin {plugin_id!r} not found",
            )
        cleaned = rel_path.strip().lstrip("/").replace("\\", "/")
        if not cleaned or any(part == ".." for part in cleaned.split("/")):
            raise OctopError(ErrorCode.NOT_FOUND, "invalid plugin UI path")
        candidates = [
            plugin_dir / "ui" / cleaned,
            plugin_dir / cleaned,
        ]
        if not cleaned.startswith("ui/") and not cleaned.startswith("dist/"):
            candidates.append(plugin_dir / "ui" / "dist" / cleaned)
        for target in candidates:
            resolved = target.resolve()
            try:
                resolved.relative_to(plugin_dir.resolve())
            except ValueError as exc:
                raise OctopError(ErrorCode.NOT_FOUND, "invalid plugin UI path") from exc
            if resolved.is_file():
                return resolved
        raise OctopError(
            ErrorCode.NOT_FOUND,
            f"plugin UI file not found: {cleaned}",
        )

    def install_path(self, source: Path, *, force: bool = False) -> LoadedPlugin:
        _require_business_access()
        source = source.resolve()
        if not source.is_dir():
            raise OctopError(
                ErrorCode.PLUGIN_INVALID_ARCHIVE,
                f"plugin directory not found: {source}",
            )
        try:
            manifest = PluginManifest.load(source / "plugin.yaml")
        except Exception as exc:
            raise OctopError(
                ErrorCode.PLUGIN_INVALID_ARCHIVE,
                f"invalid plugin manifest: {exc}",
            ) from exc
        if not _is_shipped_plugin(source, manifest.id):
            raise OctopError(
                ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                "Only plugins shipped in this application may be installed.",
            )
        dest = self._plugins_dir / manifest.id
        if dest.exists():
            if not force:
                raise OctopError(
                    ErrorCode.PLUGIN_ALREADY_EXISTS,
                    f"plugin already installed: {manifest.id}",
                    details={"id": manifest.id},
                )
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        unload_plugin(manifest.id)
        return load_plugin_dir(dest, install_deps=False)

    def install_archive(self, archive: Path, *, force: bool = False) -> LoadedPlugin:
        _require_business_access()
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Installing plugins from archives is disabled; choose a shipped catalog plugin.",
        )

    def install_url(self, url: str, *, force: bool = False) -> LoadedPlugin:
        _require_business_access()
        raise OctopError(
            ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
            "Installing plugins from URLs is disabled; choose a shipped catalog plugin.",
        )

    def install_bundled(self, plugin_id: str) -> dict[str, Any]:
        """Install and enable a plugin shipped in the wheel catalog.

        Restores a shipped plugin the user uninstalled — seeding never re-copies
        an id it already recorded — and enables one that is present but globally
        off. ``force`` is limited to a half-written directory: an intact plugin
        dir keeps whatever the user has in it. Returns the refreshed installed row.
        """
        _require_business_access()
        from octop.infra.agents.plugins.catalog import get_catalog_plugin

        _require_business_access()
        entry = get_catalog_plugin(plugin_id)
        dest = self._plugins_dir / entry.id
        if not _is_shipped_plugin(dest, entry.id):
            self.install_path(entry.source_dir, force=dest.exists())
        return self.set_enabled(entry.id, True)

    def uninstall(self, plugin_id: str) -> None:
        _require_business_access()
        unload_plugin(plugin_id)
        self._tool_catalog.pop(plugin_id, None)
        self._skill_catalog.pop(plugin_id, None)
        dest = self._plugins_dir / plugin_id
        if dest.is_dir():
            shutil.rmtree(dest)

    def plugin_skill_names(self, plugin_id: str) -> set[str]:
        """Return skill directory names registered by one loaded plugin."""
        plugin = PluginRegistry().get(plugin_id)
        if plugin is None or plugin.skills_dir is None:
            return set(self._skill_catalog.get(plugin_id) or ())
        names = {
            path.name
            for path in plugin.skills_dir.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        self._skill_catalog[plugin_id] = names
        return set(names)

    def sync_skills_to_workspace(
        self,
        workspace: Any,
        *,
        agent_plugins: object = None,
    ) -> None:
        _require_business_access()
        from harness_agent.backends.workspace import BackendWorkspace

        from octop.infra.agents.plugin_tool_defaults import agent_plugin_enabled

        if not isinstance(workspace, BackendWorkspace):
            return
        enabled = self.global_enabled_map()
        pairs: list[tuple[str, bytes]] = []
        for plugin in PluginRegistry().list_plugins():
            if enabled.get(plugin.manifest.id) is False:
                continue
            if not agent_plugin_enabled(agent_plugins, plugin.manifest.id):
                continue
            if plugin.skills_dir is None:
                continue
            for skill_dir in plugin.skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                if not (skill_dir / "SKILL.md").is_file():
                    continue
                dest = f"skills/{skill_dir.name}"
                if workspace.exists(f"{dest}/SKILL.md"):
                    continue
                for path in skill_dir.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(skill_dir).as_posix()
                    pairs.append((f"{dest}/{rel}", path.read_bytes()))
        if pairs:
            workspace.upload_many(pairs)
