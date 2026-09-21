"""Tests for the disk-declared enterprise feature catalog."""

from __future__ import annotations

import json
from pathlib import Path

from octop.infra.features.catalog import (
    Feature,
    FeatureCatalog,
    build_user_prompt,
    default_library_root,
)
from octop.infra.features.schema import validate_manifest


def _manifest(feature_id: str, **overrides: object) -> dict:
    """A minimal valid manifest; *overrides* replace top-level keys."""
    payload: dict = {
        "id": feature_id,
        "version": 1,
        "label": {"zh": "测试功能", "en": "Test feature"},
        "description": {"zh": "测试用功能", "en": "Feature used by tests"},
        "icon_name": "file-text",
        "unit": "general",
        "input_schema": {
            "type": "object",
            "required": ["topic"],
            "properties": {
                "topic": {
                    "type": "string",
                    "format": "textarea",
                    "title": {"zh": "主题", "en": "Topic"},
                },
                "note": {"type": "string", "title": {"zh": "备注", "en": "Note"}},
            },
        },
        "ui_schema": {"order": ["note", "topic"]},
        "prompt": {"user_template": "整理以下输入：\n{{inputs}}"},
        "output": {"kind": "markdown"},
    }
    payload.update(overrides)
    return payload


def _write_feature(
    root: Path,
    feature_id: str,
    payload: dict | None = None,
    *,
    prompt_file: str | None = None,
) -> Path:
    feature_dir = root / feature_id
    feature_dir.mkdir(parents=True)
    manifest = payload if payload is not None else _manifest(feature_id)
    (feature_dir / "feature.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    if prompt_file is not None:
        (feature_dir / "PROMPT.md").write_text(prompt_file, encoding="utf-8")
    return feature_dir


def _load_feature(tmp_path: Path, **overrides: object) -> Feature:
    _write_feature(tmp_path, "demo", _manifest("demo", **overrides))
    feature = FeatureCatalog(tmp_path).get("demo")
    assert feature is not None
    return feature


def test_catalog_sorts_by_unit_then_label(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "zebra",
        _manifest("zebra", label={"zh": "Alpha 功能", "en": "Alpha feature"}),
    )
    _write_feature(
        tmp_path,
        "ant",
        _manifest("ant", label={"zh": "Zulu 功能", "en": "Zulu feature"}),
    )
    _write_feature(tmp_path, "mid", _manifest("mid", unit="sales"))

    catalog = FeatureCatalog(tmp_path)

    # Same unit: label decides (not the id); units stay grouped.
    assert [feature.id for feature in catalog.list()] == ["zebra", "ant", "mid"]
    assert catalog.warnings() == []


def test_catalog_reads_declared_fields(tmp_path: Path) -> None:
    payload = _manifest(
        "notes",
        color="#123456",
        permissions={"allow_roles": ["member"]},
        prompt={"system_file": "PROMPT.md", "user_template": "{{inputs}}"},
    )
    _write_feature(tmp_path, "notes", payload, prompt_file="# 系统提示\n按模板输出")

    catalog = FeatureCatalog(tmp_path)
    feature = catalog.get("notes")

    assert feature is not None
    assert feature.version == 1
    assert feature.icon_name == "file-text"
    assert feature.color == "#123456"
    assert feature.description == payload["description"]
    assert feature.permissions == {"allow_roles": ["member"]}
    assert feature.system_prompt == "# 系统提示\n按模板输出"
    assert feature.output_kind == "markdown"
    assert catalog.get("missing") is None


def test_catalog_skips_invalid_definitions_and_keeps_valid_ones(tmp_path: Path) -> None:
    _write_feature(tmp_path, "good", _manifest("good"))
    _write_feature(tmp_path, "bad-kind", _manifest("bad-kind", output={"kind": "pdf"}))
    broken = tmp_path / "broken-json"
    broken.mkdir()
    (broken / "feature.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "no-manifest").mkdir()

    catalog = FeatureCatalog(tmp_path)

    assert [feature.id for feature in catalog.list()] == ["good"]
    warnings = catalog.warnings()
    assert len(warnings) == 2
    assert any(
        warning.startswith("bad-kind/feature.json: ") and "output.kind" in warning
        for warning in warnings
    )
    assert any(
        warning.startswith("broken-json/feature.json: invalid JSON") for warning in warnings
    )


def test_missing_system_file_skips_definition(tmp_path: Path) -> None:
    _write_feature(
        tmp_path,
        "ghost",
        _manifest("ghost", prompt={"system_file": "PROMPT.md", "user_template": "{{inputs}}"}),
    )

    catalog = FeatureCatalog(tmp_path)

    assert catalog.get("ghost") is None
    assert "prompt.system_file" in catalog.warnings()[0]


def test_reload_rescans_library(tmp_path: Path) -> None:
    _write_feature(tmp_path, "first", _manifest("first"))
    catalog = FeatureCatalog(tmp_path)
    assert [feature.id for feature in catalog.list()] == ["first"]

    _write_feature(tmp_path, "second", _manifest("second"))
    assert [feature.id for feature in catalog.list()] == ["first"]

    catalog.reload()
    assert [feature.id for feature in catalog.list()] == ["first", "second"]


def test_validate_manifest_reports_structural_problems() -> None:
    errors = validate_manifest(_manifest("quote-draft"), "other-dir")
    assert errors == ["id 'quote-draft' must match the directory name 'other-dir'"]

    broken = _manifest(
        "x",
        input_schema={
            "type": "object",
            "properties": {"rows": {"type": "object"}},
            "extra": True,
        },
    )
    errors = validate_manifest(broken)
    assert any("input_schema uses unsupported keys: 'extra'" == error for error in errors)
    assert any("input_schema.properties.rows.type" in error for error in errors)

    assert validate_manifest(_manifest("x", label="hello")) == [
        "label must be an object with 'zh' and 'en' keys"
    ]


def test_validate_manifest_accepts_bundled_library() -> None:
    root = default_library_root()
    assert root.name == "library"

    catalog = FeatureCatalog(root)

    assert {"meeting-notes", "quote-draft"} <= {feature.id for feature in catalog.list()}
    assert catalog.warnings() == []


def test_build_user_prompt_substitutes_known_placeholders_only(tmp_path: Path) -> None:
    feature = _load_feature(
        tmp_path,
        prompt={
            "user_template": "输入：\n{{inputs}}\nJSON：\n{{inputs_json}}\n未知：{{unknown}}",
        },
    )

    prompt = build_user_prompt(feature, {"topic": "周会", "note": "待跟进"})

    # ui_schema.order wins over the declaration order; CJK values pick zh titles.
    assert "- 备注：待跟进\n- 主题：周会" in prompt
    assert '"note": "待跟进"' in prompt
    assert "未知：{{unknown}}" in prompt
    assert "{{inputs}}" not in prompt


def test_build_user_prompt_uses_english_labels_for_ascii_values(tmp_path: Path) -> None:
    feature = _load_feature(tmp_path)

    prompt = build_user_prompt(feature, {"topic": "Weekly sync", "note": "follow up"})

    assert "- Note: follow up\n- Topic: Weekly sync" in prompt


def test_build_user_prompt_renders_rows_and_skips_blank_fields(tmp_path: Path) -> None:
    feature = _load_feature(
        tmp_path,
        input_schema={
            "type": "object",
            "required": ["rows"],
            "properties": {
                "rows": {
                    "type": "array",
                    "title": {"zh": "明细", "en": "Rows"},
                    "items": {"type": "array", "items": {"type": "string"}},
                }
            },
        },
        ui_schema={"order": ["rows"]},
    )

    prompt = build_user_prompt(feature, {"rows": [["笔记本", "2", "6500"], ["显示器", "3", "1200"]]})

    assert "- 明细：\n  - 笔记本 | 2 | 6500\n  - 显示器 | 3 | 1200" in prompt
    assert build_user_prompt(feature, {"rows": []}).strip() == "整理以下输入："
