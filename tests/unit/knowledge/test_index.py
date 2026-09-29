"""Unit tests for per-knowledge-base sidecar indexes."""

from __future__ import annotations

from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.parse import ParsedBlock
from octop.infra.knowledge.search import query_terms


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
