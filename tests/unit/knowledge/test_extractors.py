"""Built-in fact extractors keep located values regardless of business domain."""

from __future__ import annotations

from octop.infra.knowledge.extractors import extract_facts
from octop.infra.knowledge.parse import ParsedBlock


def _facts(text: str):
    return extract_facts([ParsedBlock(text=text, kind="page", locator={"page": 2})])


def test_measurement_extractor_recognizes_common_drawing_annotations() -> None:
    facts = _facts("法兰 φ120，公差 ±0.5，角度 45°，螺纹 M30×2，圆角 R15")
    pairs = {(fact.fact_type, fact.normalized) for fact in facts}
    assert ("diameter", "φ120") in pairs
    assert ("tolerance", "±0.5") in pairs
    assert ("angle", "45°") in pairs
    assert ("thread", "M30×2") in pairs
    assert ("radius", "R15") in pairs
    assert all(fact.locator == {"page": 2} for fact in facts)


def test_measurement_extractor_accepts_spacing_and_ascii_phi() -> None:
    facts = _facts("Φ 80 / M 8 x 1.25")
    pairs = {(fact.fact_type, fact.normalized) for fact in facts}
    assert ("diameter", "φ80") in pairs
    assert ("thread", "M8X1.25") in pairs


def test_clause_extractor_reads_sentences_and_headings() -> None:
    facts = extract_facts(
        [
            ParsedBlock(
                text="第 5.3 条规定了试验方法。\n5.3.1 试样准备\n6.2 结果判定",
                kind="page",
                locator={"page": 7},
            )
        ]
    )
    clauses = {fact.normalized for fact in facts if fact.fact_type == "clause"}
    assert {"5.3", "5.3.1", "6.2"} <= clauses


def test_facts_are_deduplicated_per_location() -> None:
    facts = _facts("φ120 和 φ120")
    assert [fact.normalized for fact in facts].count("φ120") == 1
