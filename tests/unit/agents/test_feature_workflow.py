"""Unit tests for a feature's workflow definition, its storage and its run block."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents.feature_workflow import (
    WORKSPACE_WORKFLOW_PATH,
    WorkflowCapabilities,
    clear_workflow,
    fill_step_ids,
    load_workflow,
    parse_workflow,
    render_overlay_block,
    render_run_block,
    save_workflow,
    slugify_step_id,
    validate_references,
    validate_workflow,
)
from octop.infra.errors import ErrorCode, OctopError


def _workspace(root: str) -> BackendWorkspace:
    backend = LocalShellBackend(root_dir=root, virtual_mode=False)
    return BackendWorkspace(backend, root)


def _definition(**overrides: Any) -> dict[str, Any]:
    """A well-formed active workflow; keyword arguments replace whole sections."""
    document: dict[str, Any] = {
        "version": 1,
        "status": "active",
        "inputs": {
            "type": "object",
            "required": ["customer_name"],
            "properties": {
                "customer_name": {
                    "type": "string",
                    "title": {"zh": "客户名称", "en": "Customer"},
                },
                "currency": {
                    "type": "string",
                    "enum": ["CNY", "USD"],
                    "title": {"zh": "币种", "en": "Currency"},
                },
                "attachments": {
                    "type": "file",
                    "multiple": True,
                    "accept": ".pdf,.docx",
                    "title": {"zh": "附件", "en": "Attachments"},
                },
            },
        },
        "steps": [
            {
                "id": "extract",
                "name": "提取要点",
                "prompt": "读附件，按层级提取要点。",
                "skills": ["xlsx"],
                "tools": ["read_file"],
            },
            {
                "id": "draft",
                "name": "起草",
                "prompt": "根据 {{inputs}} 起草。",
                "depends_on": ["extract"],
                "gate": "confirm",
            },
        ],
        "outputs": [{"name": "报价单", "form": "markdown", "path": "outputs/quote.md"}],
        "rules": ["金额逐行核对"],
    }
    document.update(overrides)
    return document


def test_well_formed_active_definition_is_accepted() -> None:
    assert validate_workflow(_definition()) == []


def test_draft_is_accepted_without_steps_or_prompts() -> None:
    document = _definition(status="draft", steps=[{"name": "先想清楚"}])
    assert validate_workflow(document) == []


def test_a_definition_may_declare_no_steps_at_all() -> None:
    """One-pass workflows exist: inputs in, deliverables out, no pipeline declared."""
    document = _definition()
    document.pop("steps")

    assert validate_workflow(document) == []

    # Publishing one still needs the steps that are there to stand on their own.
    document["status"] = "active"
    assert validate_workflow(document) == []


def test_active_requires_steps_and_prompts() -> None:
    problems = validate_workflow(_definition(steps=[]))
    assert any("must not be empty" in problem for problem in problems)

    problems = validate_workflow(
        _definition(steps=[{"id": "extract", "name": "提取", "prompt": "  "}])
    )
    assert any("prompt is required" in problem for problem in problems)


def test_every_problem_is_reported_at_once() -> None:
    document = _definition(
        version=9,
        status="live",
        title="报价助手",
        steps=[
            {"id": "extract", "name": "", "prompt": "x", "gate": "maybe"},
            {"id": "extract", "name": "两次同名", "prompt": "y"},
            {"id": "Bad Id", "name": "非法 id", "prompt": "z"},
        ],
        outputs=[{"name": "报价单", "form": "pdf"}],
        rules=["", "  "],
    )
    problems = validate_workflow(document)
    for expected in (
        "version must be 1",
        "status must be one of",
        "unsupported keys: title",
        "id must match",
        "name is required",
        "gate must be one of",
        "id duplicates",
        "form must be one of",
        "rules[1] must be a non-empty string",
    ):
        assert any(expected in problem for problem in problems), expected


def test_depends_on_must_name_an_earlier_step() -> None:
    steps = [
        {"id": "first", "name": "第一步", "prompt": "a", "depends_on": ["second"]},
        {"id": "second", "name": "第二步", "prompt": "b", "depends_on": ["second"]},
        {"id": "third", "name": "第三步", "prompt": "c", "depends_on": ["nope"]},
    ]
    problems = validate_workflow(_definition(steps=steps))
    assert any("must name an earlier step ('second')" in problem for problem in problems)
    assert any("names unknown step 'nope'" in problem for problem in problems)


def test_a_dependency_problem_is_not_hidden_by_an_unrelated_one() -> None:
    """Both rounds of a fix must be sayable in one refusal."""
    steps = [
        {"id": "first", "name": "", "prompt": "a"},  # a missing name, nothing to do with order
        {"id": "second", "name": "第二步", "prompt": "b", "depends_on": ["first", "second"]},
    ]
    problems = validate_workflow(_definition(steps=steps))
    assert any("name is required" in problem for problem in problems)
    assert any("must name an earlier step ('second')" in problem for problem in problems)


def test_references_must_exist_when_they_are_declared() -> None:
    """A step naming a skill nobody has is refused — with what the agent *does* have."""
    definition = _definition(
        steps=[
            {
                "id": "extract",
                "name": "提取",
                "prompt": "a",
                "skills": ["xlsx", "excel"],
                "subagents": ["writer"],
            }
        ]
    )
    problems = validate_references(
        definition,
        WorkflowCapabilities(skills=frozenset({"xlsx", "pptx"}), subagents=frozenset()),
    )

    assert len(problems) == 2
    assert "steps[0].skills names unknown skill(s): excel" in problems[0]
    assert "available: pptx, xlsx" in problems[0]
    assert "steps[0].subagents names unknown subagent(s): writer" in problems[1]
    assert "has none installed" in problems[1]


def test_a_definition_without_steps_or_references_has_nothing_to_check() -> None:
    assert validate_references({"version": 1}, WorkflowCapabilities()) == []
    assert (
        validate_references(
            _definition(steps=[{"id": "a", "name": "a", "prompt": "a"}]),
            WorkflowCapabilities(),
        )
        == []
    )


def test_the_available_hint_stays_readable_when_there_are_many() -> None:
    available = frozenset(f"skill-{index}" for index in range(30))

    problems = validate_references(
        _definition(steps=[{"id": "a", "name": "a", "prompt": "a", "skills": ["nope"]}]),
        WorkflowCapabilities(skills=available),
    )

    assert "+10 more" in problems[0]


def test_input_schema_is_restricted_to_the_supported_subset() -> None:
    document = _definition(
        inputs={
            "type": "object",
            "required": ["ghost"],
            "properties": {
                "nested": {"type": "object", "title": {"zh": "嵌套", "en": "Nested"}},
                "count": {
                    "type": "integer",
                    "format": "date",
                    "title": {"zh": "数量", "en": "Count"},
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "title": {"zh": "明细", "en": "Rows"},
                },
                "half": {"type": "string", "title": {"zh": "只写了中文"}},
                "files": {
                    "type": "file",
                    "accept": 3,
                    "title": {"zh": "文件", "en": "Files"},
                },
                "extra": {
                    "type": "string",
                    "pattern": "^a$",
                    "title": {"zh": "多余键", "en": "Extra"},
                },
            },
        }
    )
    problems = validate_workflow(document)
    for expected in (
        "inputs.required names unknown fields: ghost",
        "inputs.properties.nested.type must be one of",
        "only apply to a string field",
        "inputs.properties.rows.items.type must be one of",
        "must be a non-empty {'zh': …, 'en': …} pair",
        "unsupported keys: pattern",
    ):
        assert any(expected in problem for problem in problems), expected


def test_non_string_field_rejects_format_and_enum() -> None:
    document = _definition(
        inputs={
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "format": "textarea",
                    "enum": ["a"],
                    "title": {"zh": "数量", "en": "Count"},
                }
            },
        }
    )
    problems = validate_workflow(document)
    assert any("only apply to a string field" in problem for problem in problems)


def test_refusal_carries_every_problem_and_its_status() -> None:
    with pytest.raises(OctopError) as caught:
        parse_workflow(_definition(steps=[{"id": "Extract", "name": "x", "prompt": "y"}]))
    err = caught.value
    assert err.code is ErrorCode.WORKFLOW_INVALID
    assert err.status == 400
    reason = err.details["reason"]
    assert "id must match" in reason
    assert err.localized_message("zh").startswith("工作流定义不合法")
    assert "{reason}" not in err.localized_message("en")


def test_slugify_step_id_always_yields_a_legal_id() -> None:
    assert slugify_step_id("Extract L1 Items") == "extract_l1_items"
    assert slugify_step_id("提取要点", index=2) == "step_2"
    assert slugify_step_id("1. First step!") == "step_1_first_step"
    assert slugify_step_id("a" * 100) == "a" * 64


def test_step_ids_are_derived_for_a_document_that_omits_them() -> None:
    document = {
        "version": 1,
        "status": "draft",
        "steps": [{"name": "提取要点"}, {"name": "复核"}, {"name": "复核"}],
    }

    filled = fill_step_ids(document)

    assert [step["id"] for step in filled["steps"]] == ["step_1", "step_2", "step_3"]
    # The input is not mutated: the caller's document is theirs.
    assert "id" not in document["steps"][0]


def test_an_existing_id_is_never_rewritten_and_new_ones_dodge_it() -> None:
    document = {
        "version": 1,
        "steps": [{"id": "keep", "name": "提取"}, {"name": "复核"}, {"name": "Extract"}],
    }

    filled = fill_step_ids(document)

    assert [step["id"] for step in filled["steps"]] == ["keep", "step_2", "extract"]


def test_a_derived_id_never_collides_with_one_already_there() -> None:
    document = {"version": 1, "steps": [{"id": "extract", "name": "提取"}, {"name": "Extract"}]}

    filled = fill_step_ids(document)

    assert [step["id"] for step in filled["steps"]] == ["extract", "extract_2"]


def test_a_malformed_id_is_left_for_validation_to_report() -> None:
    """Rewriting it silently would hide a typo rather than say what is wrong with it."""
    document = {"version": 1, "status": "active", "steps": [{"id": "Bad Id", "name": "提取"}]}

    filled = fill_step_ids(document)

    assert filled["steps"][0]["id"] == "Bad Id"
    assert any("id must match" in problem for problem in validate_workflow(filled))


@pytest.mark.asyncio
async def test_save_and_load_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        stored = await save_workflow(workspace, _definition())
        assert stored["steps"][1]["gate"] == "confirm"

        loaded = await load_workflow(workspace)
        assert loaded.error is None
        assert loaded.definition == stored
        assert (Path(ws_dir) / ".octop" / "workflow.json").is_file()


@pytest.mark.asyncio
async def test_absent_definition_is_not_an_error() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        loaded = await load_workflow(_workspace(ws_dir))
        assert loaded.definition is None
        assert loaded.error is None
        assert loaded.present is False


@pytest.mark.asyncio
async def test_load_reports_what_a_hand_edited_file_got_wrong() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        await workspace.awrite_text(WORKSPACE_WORKFLOW_PATH, "{not json", force=True)
        broken = await load_workflow(workspace)
        assert broken.definition is None
        assert broken.error is not None and "not valid JSON" in broken.error

        await workspace.awrite_text(
            WORKSPACE_WORKFLOW_PATH,
            json.dumps(_definition(steps=[{"id": "Nope", "name": "x", "prompt": "y"}])),
            force=True,
        )
        invalid = await load_workflow(workspace)
        assert invalid.definition is None
        assert invalid.error is not None and "id must match" in invalid.error


@pytest.mark.asyncio
async def test_clear_workflow_removes_the_file() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = _workspace(ws_dir)
        await save_workflow(workspace, _definition())
        await clear_workflow(workspace)
        assert (await load_workflow(workspace)).present is False


def test_run_block_reads_inputs_steps_rules_and_outputs_in_order() -> None:
    block = render_run_block(
        _definition(),
        locale="zh",
        name="报价助手",
        values={"customer_name": "ACME", "attachments": ["inbound/quote.pdf"], "currency": ""},
    )
    assert "功能工作流：报价助手" in block
    assert "客户名称: ACME" in block
    assert "币种" not in block  # an empty value is omitted, not rendered blank
    assert "inbound/quote.pdf" in block
    assert block.index("第 1/2 步：提取要点") < block.index("第 2/2 步：起草")
    assert "技能：xlsx" in block and "工具：read_file" in block
    assert "基于：extract" in block
    assert "金额逐行核对" in block
    assert "报价单（markdown）" in block


def test_only_a_confirm_step_says_to_stop() -> None:
    one_gate = _definition(
        steps=[
            {"id": "extract", "name": "提取", "prompt": "a", "gate": "confirm"},
            {"id": "draft", "name": "起草", "prompt": "b", "gate": "auto"},
        ]
    )
    block = render_run_block(one_gate, locale="en")
    assert block.count("wait for their confirmation") == 1
    assert "1/2. 提取" in block


def test_run_block_without_inputs_says_the_step_should_ask() -> None:
    block = render_run_block(_definition(), locale="en")
    assert "No input card came with this message" in block


def test_placeholders_are_replaced_and_unknown_ones_survive() -> None:
    definition = _definition(
        steps=[
            {
                "id": "draft",
                "name": "起草",
                "prompt": "按 {{inputs}} 起草，参考 {{inputs_json}}，不要动 {{tomorrow}}",
            }
        ]
    )
    block = render_run_block(definition, locale="zh", values={"customer_name": "ACME"})
    assert "按 - 客户名称: ACME 起草" in block
    assert "{}" in block
    assert "{{tomorrow}}" in block


def test_overlay_block_is_empty_for_empty_text_and_states_its_priority() -> None:
    assert render_overlay_block("   ", locale="zh") == ""
    zh = render_overlay_block("我们含税", locale="zh")
    assert "最高优先级" in zh and "我们含税" in zh
    en = render_overlay_block("We quote tax-inclusive", locale="en")
    assert "highest priority" in en and "We quote tax-inclusive" in en
