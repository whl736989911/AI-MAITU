"""Tests for the disk-declared enterprise feature catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.features.catalog import (
    Feature,
    FeatureCatalog,
    build_user_prompt,
    default_library_root,
    render_step_prompt,
)
from octop.infra.features.dispatch import DEFAULT_MAX_PARALLEL
from octop.infra.features.schema import validate_manifest
from octop.infra.features.steps import Artifact


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


def _step_node(**overrides: object) -> dict:
    """One valid step declaration, as the editor writes it (see library/README.md)."""
    node: dict = {
        "id": "extract_l1",
        "name": "提取 L1 项",
        "mode": "agent",
        "output": {"name": "bom_rows", "schema": "text"},
        "prompt": "读 BOM PDF，过滤 L1。",
        "gate": "auto",
        "on_failure": "abort",
    }
    node.update(overrides)
    return node


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
    assert any(warning.startswith("broken-json/feature.json: invalid JSON") for warning in warnings)


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
    assert any(error == "input_schema uses unsupported keys: 'extra'" for error in errors)
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


def test_validate_manifest_accepts_a_capability_layer() -> None:
    """Every key the layer declares is optional, and an empty list is a scope."""
    errors = validate_manifest(
        _manifest(
            "quote-draft",
            agent={
                "model": "openai/gpt-4o",
                "temperature": 0.3,
                "top_p": 0.9,
                "max_tokens": 2048,
                "max_iters": 40,
                "max_input_length": 64_000,
                "max_parallel": 6,
                "tools_disabled": ["browser_use"],
                "skills": [],
                "subagents": ["researcher"],
                "mcp_servers": [],
                "knowledge_base_ids": ["kb-1"],
            },
        )
    )

    assert errors == []


def test_validate_manifest_reports_every_capability_problem() -> None:
    """A capability the run could not honour must not be storable in silence."""
    errors = validate_manifest(
        _manifest(
            "quote-draft",
            agent={
                "model": "gpt-4o",
                "temperature": 3,
                "max_tokens": 0,
                "max_parallel": 0,
                "skills": "meeting-notes",
                "subagents": ["writer", "writer"],
                "tools_disabled": ["no-such-tool", "task"],
                "unknown_key": True,
            },
        )
    )

    assert "agent.model must name a model as 'provider/model'" in errors
    assert "agent.temperature must be between 0 and 2" in errors
    assert "agent.max_tokens must be between 1 and inf" in errors
    assert "agent.max_parallel must be between 1 and inf" in errors
    assert "agent.skills must be an array of non-empty strings or null" in errors
    assert "agent.subagents must not repeat the same entry" in errors
    assert "agent uses unsupported keys: 'unknown_key'" in errors
    # ``normalize_tools_disabled`` drops the always-on names without a word, so a
    # stored one would look applied while doing nothing.
    assert any(error.startswith("agent.tools_disabled cannot disable") for error in errors)
    assert any("agent.tools_disabled names unknown built-in tools" in error for error in errors)


def test_validate_manifest_refuses_a_capability_layer_that_is_not_an_object() -> None:
    assert validate_manifest(_manifest("quote-draft", agent=["model"])) == [
        "agent must be an object"
    ]


def test_the_capability_layer_carries_the_feature_level_ceiling(tmp_path: Path) -> None:
    """``agent.max_parallel`` is the default a step's own ceiling overrides (7.7)."""
    feature = _load_feature(tmp_path, agent={"subagents": ["researcher"], "max_parallel": 6})

    assert feature.agent is not None
    assert feature.agent.max_parallel == 6


def test_validate_manifest_refuses_a_dispatching_step_under_an_empty_subagent_scope() -> None:
    """``subagents: []`` is the explicit "none of them": every run would refuse."""
    errors = validate_manifest(
        _manifest(
            "quote-draft",
            agent={"subagents": []},
            steps=[
                _step_node(mode="orchestrate"),
                _step_node(
                    id="second",
                    agent_role="tooling",
                    output={"name": "summary", "schema": "text"},
                ),
            ],
        )
    )

    assert [error for error in errors if error.startswith("agent.subagents")] == [
        "agent.subagents allows no subagent, but step 'extract_l1' must dispatch one "
        "(its mode or agent_role asks for a named subagent)",
        "agent.subagents allows no subagent, but step 'second' must dispatch one "
        "(its mode or agent_role asks for a named subagent)",
    ]


def test_a_step_that_dispatches_nothing_runs_under_an_empty_subagent_scope() -> None:
    """The cross-check is about *declared* dispatch: a plain agent step is fine."""
    assert (
        validate_manifest(_manifest("quote-draft", agent={"subagents": []}, steps=[_step_node()]))
        == []
    )


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

    prompt = build_user_prompt(
        feature, {"rows": [["笔记本", "2", "6500"], ["显示器", "3", "1200"]]}
    )

    assert "- 明细：\n  - 笔记本 | 2 | 6500\n  - 显示器 | 3 | 1200" in prompt
    assert build_user_prompt(feature, {"rows": []}).strip() == "整理以下输入："


def test_extra_root_overrides_the_bundled_definition(tmp_path: Path) -> None:
    """An edited feature must stay edited: the overlay is the version to serve."""
    library = tmp_path / "library"
    overlay = tmp_path / "features"
    _write_feature(library, "meeting-notes", _manifest("meeting-notes", unit="general"))
    _write_feature(overlay, "meeting-notes", _manifest("meeting-notes", unit="sales"))

    catalog = FeatureCatalog(library, extra_roots=[overlay])
    catalog.reload()

    feature = catalog.get("meeting-notes")
    assert feature is not None
    assert feature.unit == "sales"
    assert catalog.feature_dir("meeting-notes") == overlay / "meeting-notes"
    assert catalog.is_bundled("meeting-notes") is False


def test_bundled_definition_stays_when_the_overlay_is_invalid(tmp_path: Path) -> None:
    """A bad overlay is skipped like any bad definition, not a reason to vanish."""
    library = tmp_path / "library"
    overlay = tmp_path / "features"
    _write_feature(library, "meeting-notes", _manifest("meeting-notes", unit="general"))
    _write_feature(overlay, "meeting-notes", _manifest("meeting-notes", input_schema={"type": "z"}))

    catalog = FeatureCatalog(library, extra_roots=[overlay])
    catalog.reload()

    feature = catalog.get("meeting-notes")
    assert feature is not None
    assert feature.unit == "general"
    assert catalog.is_bundled("meeting-notes") is True
    assert catalog.feature_dir("meeting-notes") == library / "meeting-notes"
    assert any(warning.startswith("meeting-notes/feature.json:") for warning in catalog.warnings())


def test_missing_overlay_root_is_not_a_warning(tmp_path: Path) -> None:
    """Nothing has been saved yet on a fresh install — that is not a problem."""
    library = tmp_path / "library"
    _write_feature(library, "meeting-notes")

    catalog = FeatureCatalog(library, extra_roots=[tmp_path / "features"])
    catalog.reload()

    assert catalog.warnings() == []
    assert [feature.id for feature in catalog.list()] == ["meeting-notes"]
    assert catalog.roots == (library, tmp_path / "features")


# --- step prompts -----------------------------------------------------------


def _render_step(tmp_path: Path, locale: str = "zh", **overrides: object) -> str:
    """The prompt one declared step renders to (the step is the feature's first)."""
    feature = _load_feature(tmp_path, steps=[_step_node(**overrides)])
    return render_step_prompt(feature.steps[0], feature, {}, {}, locale=locale)


def test_a_decomposing_step_is_told_the_dispatch_tool_and_its_ceiling(tmp_path: Path) -> None:
    """7.6/7.7 stated to the model: it decomposes, the platform caps and records."""
    prompt = _render_step(tmp_path, mode="orchestrate", max_parallel=3)

    assert "本步骤的拆解（由你决定）" in prompt
    assert "`task`" in prompt
    assert "`subagent_type`" in prompt and "`description`" in prompt
    assert "同一个回复里派出的多块会并行跑" in prompt
    assert "最多 3 个子 agent 在跑" in prompt, "the ceiling the platform enforces"
    assert "拆解不改变交付" in prompt, "the contract block above still holds"


@pytest.mark.parametrize(
    ("declared", "default", "expected"),
    [(None, 6, 6), (2, 6, 2), (None, None, DEFAULT_MAX_PARALLEL)],
)
def test_the_prompt_quotes_the_ceiling_the_step_actually_runs_under(
    tmp_path: Path, declared: int | None, default: int | None, expected: int
) -> None:
    """Own declaration wins, then the feature's, then the platform's default."""
    feature = _load_feature(tmp_path, steps=[_step_node(mode="orchestrate", max_parallel=declared)])
    step = feature.steps[0]

    prompt = render_step_prompt(step, feature, {}, {}, locale="en", max_parallel_default=default)

    assert f"At most {expected} subagents run at once" in prompt


def test_a_role_pinned_step_is_told_which_subagent_it_runs_as(tmp_path: Path) -> None:
    prompt = _render_step(tmp_path, locale="en", agent_role="tooling")

    assert "runs as the subagent `tooling`" in prompt
    assert "`subagent_type` = `tooling`" in prompt
    assert "never dispatches that role fails the step" in prompt


def test_the_decomposition_block_follows_the_callers_language(tmp_path: Path) -> None:
    prompt = _render_step(tmp_path, locale="en", mode="orchestrate")

    assert "This step's decomposition (yours to decide)" in prompt
    assert "拆解" not in prompt


def test_a_plain_step_prompt_is_unchanged(tmp_path: Path) -> None:
    """The regression guard: only a step that dispatches gets anything new.

    The expected text is written out here on purpose — comparing it against the
    renderer's own output would pass however the block changed.
    """
    feature = _load_feature(
        tmp_path,
        steps=[
            _step_node(),
            _step_node(
                id="report",
                name="摘要报告",
                inputs=["bom_rows"],
                output={"name": "summary", "schema": "text"},
                prompt="读 BOM PDF，过滤 L1。",
            ),
        ],
    )
    artifacts = {
        "bom_rows": Artifact(
            name="bom_rows",
            schema="text",
            value="已生成 BOM.xlsx",
            step_id="extract_l1",
        )
    }

    prompt = render_step_prompt(feature.steps[1], feature, {}, artifacts, locale="zh")

    assert prompt == (
        "读 BOM PDF，过滤 L1。\n\n"
        "本步骤的输入产物（前序步骤已产出的结构化数据，直接使用，不要重新推断）：\n\n"
        '### bom_rows (text)\n```json\n"已生成 BOM.xlsx"\n```\n\n'
        "输出契约（必须遵守）: 只输出本步骤的文本产物，不要输出 JSON。"
    )
