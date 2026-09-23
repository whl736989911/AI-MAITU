"""Unit tests for the shipped plugin catalog (Admin plugin market source)."""

from __future__ import annotations

import pytest

from octop.infra.agents.plugins.catalog import (
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
