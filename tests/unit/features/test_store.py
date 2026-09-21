"""Tests for writing feature definitions into the user's own directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octop.infra.features import (
    FeatureAlreadyExists,
    FeatureCatalog,
    FeatureDefinitionInvalid,
    FeatureNotFound,
    FeatureReadOnly,
    FeatureStore,
)

BUNDLED_ID = "meeting-notes"
SYSTEM_PROMPT = "Write the summary in three sections."


def _manifest(feature_id: str, *, unit: str = "general", **overrides: Any) -> dict[str, Any]:
    """A minimal valid manifest for a hand-written (bundled or overlay) feature."""
    payload: dict[str, Any] = {
        "id": feature_id,
        "version": 1,
        "label": {"zh": f"{feature_id}·中文", "en": f"{feature_id} en"},
        "description": {"zh": "说明", "en": "description"},
        "icon_name": "file-text",
        "unit": unit,
        "input_schema": {
            "type": "object",
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "title": {"zh": "主题", "en": "Topic"}},
            },
        },
        "prompt": {"user_template": "整理：{{inputs}}"},
        "output": {"kind": "markdown"},
    }
    payload.update(overrides)
    return payload


def _write_feature(root: Path, feature_id: str, payload: dict[str, Any] | None = None) -> Path:
    """Drop a definition on disk the way the bundled library ships one."""
    directory = root / feature_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest = payload if payload is not None else _manifest(feature_id)
    (directory / "feature.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return directory


def _definition(feature_id: str = "weekly-report", **overrides: Any) -> dict[str, Any]:
    """A create/update request body as the settings UI sends it."""
    payload: dict[str, Any] = {
        "id": feature_id,
        "label": {"zh": "周报", "en": "Weekly report"},
        "description": {"zh": "把零散记录整理成周报", "en": "Turn notes into a weekly report"},
        "icon_name": "clipboard-list",
        "unit": "general",
        "input_schema": {
            "type": "object",
            "required": ["notes"],
            "properties": {
                "notes": {
                    "type": "string",
                    "format": "textarea",
                    "title": {"zh": "记录", "en": "Notes"},
                },
            },
        },
        "ui_schema": {"order": ["notes"], "widgets": {"notes": "textarea"}},
        "prompt": {"user_template": "整理成周报：\n{{inputs}}", "system_prompt": SYSTEM_PROMPT},
        "output": {"kind": "markdown"},
        "permissions": {"allow_units": ["*"]},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    return tmp_path / "features"


@pytest.fixture
def catalog(tmp_path: Path, store_root: Path) -> FeatureCatalog:
    """Catalog over a bundled library plus the writable overlay root."""
    library = tmp_path / "library"
    _write_feature(library, BUNDLED_ID)
    catalog = FeatureCatalog(library, extra_roots=[store_root])
    catalog.reload()
    return catalog


@pytest.fixture
def store(store_root: Path, catalog: FeatureCatalog) -> FeatureStore:
    return FeatureStore(store_root, catalog)


def test_create_writes_manifest_and_prompt_into_the_user_directory(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    """One create: files on disk, then served by the catalog right after."""
    assert store.create(_definition()) == "weekly-report"

    feature_dir = store_root / "weekly-report"
    manifest = json.loads((feature_dir / "feature.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "weekly-report"
    assert manifest["version"] == 1
    assert manifest["unit"] == "general"
    assert manifest["prompt"] == {
        "system_file": "PROMPT.md",
        "user_template": "整理成周报：\n{{inputs}}",
    }
    assert (feature_dir / "PROMPT.md").read_text(encoding="utf-8") == SYSTEM_PROMPT

    loaded = catalog.get("weekly-report")
    assert loaded is not None
    assert loaded.label == {"zh": "周报", "en": "Weekly report"}
    assert loaded.icon_name == "clipboard-list"
    assert loaded.user_template == "整理成周报：\n{{inputs}}"
    assert loaded.system_prompt == SYSTEM_PROMPT
    assert loaded.ui_schema == {"order": ["notes"], "widgets": {"notes": "textarea"}}
    assert loaded.permissions == {"allow_units": ["*"]}
    assert catalog.warnings() == []


def test_create_without_a_system_prompt_writes_no_prompt_file(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    definition = _definition(prompt={"user_template": "{{inputs}}"})
    store.create(definition)

    feature_dir = store_root / "weekly-report"
    assert not (feature_dir / "PROMPT.md").exists()
    manifest = json.loads((feature_dir / "feature.json").read_text(encoding="utf-8"))
    assert manifest["prompt"] == {"user_template": "{{inputs}}"}
    loaded = catalog.get("weekly-report")
    assert loaded is not None and loaded.system_prompt is None


def test_capability_layer_is_written_as_declared(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    """The ``agent`` node lands in ``feature.json`` and reads back as authored."""
    layer = {
        "model": "openai/gpt-4o",
        "temperature": 0.3,
        "tools_disabled": ["browser_use"],
        "skills": [],
        "subagents": ["researcher"],
        "mcp_servers": ["github"],
        "knowledge_base_ids": ["kb-1"],
    }

    store.create(_definition(agent=layer))

    manifest = json.loads(
        (store_root / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )
    assert manifest["agent"] == layer
    loaded = catalog.get("weekly-report")
    assert loaded is not None and loaded.agent is not None
    assert loaded.agent.as_dict() == layer
    assert loaded.agent.runtime_values() == {"temperature": 0.3}


def test_capability_layer_omits_what_was_never_declared(
    store: FeatureStore,
    store_root: Path,
) -> None:
    """A cleared key disappears rather than sitting in the file as ``null``.

    ``null`` and absent read the same way here, but only one of them tells the
    next reader that nothing was declared — the other looks like a value.
    """
    store.create(_definition(agent={"model": "openai/gpt-4o", "temperature": None, "skills": None}))

    manifest = json.loads(
        (store_root / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )
    assert manifest["agent"] == {"model": "openai/gpt-4o"}


def test_a_definition_without_a_capability_layer_writes_no_agent_node(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    """An untouched definition keeps the file it started from."""
    store.create(_definition())
    assert "agent" not in json.loads(
        (store_root / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )

    # A write is a full replace: an update that declares no layer clears the one
    # an earlier save had, rather than leaving a stale scope in place.
    store.update("weekly-report", _definition(unit="support"))
    cleared = json.loads(
        (store_root / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )
    assert "agent" not in cleared
    loaded = catalog.get("weekly-report")
    assert loaded is not None and loaded.agent is None


def test_invalid_definition_is_refused_and_nothing_is_written(
    store: FeatureStore,
    store_root: Path,
) -> None:
    """The whole point of validating first: a refused definition leaves no trace."""
    definition = _definition(
        input_schema={"type": "string", "properties": {"a": {"type": "string"}}}
    )

    with pytest.raises(FeatureDefinitionInvalid) as excinfo:
        store.create(definition)

    assert "input_schema.type must be 'object'" in str(excinfo.value)
    assert not (store_root / "weekly-report").exists()
    assert store.read_only_ids() == [BUNDLED_ID]


def test_every_problem_is_reported_in_one_refusal(store: FeatureStore) -> None:
    definition = _definition(
        unit="",
        label={"zh": "周报"},
        prompt={"user_template": "", "system_prompt": 42},
        output={"kind": "pdf"},
    )

    with pytest.raises(FeatureDefinitionInvalid) as excinfo:
        store.create(definition)

    assert len(excinfo.value.errors) >= 5


@pytest.mark.parametrize("feature_id", ["../escape", "a/b", "Upper", "", "x" * 65, ".hidden"])
def test_create_requires_a_directory_safe_id(
    store: FeatureStore,
    store_root: Path,
    tmp_path: Path,
    feature_id: str,
) -> None:
    """The id becomes a directory name, so a separator or a dot must not pass."""
    with pytest.raises(FeatureDefinitionInvalid):
        store.create(_definition(feature_id))

    assert not store_root.exists() or list(store_root.iterdir()) == []
    assert not (tmp_path / "escape").exists()


def test_create_refuses_an_id_the_catalog_already_serves(
    store: FeatureStore,
    catalog: FeatureCatalog,
    tmp_path: Path,
) -> None:
    """A shipped feature is not shadowed by a second definition with its id."""
    with pytest.raises(FeatureAlreadyExists):
        store.create(_definition(BUNDLED_ID))

    bundled = tmp_path / "library" / BUNDLED_ID / "feature.json"
    assert json.loads(bundled.read_text(encoding="utf-8"))["unit"] == "general"
    assert catalog.get(BUNDLED_ID) is not None


def test_update_and_delete_refuse_a_bundled_definition(
    store: FeatureStore,
    tmp_path: Path,
) -> None:
    """Bundled definitions are not copied into the overlay either — just refused."""
    bundled = tmp_path / "library" / BUNDLED_ID / "feature.json"
    before = bundled.read_text(encoding="utf-8")

    with pytest.raises(FeatureReadOnly):
        store.update(BUNDLED_ID, _definition(BUNDLED_ID))
    with pytest.raises(FeatureReadOnly):
        store.delete(BUNDLED_ID)

    assert bundled.read_text(encoding="utf-8") == before
    assert (tmp_path / "library" / BUNDLED_ID).is_dir()


def test_read_only_ids_are_exactly_the_ids_writes_are_refused_for(
    store: FeatureStore,
    catalog: FeatureCatalog,
) -> None:
    store.create(_definition())

    assert store.read_only_ids() == [BUNDLED_ID]
    assert store.is_writable("weekly-report") is True
    assert store.is_writable(BUNDLED_ID) is False
    assert store.is_writable("nope") is False
    with pytest.raises(FeatureReadOnly):
        store.delete(BUNDLED_ID)
    with pytest.raises(FeatureNotFound):
        store.delete("nope")


def test_user_definition_overrides_the_bundled_one_and_edits_stay_there(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
    tmp_path: Path,
) -> None:
    """An overlay wins on the id; updating it must not touch the shipped copy."""
    overlay = _write_feature(store_root, BUNDLED_ID, _manifest(BUNDLED_ID, unit="sales"))
    (overlay / "PROMPT.md").write_text("Hand-written overlay prompt.", encoding="utf-8")
    catalog.reload()
    bundled = tmp_path / "library" / BUNDLED_ID / "feature.json"
    before = bundled.read_text(encoding="utf-8")

    overlaid = catalog.get(BUNDLED_ID)
    assert overlaid is not None
    assert overlaid.unit == "sales"
    assert catalog.is_bundled(BUNDLED_ID) is False
    assert store.read_only_ids() == []

    store.update(BUNDLED_ID, _definition(BUNDLED_ID, unit="support"))

    assert bundled.read_text(encoding="utf-8") == before
    edited = json.loads((overlay / "feature.json").read_text(encoding="utf-8"))
    assert edited["unit"] == "support"
    assert (overlay / "PROMPT.md").read_text(encoding="utf-8") == SYSTEM_PROMPT
    loaded = catalog.get(BUNDLED_ID)
    assert loaded is not None
    assert loaded.unit == "support"
    assert loaded.system_prompt == SYSTEM_PROMPT


def test_delete_removes_the_overlay_and_restores_the_bundled_definition(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    _write_feature(store_root, BUNDLED_ID, _manifest(BUNDLED_ID, unit="sales"))
    catalog.reload()

    store.delete(BUNDLED_ID)

    assert not (store_root / BUNDLED_ID).exists()
    loaded = catalog.get(BUNDLED_ID)
    assert loaded is not None
    assert loaded.unit == "general"
    assert catalog.is_bundled(BUNDLED_ID) is True


def test_update_clears_the_prompt_file_when_the_prompt_is_removed(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    assert store.create(_definition()) == "weekly-report"
    feature_dir = store_root / "weekly-report"

    store.update("weekly-report", _definition(prompt={"user_template": "{{inputs}}"}))

    assert not (feature_dir / "PROMPT.md").exists()
    manifest = json.loads((feature_dir / "feature.json").read_text(encoding="utf-8"))
    assert "system_file" not in manifest["prompt"]
    loaded = catalog.get("weekly-report")
    assert loaded is not None and loaded.system_prompt is None


def test_update_normalises_a_hand_written_prompt_file_name(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    """The API only ever expresses ``PROMPT.md``; a PUT rewrites the reference."""
    overlay = _write_feature(
        store_root,
        "weekly-report",
        _manifest(
            "weekly-report",
            prompt={"system_file": "CUSTOM.md", "user_template": "{{inputs}}"},
        ),
    )
    (overlay / "CUSTOM.md").write_text("Custom file.", encoding="utf-8")
    catalog.reload()
    assert catalog.get("weekly-report") is not None

    store.update("weekly-report", _definition())

    manifest = json.loads((overlay / "feature.json").read_text(encoding="utf-8"))
    assert manifest["prompt"]["system_file"] == "PROMPT.md"
    assert (overlay / "PROMPT.md").read_text(encoding="utf-8") == SYSTEM_PROMPT
    loaded = catalog.get("weekly-report")
    assert loaded is not None and loaded.system_prompt == SYSTEM_PROMPT


def test_update_refuses_a_body_id_that_disagrees_with_the_addressed_feature(
    store: FeatureStore,
) -> None:
    store.create(_definition())

    with pytest.raises(FeatureDefinitionInvalid) as excinfo:
        store.update("weekly-report", _definition("something-else"))

    assert "must match the addressed feature" in str(excinfo.value)


def test_unknown_keys_are_refused_instead_of_dropped(store: FeatureStore) -> None:
    """A typo must not silently lose a field the author believed was saved."""
    with pytest.raises(FeatureDefinitionInvalid) as excinfo:
        store.create(_definition(system_prompt="top level, not under prompt"))

    assert "unsupported keys: 'system_prompt'" in str(excinfo.value)


def test_update_unknown_id_is_not_found(store: FeatureStore) -> None:
    with pytest.raises(FeatureNotFound):
        store.update("nope", _definition("nope"))


def test_removing_a_feature_deletes_its_prompt_file_too(
    store: FeatureStore,
    store_root: Path,
    catalog: FeatureCatalog,
) -> None:
    store.create(_definition())

    store.delete("weekly-report")

    assert not (store_root / "weekly-report").exists()
    assert catalog.get("weekly-report") is None
