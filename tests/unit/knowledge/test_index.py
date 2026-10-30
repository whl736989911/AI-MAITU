"""Unit tests for per-knowledge-base sidecar indexes."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.parse import ParsedBlock
from octop.infra.knowledge.search import query_terms, search_base


def test_index_replaces_document_chunks_and_returns_cosine_top_k(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-1")

    index.replace_doc_chunks(
        "doc-1",
        ["first", "second"],
        [[1.0, 0.0], [0.0, 1.0]],
        metadata=[{"page": 1}, {"page": 2}],
    )
    index.replace_doc_chunks("doc-2", ["third"], [[0.8, 0.2]])

    hits = index.search([1.0, 0.0], k=2)

    assert [hit.text for hit in hits] == ["first", "third"]
    assert hits[0].doc_id == "doc-1"
    assert hits[0].ordinal == 0
    assert hits[0].metadata == {"page": 1}

    index.replace_doc_chunks("doc-1", ["replacement"], [[0.0, 1.0]])
    assert [hit.text for hit in index.search([1.0, 0.0], k=5)] == ["third", "replacement"]

    index.delete_doc("doc-2")
    assert [hit.doc_id for hit in index.search([1.0, 0.0], k=5)] == ["doc-1"]


def test_exact_phrase_after_candidate_window_is_found_without_private_hits(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-1")
    index.replace_doc_segments(
        "public",
        [
            *(
                ParsedBlock("common unrelated phrase", "line", {"line": number})
                for number in range(1, 1201)
            ),
            ParsedBlock("common phrase", "line", {"line": 1201}),
        ],
        version="v1",
    )
    index.replace_doc_segments(
        "private", [ParsedBlock("common phrase", "line", {"line": 1})], version="v1"
    )

    hits = index.search_segments(
        ["common", "phrase"],
        allowed_doc_ids=["public"],
        phrase="common phrase",
        limit=10,
    )
    assert hits[0].doc_id == "public"
    assert hits[0].metadata["locator"] == {"line": 1201}
    assert all(hit.doc_id == "public" for hit in hits)


def test_unicode_words_are_searchable_without_an_embedding_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-unicode")
    index.replace_doc_segments(
        "names",
        [ParsedBlock("MÜLLER 新合同", "line", {"line": 3})],
        version="v1",
    )

    assert query_terms("Müller") == ("müller",)
    assert (
        index.search_text(query_terms("Müller"), allowed_doc_ids=["names"])[0].text
        == "MÜLLER 新合同"
    )

    assert index.search_text(query_terms("合同"), allowed_doc_ids=["names"])[0].doc_id == "names"


@pytest.mark.parametrize(
    ("query", "exact", "scattered"),
    [
        ("Müller GmbH", "MÜLLER GmbH", "MÜLLER elsewhere GmbH"),
        ("ABC 2026", "ＡＢＣ　２０２６", "ABC elsewhere 2026"),
        ("alpha beta", "alpha\t\nbeta", "alpha elsewhere beta"),
    ],
)
def test_normalized_phrase_is_not_lost_at_the_candidate_limit(
    tmp_path, monkeypatch, query: str, exact: str, scattered: str
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-normalized")
    index.replace_doc_segments(
        "a-noise",
        [ParsedBlock(scattered, "line", {"line": number}) for number in range(1100)],
        version="v1",
    )
    index.replace_doc_segments("z-exact", [ParsedBlock(exact, "line", {"line": 1})], version="v1")
    index.replace_doc_segments("0-private", [ParsedBlock(exact, "line", {"line": 1})], version="v1")

    hits = search_base(
        "kb-normalized",
        query=query,
        ready_documents={"a-noise": object(), "z-exact": object()},
        limit=1,
    )

    assert [(hit.doc_id, hit.text) for hit, _document in hits] == [("z-exact", exact)]


@pytest.mark.parametrize("query", ["Müller", "STRASSE", "ABC"])
def test_legacy_chunks_use_the_same_unicode_normalization(
    tmp_path, monkeypatch, query: str
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-legacy")
    index.replace_doc_chunks("legacy", ["MÜLLER Straße ＡＢＣ"], [[1.0]])

    hits = index.search_text(query_terms(query), allowed_doc_ids=["legacy"])

    assert [(hit.doc_id, hit.text) for hit in hits] == [("legacy", "MÜLLER Straße ＡＢＣ")]


def test_chunk_batches_are_ordered_and_roll_back_on_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-batches")
    index.replace_doc_chunks("doc", ["old"], [[1.0]])

    def batches():
        yield (["first", "second"], [[1.0, 0.0], [0.0, 1.0]], [{"page": 1}, {"page": 2}])
        raise RuntimeError("later batch failed")

    try:
        index.replace_doc_chunk_batches("doc", batches())
    except RuntimeError as exc:
        assert str(exc) == "later batch failed"
    else:
        raise AssertionError("expected batch failure")
    assert [row[0] for row in index.doc_chunks("doc")] == ["old"]

    dimension = index.replace_doc_chunk_batches(
        "doc",
        iter(
            [
                (["first"], [[1.0, 0.0]], [{"page": 1}]),
                (["second"], [[0.0, 1.0]], [{"page": 2}]),
            ]
        ),
    )
    assert dimension == 2
    assert index.doc_chunks("doc") == [
        ("first", [1.0, 0.0], {"page": 1}),
        ("second", [0.0, 1.0], {"page": 2}),
    ]


def test_document_preview_is_bounded_and_ordinal_ordered(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    index = KnowledgeIndex("kb-preview")
    assert index.document_preview("missing", max_chars=10) is None
    index.replace_doc_segments(
        "doc",
        [
            ParsedBlock(" second ", "line", {"line": 2}),
            ParsedBlock("first", "line", {"line": 1}),
        ],
        version="v1",
    )
    assert index.document_preview("doc", max_chars=8) == "second\nf"
    assert index.document_preview("doc", max_chars=0) == ""
