"""Unit tests for Octop plugin manager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octop_harness.plugins import PluginRegistry

from octop.infra.agents.plugins import manager as manager_module
from octop.infra.agents.plugins.manager import (
    PluginManager,
    parse_plugin_group,
    parse_plugin_icon,
    parse_plugin_ui_meta,
)
from octop.infra.errors import ErrorCode, OctopError

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo-tool"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


def test_install_rejects_unshipped_code(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    with pytest.raises(OctopError) as excinfo:
        mgr.install_path(_FIXTURE, force=True)
    assert excinfo.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not (tmp_path / "plugins" / "echo-tool").exists()


def test_unshipped_plugins_are_not_loaded_or_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    dest = plugins_dir / "echo-tool"
    dest.mkdir(parents=True)
    for source in _FIXTURE.rglob("*"):
        if source.is_file():
            target = dest / source.relative_to(_FIXTURE)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    with pytest.raises(OctopError) as excinfo:
        mgr.load_installed(install_deps=True)
    assert excinfo.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert mgr.load_installed(install_deps=False) == []
    assert mgr.load_missing(install_deps=False) == []
    assert PluginRegistry().get("echo-tool") is None
    with pytest.raises(OctopError) as excinfo:
        mgr.set_enabled("echo-tool", True)
    assert excinfo.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


def test_shipped_bundle_is_installed_and_tampering_prevents_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    row = mgr.install_bundled("fun-facts")
    assert row["id"] == "fun-facts"
    assert row["enabled"] is True
    installed = tmp_path / "plugins" / "fun-facts"
    main_py = installed / "main.py"
    main_py.write_text(
        main_py.read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )
    assert all(
        plugin.manifest.id != "fun-facts" for plugin in mgr.load_installed(install_deps=False)
    )
    assert PluginRegistry().get("fun-facts") is None


def test_load_installed_clears_stale_registry_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    initial = PluginManager(
        plugins_dir=tmp_path / "stale_plugins",
        config_path=config_path,
    )
    initial.install_bundled("fun-facts")
    assert initial.load_installed(install_deps=False)
    assert PluginRegistry().get("fun-facts") is not None

    replacement = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    replacement.install_bundled("weather")
    loaded = replacement.load_installed(install_deps=False)

    assert [plugin.manifest.id for plugin in loaded] == ["weather"]
    assert PluginRegistry().get("fun-facts") is None
    assert PluginRegistry().get("weather") is not None


def test_load_missing_does_not_clear_existing_registry_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    mgr.install_bundled("fun-facts")
    assert mgr.load_installed(install_deps=False)

    assert mgr.load_missing(install_deps=False) == []
    assert PluginRegistry().get("fun-facts") is not None


def test_archive_and_url_plugin_installation_are_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    for install in (
        lambda: mgr.install_url("https://example.com/plugin.zip"),
        lambda: mgr.install_archive(tmp_path / "plugin.zip"),
    ):
        with pytest.raises(OctopError) as excinfo:
            install()
        assert excinfo.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


def test_unshipped_path_cannot_be_installed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)

    with pytest.raises(OctopError) as excinfo:
        mgr.install_path(_FIXTURE, force=False)
    assert excinfo.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED


def test_parse_plugin_ui_meta_and_list(tmp_path: Path) -> None:
    from octop.infra.agents.plugins.bundled import default_bundled_plugins_root

    shipped = default_bundled_plugins_root() / "weather"
    assert parse_plugin_ui_meta(shipped) == {"entry": "ui/index.js", "manifest": "ui/manifest.json"}

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    mgr = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config_path)
    mgr.install_bundled("weather")
    row = next(item for item in mgr.list_installed() if item.get("id") == "weather")
    assert row["ui"] == {"entry": "ui/index.js", "manifest": "ui/manifest.json"}
    assert mgr.resolve_ui_file("weather", "index.js").name == "index.js"
    with pytest.raises(OctopError) as excinfo:
        mgr.resolve_ui_file("weather", "../plugin.yaml")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_load_missing_skips_unshipped_cli_install(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    dest = plugins_dir / "echo-tool"
    dest.mkdir(parents=True)
    for source in _FIXTURE.rglob("*"):
        if source.is_file():
            target = dest / source.relative_to(_FIXTURE)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    mgr = PluginManager(plugins_dir=plugins_dir, config_path=config_path)
    assert mgr.load_missing(install_deps=False) == []
    assert PluginRegistry().get("echo-tool") is None


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
    mgr.install_bundled("weather")

    corrupt = '{"bind_host": "0.0.0.0", "database": {"driver": "postgresql"},}'
    config_path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(OctopError) as excinfo:
        mgr.set_enabled("weather", False)
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
    mgr.install_bundled("weather")
    mgr.set_enabled("weather", False)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["bind_host"] == "0.0.0.0"
    assert data["database"] == {"driver": "postgresql", "host": "db.internal"}
    assert data["plugins"]["weather"]["enabled"] is False


@pytest.mark.parametrize("registered_before_failure", [False, True])
def test_failed_enable_keeps_previous_config_and_removes_new_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered_before_failure: bool
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"unrelated": "preserved"}', encoding="utf-8")
    manager = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config)
    manager.install_bundled("weather")
    manager.set_enabled("weather", False)
    previous = config.read_bytes()
    original_load = manager_module.load_plugin_dir

    def fail_load(*args, **kwargs):
        if registered_before_failure:
            original_load(*args, **kwargs)
        raise RuntimeError("activation failed")

    monkeypatch.setattr(manager_module, "load_plugin_dir", fail_load)
    with pytest.raises(RuntimeError, match="activation failed"):
        manager.set_enabled("weather", True)
    assert config.read_bytes() == previous
    assert manager.global_enabled_map()["weather"] is False
    assert PluginRegistry().get("weather") is None


@pytest.mark.parametrize("already_loaded", [False, True])
def test_enable_config_failure_restores_previous_runtime_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_loaded: bool
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    manager = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config)
    manager.install_bundled("weather")
    if not already_loaded:
        manager.set_enabled("weather", False)
    previous_config = config.read_bytes()
    previous_plugin = PluginRegistry().get("weather")

    def fail_save(*_args, **_kwargs):
        raise OSError("configuration save failed")

    monkeypatch.setattr(manager_module, "write_json_atomic", fail_save)
    with pytest.raises(OSError, match="configuration save failed"):
        manager.set_enabled("weather", True)
    assert config.read_bytes() == previous_config
    assert PluginRegistry().get("weather") is previous_plugin


@pytest.mark.parametrize("failure", ["copy", "publish"])
def test_forced_install_preserves_previous_files_and_runtime_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from octop.infra.agents.plugins.catalog import get_catalog_plugin

    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    manager = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config)
    manager.install_bundled("weather")
    source = get_catalog_plugin("weather").source_dir
    destination = manager.plugins_dir / "weather"
    previous_entry = (destination / "main.py").read_bytes()
    previous_config = config.read_bytes()
    previous_plugin = PluginRegistry().get("weather")
    if failure == "copy":
        original_copy = manager_module.shutil.copytree

        def fail_copy(src, dst, *args, **kwargs):
            if Path(src) == source:
                Path(dst).mkdir(parents=True)
                (Path(dst) / "plugin.yaml").write_bytes((source / "plugin.yaml").read_bytes())
                raise OSError("copy failed")
            return original_copy(src, dst, *args, **kwargs)

        monkeypatch.setattr(manager_module.shutil, "copytree", fail_copy)
    else:
        original_replace = manager_module.os.replace
        failed = False

        def fail_publish(src, dst):
            nonlocal failed
            if Path(dst) == destination and not failed:
                failed = True
                raise OSError("publish failed")
            return original_replace(src, dst)

        monkeypatch.setattr(manager_module.os, "replace", fail_publish)
    with pytest.raises(OSError):
        manager.install_path(source, force=True)
    assert (destination / "main.py").read_bytes() == previous_entry
    assert config.read_bytes() == previous_config
    assert PluginRegistry().get("weather") is previous_plugin


@pytest.mark.parametrize("load_method", ["load_installed", "load_missing"])
def test_corrupt_config_does_not_activate_a_disabled_plugin(
    tmp_path: Path, load_method: str
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    manager = PluginManager(plugins_dir=tmp_path / "plugins", config_path=config)
    manager.install_bundled("weather")
    manager.set_enabled("weather", False)
    corrupt = b'{"plugins":'
    config.write_bytes(corrupt)

    with pytest.raises(OctopError) as raised:
        getattr(manager, load_method)(install_deps=False)
    assert raised.value.code is ErrorCode.CONFIG_FILE_CORRUPT
    assert PluginRegistry().get("weather") is None
    assert config.read_bytes() == corrupt
