"""Unit tests for the step format and its typed artifacts.

This module is the format's own judge, so these tests are about exactly that:
what a definition may declare, what a step's answer has to look like before it
becomes the artifact it promised, and what the engine refuses instead of running
as something it is not.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octop.infra.features.steps import (
    ON_FAILURE_ABORT,
    STEP_VOIDED,
    FeatureStep,
    StepOutput,
    StepOutputInvalid,
    parse_artifact,
    parse_steps,
    render_value,
    unsupported_reasons,
    validate_artifact,
    verdict_of,
)


def _step(**overrides: Any) -> dict[str, Any]:
    """One valid step declaration, as the editor writes it."""
    node: dict[str, Any] = {
        "id": "extract_l1",
        "name": "提取 L1 项",
        "mode": "agent",
        "inputs": [],
        "output": {"name": "bom_rows", "schema": "table:4cols"},
        "prompt": "读 BOM PDF，过滤 L1。",
        "gate": "auto",
        "on_failure": "abort",
    }
    node.update(overrides)
    return node


def _parsed(**overrides: Any) -> FeatureStep:
    steps, errors = parse_steps([_step(**overrides)])
    assert not errors, errors
    return steps[0]


# --- parsing and validation -------------------------------------------------


def test_a_declared_plan_parses_into_its_effective_values() -> None:
    steps, errors = parse_steps(
        [
            _step(tools=["read_file"], max_parallel=4),
            _step(
                id="report",
                name="摘要报告",
                inputs=["bom_rows"],
                output={"name": "summary", "schema": "object"},
                gate="confirm",
                allow_edit=True,
            ),
        ]
    )

    assert not errors
    assert [step.id for step in steps] == ["extract_l1", "report"]
    assert steps[0].tools == ("read_file",)
    assert steps[0].max_parallel == 4
    assert steps[1].inputs == ("bom_rows",)
    assert steps[1].allow_edit is True
    # A ``tools`` list left out inherits the run's surface; an explicit [] is the
    # scope "no tools at all" — the two are different facts and stay different.
    assert steps[1].tools is None
    assert parse_steps([_step(tools=[])])[0][0].tools == ()


def test_a_step_is_served_back_exactly_as_it_was_declared() -> None:
    """The editor reads a step back to edit it: nothing is filled in for it."""
    node = _step(tools=["read_file"])

    steps, errors = parse_steps([node])

    assert not errors
    assert steps[0].as_dict() == node


def test_an_absent_steps_node_is_no_steps_at_all() -> None:
    """A feature that declares none is the one-shot feature it was before."""
    assert parse_steps(None) == ([], [])
    assert parse_steps([]) == ([], [])


@pytest.mark.parametrize(
    ("node", "reason"),
    [
        (_step(mode=None), "steps[0].mode must be declared"),
        (_step(gate="eventually"), "steps[0].gate must be one of"),
        (_step(on_failure="ignore"), "steps[0].on_failure must be one of"),
        (_step(prompt="  "), "steps[0].prompt must be a non-empty string"),
        (_step(surprise=True), "steps[0] uses unsupported keys: 'surprise'"),
        (_step(output=None), "steps[0].output must be an object"),
        (_step(output={"name": "rows"}), "steps[0].output.schema must be one of"),
        (
            _step(output={"name": "rows", "schema": "matrix"}),
            "steps[0].output.schema must be one of",
        ),
        (_step(output={"name": "Rows"}), "steps[0].output.name must be a lowercase identifier"),
        (_step(max_parallel=0), "steps[0].max_parallel must be a positive integer"),
        (_step(tools=["not_a_tool"]), "steps[0].tools names unknown built-in tools: 'not_a_tool'"),
        (_step(id="Extract"), "steps[0].id must be a lowercase identifier"),
        (
            _step(gate="validate", allow_edit=True),
            "steps[0].output.schema must be 'object' for a 'validate' gate",
        ),
        (_step(allow_edit="yes"), "steps[0].allow_edit must be true or false"),
    ],
)
def test_a_declaration_the_engine_cannot_honour_is_refused(
    node: dict[str, Any], reason: str
) -> None:
    """Refused with the field named — never stored for the engine to guess at."""
    steps, errors = parse_steps([node])

    assert steps == []
    assert any(reason in error for error in errors), errors


def test_an_input_no_earlier_step_produces_is_refused() -> None:
    """A step cannot read an artifact that does not exist yet."""
    steps, errors = parse_steps([_step(inputs=["routing"])])

    assert steps == []
    assert "steps[0].inputs names 'routing', which no earlier step produces" in errors


def test_a_step_cannot_reuse_an_artifact_name() -> None:
    """An artifact name addresses one value: two producers would make it ambiguous."""
    steps, errors = parse_steps([_step(), _step(id="again", inputs=["bom_rows"])])

    assert [step.id for step in steps] == ["extract_l1"]
    assert "steps[1].output.name 'bom_rows' was already produced by step 'extract_l1'" in errors


def test_every_problem_is_reported_at_once() -> None:
    """The editor shows the author the whole list, not the first line."""
    _steps, errors = parse_steps([_step(gate="eventually", max_parallel=-1)])

    assert len(errors) == 2


# --- the artifact a step produces -------------------------------------------


def test_a_table_artifact_is_read_out_of_a_fenced_answer() -> None:
    step = _parsed()

    value = parse_artifact(step, '这是结果：\n```json\n[[1, "螺栓", "M8", 4]]\n```\n以上。')

    assert value == [[1, "螺栓", "M8", 4]]


def test_a_text_artifact_is_the_answer_verbatim() -> None:
    step = _parsed(output={"name": "package", "schema": "text"})

    assert parse_artifact(step, " 已生成 BOM.xlsx ") == " 已生成 BOM.xlsx "


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("这是自然语言，不是数据。", "carries no JSON value"),
        ('[[1, "螺栓", "M8"]]', "row 0 has 3 cells"),
        ('[[1, "螺栓", "M8", 4], [2, "垫片", "φ8"]]', "row 1 has 3 cells where row 0 has 4"),
        ('{"rows": 1}', "the value is an object"),
    ],
)
def test_an_answer_that_is_not_the_declared_type_fails_the_step(answer: str, reason: str) -> None:
    """Storing prose where a table was promised is exactly what 7.2 forbids."""
    step = _parsed()

    with pytest.raises(StepOutputInvalid) as excinfo:
        parse_artifact(step, answer)

    assert reason in str(excinfo.value)
    assert "table:4cols" in str(excinfo.value)


def test_a_list_artifact_rejects_an_object() -> None:
    step = _parsed(output={"name": "ops", "schema": "list"})

    with pytest.raises(StepOutputInvalid, match="is declared 'list' but the value is an object"):
        parse_artifact(step, '{"ops": []}')


def test_an_empty_table_is_not_a_table() -> None:
    step = _parsed()

    with pytest.raises(StepOutputInvalid, match="is declared 'table:4cols'"):
        parse_artifact(step, "[]")


def test_rows_of_an_object_table_must_share_their_keys() -> None:
    step = _parsed(output={"name": "rows", "schema": "table"})

    assert parse_artifact(step, '[{"a": 1, "b": 2}, {"b": 2, "a": 1}]') == [
        {"a": 1, "b": 2},
        {"b": 2, "a": 1},
    ]
    with pytest.raises(StepOutputInvalid, match="row 1 has 1 cells"):
        parse_artifact(step, '[{"a": 1, "b": 2}, {"a": 1}]')


def test_a_human_correction_is_checked_against_the_same_declared_type() -> None:
    """``edits`` go through the same judge as the model's own answer."""
    step = _parsed()

    assert validate_artifact(step.output, [[1, "螺栓", "M8", 4]]) == [[1, "螺栓", "M8", 4]]
    with pytest.raises(StepOutputInvalid, match="row 0 has 1 cells"):
        validate_artifact(step.output, [[1]])


def test_a_text_artifact_refuses_a_structured_correction() -> None:
    with pytest.raises(StepOutputInvalid, match="is declared 'text' but the value is an object"):
        validate_artifact(StepOutput(name="note", schema="text"), {"note": "x"})


# --- gates and modes --------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [({"passed": True, "problems": []}, True), ({"passed": False}, False)],
)
def test_a_check_gate_reads_its_verdict(verdict: dict[str, Any], expected: bool) -> None:
    assert verdict_of(StepOutput(name="verdict", schema="object"), verdict) is expected


@pytest.mark.parametrize("verdict", [{"ok": True}, {"passed": "yes"}, "passed", []])
def test_a_verdict_that_cannot_be_read_is_a_failure_not_a_pass(verdict: Any) -> None:
    """The one place where being permissive would deliver unchecked work."""
    with pytest.raises(StepOutputInvalid, match="must carry a boolean 'passed'"):
        verdict_of(StepOutput(name="verdict", schema="object"), verdict)


def test_the_unimplemented_mode_is_named_rather_than_downgraded() -> None:
    steps, errors = parse_steps(
        [
            _step(mode="orchestrate", max_parallel=8),
            _step(
                id="second",
                mode="agent",
                agent_role="tooling",
                output={"name": "x", "schema": "list"},
            ),
        ]
    )

    assert not errors, "the format accepts both: they are part of 7.10"
    assert steps[0].max_parallel == 8, "stored, so the definition keeps what it declared"

    reasons = unsupported_reasons(steps)

    assert len(reasons) == 2
    assert "mode 'orchestrate'" in reasons[0]
    assert "never degrades it to a single agent" in reasons[0]
    assert "agent_role 'tooling'" in reasons[1]


def test_a_plan_of_agent_steps_is_runnable() -> None:
    assert unsupported_reasons([_parsed()]) == []


def test_the_recorded_plan_keeps_every_effective_value() -> None:
    """The snapshot has to describe the step as it will actually run."""
    step = _parsed(tools=["read_file"], max_parallel=4)

    assert step.snapshot() == {
        "id": "extract_l1",
        "name": "提取 L1 项",
        "mode": "agent",
        "inputs": [],
        "tools": ["read_file"],
        "max_parallel": 4,
        "output": {"name": "bom_rows", "schema": "table:4cols"},
        "gate": "auto",
        "allow_edit": False,
        "on_failure": ON_FAILURE_ABORT,
        "agent_role": None,
    }


def test_a_run_reports_its_output_as_the_artifact_type_it_declared() -> None:
    assert render_value(StepOutput(name="package", schema="text"), "done") == "done"
    assert json.loads(render_value(StepOutput(name="rows", schema="list"), [["螺栓"]])) == [
        ["螺栓"]
    ]


def test_voided_is_a_step_state_of_its_own() -> None:
    """A rewound step is not "pending" again: its artifact was thrown away."""
    assert STEP_VOIDED == "voided"
