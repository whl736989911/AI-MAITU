"""Unit tests for the change engine — apply, conflict, and undo, path by path."""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.agents.feature_workflow_changes import (
    MISSING,
    ChangeConflictError,
    apply_items,
    get_at,
    parse_path,
    resolve_items,
    revert_items,
    validate_items,
)


def _definition(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": 1,
        "status": "active",
        "steps": [
            {"id": "extract", "name": "提取", "prompt": "读附件", "gate": "auto"},
            {"id": "draft", "name": "起草", "prompt": "写", "gate": "auto"},
        ],
        "rules": ["金额逐行核对"],
        "outputs": [{"name": "报价单", "form": "markdown"}],
    }
    document.update(overrides)
    return document


def test_paths_address_lists_objects_and_nothing() -> None:
    assert parse_path("/rules/2") == ["rules", "2"]
    assert parse_path("/steps/1/gate") == ["steps", "1", "gate"]
    assert parse_path("") == []
    with pytest.raises(ValueError, match="must start with"):
        parse_path("rules/0")
    with pytest.raises(ValueError, match="must be a string"):
        parse_path(None)  # type: ignore[arg-type]


def test_get_at_reads_values_and_reports_what_is_missing() -> None:
    document = _definition()

    assert get_at(document, "") is document
    assert get_at(document, "/steps/1/name") == "起草"
    assert get_at(document, "/rules/0") == "金额逐行核对"
    assert get_at(document, "/steps/9/name") is MISSING
    assert get_at(document, "/rules/9") is MISSING
    assert get_at(document, "/nope") is MISSING
    assert get_at(document, "/steps/notanumber") is MISSING


def test_an_edit_is_applied_to_a_copy_and_leaves_the_original_alone() -> None:
    document = _definition()

    changed = apply_items(
        document,
        [{"path": "/steps/1/gate", "before": "auto", "after": "confirm"}],
    )

    assert changed["steps"][1]["gate"] == "confirm"
    assert document["steps"][1]["gate"] == "auto"
    assert changed is not document


def test_appending_lands_and_is_recorded_as_the_index_it_landed_on() -> None:
    document = _definition()

    changed = apply_items(document, [{"path": "/rules/-", "before": None, "after": "不得编造交期"}])

    assert changed["rules"] == ["金额逐行核对", "不得编造交期"]


def test_a_batch_is_refused_whole_when_the_document_moved() -> None:
    """The edit somebody made in the editor must not be overwritten."""
    document = _definition()
    document["steps"][1]["gate"] = "confirm"  # edited after the change was built

    with pytest.raises(ChangeConflictError) as caught:
        apply_items(
            document,
            [
                {"path": "/rules/-", "before": None, "after": "不得编造交期"},
                {"path": "/steps/1/gate", "before": "auto", "after": "confirm"},
            ],
        )

    assert caught.value.paths == ("/steps/1/gate",)
    assert document["rules"] == ["金额逐行核对"]


def test_a_missing_field_matches_an_expected_null() -> None:
    """``before: null`` means "nothing was there", which is what an add records."""
    document = _definition()

    changed = apply_items(
        document,
        [{"path": "/rules/-", "before": None, "after": "第一条"}],
    )

    assert changed["rules"][-1] == "第一条"


def test_reverting_restores_edits_and_removes_additions() -> None:
    document = _definition()
    items = resolve_items(
        document,
        [
            {"path": "/steps/1/gate", "before": "auto", "after": "confirm"},
            {"path": "/rules/-", "before": None, "after": "不得编造交期"},
        ],
    )
    changed = apply_items(document, items)
    changed["rules"].append("不要编造客户名称")  # a later, unrecorded edit

    restored, conflicts = revert_items(changed, items)

    assert conflicts == ()
    assert restored["steps"][1]["gate"] == "auto"
    assert restored["rules"] == ["金额逐行核对", "不要编造客户名称"]


def test_reverting_removes_a_field_the_change_added() -> None:
    """Undo states the document as it was: "nothing was there" means the field goes."""
    document = _definition()
    items = resolve_items(document, [{"path": "/notes", "before": None, "after": "内部备注"}])
    changed = apply_items(document, items)
    assert changed["notes"] == "内部备注"

    restored, conflicts = revert_items(changed, items)

    assert conflicts == ()
    assert "notes" not in restored
    assert restored == document


def test_reverting_reports_what_a_later_edit_replaced_instead_of_forcing_it() -> None:
    document = _definition()
    items = [{"path": "/steps/1/gate", "before": "auto", "after": "confirm"}]
    changed = apply_items(document, items)
    changed["steps"][1]["gate"] = "auto"  # somebody put it back by hand

    restored, conflicts = revert_items(changed, items)

    assert conflicts == ("/steps/1/gate",)
    assert restored["steps"][1]["gate"] == "auto"


def test_reverting_a_whole_document_change_puts_the_old_one_back() -> None:
    """An overlay change is one item on the document root's text."""
    items = [{"path": "/overlay", "before": "旧的话", "after": "新的话"}]

    restored, conflicts = revert_items({"overlay": "新的话"}, items)

    assert conflicts == ()
    assert restored == {"overlay": "旧的话"}


def test_two_appends_in_one_batch_resolve_to_the_indices_they_land_on() -> None:
    """Recorded paths must distinguish them, or undo deletes the wrong element."""
    document = _definition()
    items = [
        {"path": "/rules/-", "before": None, "after": "第一条"},
        {"path": "/rules/-", "before": None, "after": "第二条"},
    ]

    resolved = resolve_items(document, items)
    changed = apply_items(document, resolved)

    assert [item["path"] for item in resolved] == ["/rules/1", "/rules/2"]
    assert changed["rules"] == ["金额逐行核对", "第一条", "第二条"]

    restored, conflicts = revert_items(changed, resolved)
    assert conflicts == ()
    assert restored["rules"] == ["金额逐行核对"]


def test_batch_validation_names_every_problem() -> None:
    problems = validate_items(
        [
            {"path": "rules/0", "after": "x"},
            {"path": "/rules/0"},
            {"path": "/rules/0", "after": "x", "why": "extra"},
        ]
    )

    assert any("must start with" in problem for problem in problems)
    assert any("after is required" in problem for problem in problems)
    assert any("unsupported keys: why" in problem for problem in problems)


def test_an_empty_batch_is_not_a_change() -> None:
    assert validate_items([]) == ["items must be a non-empty array"]
    assert validate_items("nope") == ["items must be a non-empty array"]


def test_a_field_may_only_be_created_by_an_item_that_says_nothing_was_there() -> None:
    """``before: null`` is the permission to add; a typo is not silently a new field."""
    added = apply_items(_definition(), [{"path": "/notes", "before": None, "after": "内部备注"}])
    assert added["notes"] == "内部备注"

    # An item that expects a value where nothing exists is a conflict, not an add.
    with pytest.raises(ChangeConflictError):
        apply_items(_definition(), [{"path": "/statuses", "before": "x", "after": "y"}])
    # And an index that is not a number is still refused rather than created.
    with pytest.raises(ValueError, match="numeric index"):
        apply_items(_definition(), [{"path": "/rules/zero", "before": None, "after": "y"}])
