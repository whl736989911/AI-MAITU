"""Unit tests for Octop plugin manager."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from harness_agent.plugins import PluginRegistry

from octop.infra.agents.plugins.bundled import default_bundled_plugins_root
from octop.infra.agents.plugins.manager import (
    PluginManager,
    parse_plugin_group,
    parse_plugin_icon,
    parse_plugin_ui_meta,
)
from octop.infra.errors import ErrorCode, OctopError

_SHIPPED_ID = "fortune"
_SHIPPED = default_bundled_plugins_root() / _SHIPPED_ID


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


def test_install_and_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    loaded = mgr.install_path(_SHIPPED, force=True)
    assert loaded.manifest.id == _SHIPPED_ID
    items = mgr.list_installed()
    assert any(i.get("id") == _SHIPPED_ID for i in items)


def test_global_disable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"plugins": {_SHIPPED_ID: {"enabled": False}}}),
        encoding="utf-8",
    )
    plugins_dir = tmp_path / "plugins"
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    mgr.install_path(_SHIPPED, force=True)
    loaded = mgr.load_installed(install_deps=False)
    assert loaded == []


def test_set_enabled_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    mgr.install_path(_SHIPPED, force=True)
    assert any(i.get("id") == _SHIPPED_ID and i.get("enabled") for i in mgr.list_installed())

    disabled = mgr.set_enabled(_SHIPPED_ID, False)
    assert disabled.get("enabled") is False
    assert disabled.get("loaded") is False
    assert PluginRegistry().get(_SHIPPED_ID) is None
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["plugins"][_SHIPPED_ID]["enabled"] is False

    enabled = mgr.set_enabled(_SHIPPED_ID, True)
    assert enabled.get("enabled") is True
    assert enabled.get("loaded") is True
    assert PluginRegistry().get(_SHIPPED_ID) is not None


def test_external_plugin_sources_are_rejected_before_execution(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    original_config = '{"unchanged": true}'
    config_path.write_text(original_config, encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)

    outside = tmp_path / "custom-plugin"
    outside.mkdir()
    (outside / "plugin.yaml").write_text(
        "id: custom-plugin\nversion: 0.1.0\nname: Custom\nkind: tool\nentry: main.py\n",
        encoding="utf-8",
    )
    marker = tmp_path / "executed"
    (outside / "main.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef setup(ctx): pass\n",
        encoding="utf-8",
    )
    archive = tmp_path / "custom-plugin.zip"
    archive.write_bytes(b"not inspected")

    for operation in (
        lambda: mgr.install_path(outside),
        lambda: mgr.install_archive(archive),
        lambda: mgr.install_url("https://example.com/custom-plugin.zip"),
    ):
        with pytest.raises(OctopError):
            operation()

    assert not marker.exists()
    assert config_path.read_text(encoding="utf-8") == original_config


def test_install_path_already_exists(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    mgr.install_path(_SHIPPED, force=True)
    with pytest.raises(OctopError) as excinfo:
        mgr.install_path(_SHIPPED, force=False)
    assert excinfo.value.code is ErrorCode.PLUGIN_ALREADY_EXISTS
    assert excinfo.value.status == 409
    assert excinfo.value.details.get("id") == _SHIPPED_ID


def test_parse_plugin_ui_meta_and_list(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "with-ui"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: with-ui",
                "version: 0.1.0",
                "name: With UI",
                "kind: tool",
                "entry: main.py",
                "ui:",
                "  entry: ui/dist/index.js",
                "  manifest: ui/dist/manifest.json",
            ],
        ),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "def setup(ctx):\n    pass\n",
        encoding="utf-8",
    )
    dist = plugin_dir / "ui" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.js").write_text("export function setup() {}", encoding="utf-8")
    (dist / "manifest.json").write_text("{}", encoding="utf-8")

    assert parse_plugin_ui_meta(plugin_dir) == {
        "entry": "ui/dist/index.js",
        "manifest": "ui/dist/manifest.json",
    }

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    shutil.copytree(plugin_dir, plugins_dir / "with-ui")
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    items = mgr.list_installed()
    assert any(row.get("id") == "with-ui" for row in items)
    resolved = mgr.resolve_ui_file("with-ui", "dist/index.js")
    assert resolved.name == "index.js"
    with pytest.raises(OctopError) as excinfo:
        mgr.resolve_ui_file("with-ui", "../plugin.yaml")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_load_missing_picks_up_bundled_plugin(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(_SHIPPED, plugins_dir / _SHIPPED_ID)
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    assert PluginRegistry().get(_SHIPPED_ID) is None
    newly = mgr.load_missing(install_deps=False)
    assert len(newly) == 1
    assert newly[0].manifest.id == _SHIPPED_ID
    assert PluginRegistry().get(_SHIPPED_ID) is not None
    assert mgr.load_missing(install_deps=False) == []


def test_parse_plugin_ui_meta_missing_entry(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "no-ui-file"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: no-ui-file",
                "version: 0.1.0",
                "name: No UI File",
                "kind: tool",
                "entry: main.py",
                "ui:",
                "  entry: ui/dist/index.js",
            ],
        ),
        encoding="utf-8",
    )
    assert parse_plugin_ui_meta(plugin_dir) is None


def test_parse_plugin_icon(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "with-icon"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: with-icon",
                "version: 0.1.0",
                "name: With Icon",
                "kind: tool",
                "entry: main.py",
                'icon: "🧩"',
            ],
        ),
        encoding="utf-8",
    )
    assert parse_plugin_icon(plugin_dir) == "🧩"
    assert parse_plugin_icon(tmp_path / "missing") is None


def test_relative_icon_group_and_market_asset_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.agents.plugins import bundled as bundled_plugins

    market = tmp_path / "bundled"
    plugin_dir = market / "with-icon"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: with-icon",
                "version: 0.1.0",
                "name: With Icon",
                "kind: tool",
                "entry: main.py",
                "group: Some_Group",
                "icon: icon.svg",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bundled_plugins, "default_bundled_plugins_root", lambda: market)

    assert parse_plugin_group(plugin_dir) == "some-group"
    assert parse_plugin_icon(plugin_dir) == "/api/plugins/with-icon/ui/icon.svg"
    assert (
        parse_plugin_icon(
            plugin_dir,
            asset_prefix="/api/plugins/market/with-icon/ui",
        )
        == "/api/plugins/market/with-icon/ui/icon.svg"
    )

    mgr = PluginManager(plugins_dir=tmp_path / "installed", config_path=tmp_path / "config.json")
    assert mgr.resolve_market_ui_file("with-icon", "icon.svg") == plugin_dir / "icon.svg"
    with pytest.raises(OctopError) as excinfo:
        mgr.resolve_market_ui_file("with-icon", "../plugin.yaml")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_set_enabled_refuses_corrupt_config_and_preserves_bytes(tmp_path: Path) -> None:
    """issue #730: the dashboard toggle must not wipe an unparseable config.json."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    mgr.install_path(_SHIPPED, force=True)

    corrupt = '{"bind_host": "0.0.0.0", "database": {"driver": "postgresql"},}'
    config_path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(OctopError) as excinfo:
        mgr.set_enabled(_SHIPPED_ID, False)
    assert excinfo.value.code is ErrorCode.CONFIG_FILE_CORRUPT
    assert config_path.read_text(encoding="utf-8") == corrupt


def test_set_enabled_preserves_unrelated_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bind_host": "0.0.0.0",
                "database": {"driver": "postgresql", "host": "db.internal"},
            }
        ),
        encoding="utf-8",
    )
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    mgr.install_path(_SHIPPED, force=True)
    mgr.set_enabled(_SHIPPED_ID, False)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["bind_host"] == "0.0.0.0"
    assert data["database"] == {"driver": "postgresql", "host": "db.internal"}
    assert data["plugins"][_SHIPPED_ID]["enabled"] is False
