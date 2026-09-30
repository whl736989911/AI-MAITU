"""Unit tests for the shipped plugin catalog (Admin plugin market source)."""

from __future__ import annotations

import pytest

from octop.infra.agents.plugins.catalog import (
    _entry_for,
    get_catalog_plugin,
    list_catalog_plugins,
)
from octop.infra.errors import ErrorCode, OctopError


def test_catalog_lists_shipped_plugins_with_both_locales() -> None:
    entries = list_catalog_plugins()
    assert entries, "the wheel ships bundled plugins"
    assert [entry.id for entry in entries] == sorted(entry.id for entry in entries)
    for entry in entries:
        # The market renders zh/en cards; a missing locale would render blank.
        assert entry.label()["zh"] and entry.label()["en"], entry.id
        assert entry.summary()["zh"] and entry.summary()["en"], entry.id
        assert entry.source_dir.is_dir()
        assert entry.kind in ("tool", "skill", "hook")


@pytest.mark.parametrize("plugin_id", ["../secrets", "", "no/slash", "unknown-plugin"])
def test_catalog_lookup_rejects_ids_outside_the_catalog(plugin_id: str) -> None:
    """Only shipped ids resolve — a traversal attempt must never reach the FS."""
    with pytest.raises(OctopError) as exc:
        get_catalog_plugin(plugin_id)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_catalog_resolves_relative_icon_and_group_for_market(tmp_path) -> None:
    plugin_dir = tmp_path / "catalog" / "sample"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: sample",
                "version: 1.0.0",
                "name: Sample",
                "description: Sample plugin",
                "kind: tool",
                "entry: main.py",
                "group: Some_Group",
                "icon: icon.svg",
            ],
        ),
        encoding="utf-8",
    )

    entry = _entry_for(plugin_dir)

    assert entry is not None
    assert entry.group == "some-group"
    assert entry.icon == "/api/plugins/market/sample/ui/icon.svg"
