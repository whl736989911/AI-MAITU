"""Unit tests for query terms, ranking, and snippets (design §9)."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.index import Hit
from octop.infra.knowledge.search import (
    MAX_TERMS,
    fuse_rankings,
    normalize_query,
    query_terms,
    rank_hits,
    score_chunk,
    snippet,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Refund Policy", ("refund", "policy")),
        ("INV-2026-0042", ("inv", "2026", "0042")),
        ("采购合同", ("采购合同",)),
        # A run longer than four characters is searched by its windows, which is
        # what finds the part of a sentence a document actually contains.
        ("采购合同的付款条款", ("采购", "购合", "合同", "同的", "的付", "付款", "款条", "条款")),
        ("合同 contract", ("合同", "contract")),
        ("  ", ()),
        ("", ()),
    ],
)
def test_query_terms(query: str, expected: tuple[str, ...]) -> None:
    assert query_terms(query) == expected


def test_query_terms_deduplicate_and_bound_the_list() -> None:
    assert query_terms("合同 合同 contract") == ("合同", "contract")

    many = query_terms(" ".join(f"term{index}" for index in range(40)))

    assert len(many) == MAX_TERMS


def test_normalize_query_collapses_whitespace() -> None:
    assert normalize_query("  Refund\n\t Policy ") == "refund policy"


def test_the_whole_query_outranks_its_terms_scattered() -> None:
    """A phrase is the strongest evidence there is, so it must win."""
    phrase = "refunds take five business days"
    together = "Refunds take five business days for card payments."
    scattered = "Refunds are processed. Take note of the five days rule."

    assert score_chunk(together, ("refunds", "five", "days"), phrase) > score_chunk(
        scattered, ("refunds", "five", "days"), phrase
    )


def test_coverage_outranks_repetition() -> None:
    """Every term present beats one term repeated."""
    every = "alpha beta gamma"
    repeated = "alpha alpha alpha alpha alpha"

    assert score_chunk(every, ("alpha", "beta", "gamma")) > score_chunk(
        repeated, ("alpha", "beta", "gamma")
    )


def test_frequency_is_damped() -> None:
    """A word mentioned fifty times is not fifty times more relevant."""
    once = score_chunk("alpha", ("alpha",))
    five = score_chunk("alpha " * 5, ("alpha",))
    fifty = score_chunk("alpha " * 50, ("alpha",))

    # Repetition helps, and then it stops helping: past the cap a chunk that
    # repeats a term is exactly as relevant as one that mentions it five times.
    assert five > once
    assert fifty == five


def test_an_empty_query_scores_nothing() -> None:
    assert score_chunk("anything", (), "") == 0.0


def test_snippet_keeps_short_text_whole() -> None:
    assert snippet("Refunds take five days.", ("refunds",)) == "Refunds take five days."


def test_snippet_centres_on_the_match() -> None:
    text = "x" * 400 + " refund policy " + "y" * 400

    out = snippet(text, ("refund",), width=80)

    assert "refund policy" in out
    assert out.startswith("…")
    assert out.endswith("…")
    assert len(out) <= 82  # the window plus its two ellipses


def _hit(chunk_id: str, text: str) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id=chunk_id.split(":")[0],
        ordinal=int(chunk_id.split(":")[1]),
        text=text,
        score=0.0,
        metadata={},
    )


def test_fusion_prefers_what_both_halves_found() -> None:
    """design §9: a chunk the vectors *and* the words found comes first."""
    both = _hit("c:0", "found by both")
    meaning_only = _hit("a:0", "found by meaning")
    third = _hit("b:0", "third by meaning")

    # ``c:0`` is the only chunk both lists contain, so it carries two
    # contributions and outranks the top of either list on its own.
    fused = fuse_rankings([[both, meaning_only, third], [both]])

    assert [hit.chunk_id for hit in fused] == ["c:0", "a:0", "b:0"]
    assert fused[0].score > fused[1].score


def test_fusion_keeps_one_entry_per_chunk() -> None:
    """The same chunk found twice is one result, not two."""
    fused = fuse_rankings([[_hit("a:0", "text")], [_hit("a:0", "text")]])

    assert len(fused) == 1
    assert fused[0].chunk_id == "a:0"


def test_fusion_without_a_second_ranking_keeps_the_first() -> None:
    vectors = [_hit("a:0", "first"), _hit("b:0", "second")]

    assert [hit.chunk_id for hit in fuse_rankings([vectors, []])] == ["a:0", "b:0"]
    assert fuse_rankings([]) == []
    assert fuse_rankings([[], []]) == []


def test_fusion_is_stable_for_equal_ranks() -> None:
    """Two chunks ranked equally still come back in one fixed order."""
    first = _hit("b:0", "x")
    second = _hit("a:0", "y")

    fused = fuse_rankings([[first], [second]])

    assert [hit.chunk_id for hit in fused] == ["a:0", "b:0"]
    assert [hit.chunk_id for hit in fuse_rankings([[first], [second]])] == ["a:0", "b:0"]


def test_rank_hits_scores_and_orders_stably() -> None:
    hits = [
        Hit(chunk_id="b:0", doc_id="b", ordinal=0, text="beta", score=0.0, metadata={}),
        Hit(chunk_id="a:1", doc_id="a", ordinal=1, text="alpha beta", score=0.0, metadata={}),
        Hit(chunk_id="a:0", doc_id="a", ordinal=0, text="alpha beta", score=0.0, metadata={}),
    ]

    ranked = rank_hits(hits, ("alpha", "beta"))

    # The two chunks holding both terms come first, in document/position order —
    # the same query over the same index answers the same way every time.
    assert [hit.chunk_id for hit in ranked] == ["a:0", "a:1", "b:0"]
    assert ranked[0].score > ranked[-1].score
