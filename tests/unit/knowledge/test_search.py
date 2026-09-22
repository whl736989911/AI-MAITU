"""Unit tests for query terms, ranking, and snippets (design §9)."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.index import Hit
from octop.infra.knowledge.search import (
    MAX_TERMS,
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
