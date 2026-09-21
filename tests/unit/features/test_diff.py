"""Paragraph-level diff between an AI draft and the human final text."""

from __future__ import annotations

from octop.infra.features.diff import diff_segments


def _draft_side(ops: list[dict[str, str]], draft: str) -> list[str]:
    """Replay *ops* against the draft text: keeps/removes/replaces contribute."""
    out: list[str] = []
    for op in ops:
        if op["op"] == "keep":
            out.append(op["text"])
        elif op["op"] == "remove":
            out.append(op["text"])
        elif op["op"] == "replace":
            out.append(op["draft"])
    assert out == [line for line in draft.split("\n") if line.strip()]
    return out


def _final_side(ops: list[dict[str, str]], final: str) -> list[str]:
    """Replay *ops* against the final text: keeps/adds/replaces contribute."""
    out: list[str] = []
    for op in ops:
        if op["op"] == "keep":
            out.append(op["text"])
        elif op["op"] == "add":
            out.append(op["text"])
        elif op["op"] == "replace":
            out.append(op["final"])
    assert out == [line for line in final.split("\n") if line.strip()]
    return out


def test_identical_texts_have_no_diff() -> None:
    """An untouched draft is the strongest positive sample: the diff is empty."""
    assert diff_segments("客户：张三\n金额：100", "客户：张三\n金额：100") == []


def test_diff_between_empty_texts_is_empty() -> None:
    assert diff_segments("", "") == []


def test_whitespace_only_edits_are_not_a_diff() -> None:
    """Re-formatting is not intent — it must not become learning signal."""
    assert diff_segments("  a  \n\nb\t\n", "\na\nb") == []


def test_line_break_styles_do_not_change_a_segment() -> None:
    assert diff_segments("a\r\nb", "a\nb") == []


def test_pure_addition_keeps_context_and_adds_the_new_segment() -> None:
    ops = diff_segments("甲\n乙", "甲\n补充：丙\n乙")

    assert ops == [
        {"op": "keep", "text": "甲"},
        {"op": "add", "text": "补充：丙"},
        {"op": "keep", "text": "乙"},
    ]


def test_pure_removal_reports_the_dropped_segment() -> None:
    ops = diff_segments("甲\n多余的说明\n乙", "甲\n乙")

    assert ops == [
        {"op": "keep", "text": "甲"},
        {"op": "remove", "text": "多余的说明"},
        {"op": "keep", "text": "乙"},
    ]


def test_replaced_segment_carries_both_sides() -> None:
    ops = diff_segments("客户：张三", "客户：张三（全称：张三丰）")

    assert ops == [
        {"op": "replace", "draft": "客户：张三", "final": "客户：张三（全称：张三丰）"}
    ]


def test_reordered_segments_survive_replay() -> None:
    """A move reads as add+remove; replay must still rebuild both texts exactly."""
    draft = "摘要\n正文\n结论"
    final = "结论\n摘要\n正文"

    ops = diff_segments(draft, final)

    assert ops != []
    _draft_side(ops, draft)
    _final_side(ops, final)


def test_empty_draft_is_all_additions() -> None:
    ops = diff_segments("", "第一段\n第二段")

    assert ops == [
        {"op": "add", "text": "第一段"},
        {"op": "add", "text": "第二段"},
    ]


def test_empty_final_is_all_removals() -> None:
    ops = diff_segments("第一段\n第二段", "")

    assert ops == [
        {"op": "remove", "text": "第一段"},
        {"op": "remove", "text": "第二段"},
    ]


def test_blank_lines_do_not_produce_ops() -> None:
    """Paragraph-level, not character-level: added blank lines are formatting."""
    ops = diff_segments("甲\n乙", "甲\n\n\n乙")

    assert ops == []


def test_long_edited_document_stays_lossless() -> None:
    """Past the LCS budget the fallback still pairs every changed segment."""
    draft = "\n".join(f"草稿 {index}" for index in range(600))
    final = "\n".join(f"定稿 {index}" for index in range(600))

    ops = diff_segments(draft, final)

    assert [op["op"] for op in ops] == ["replace"] * 600
    _draft_side(ops, draft)
    _final_side(ops, final)


def test_mixed_edit_replays_to_both_texts() -> None:
    draft = "标题\n摘要：旧\n要点一\n要点二\n结论"
    final = "标题\n摘要：新\n要点一\n要点三\n结论"

    ops = diff_segments(draft, final)

    _draft_side(ops, draft)
    _final_side(ops, final)
    assert {op["op"] for op in ops} == {"keep", "replace"}
