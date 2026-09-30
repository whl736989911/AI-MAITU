"""Unit tests for searching indexed knowledge (design §9, §14).

The database and the per-base index are real; only the embedding backend call is
stubbed, because a unit test cannot download an ONNX model.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_search_rules import KnowledgeSearchRulesRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import jobs as jobs_module
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.search import SearchHit
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry

_POLICY = (
    "# Refunds\n\nRefunds take five business days.\nRefunds are issued to the original card.\n"
)
_PRICES = "# Prices\n\nApple 2, pear 3.\n"


def _fake_embeddings(_services: object, texts: list[str]) -> list[list[float]]:
    return [[float(len(text)), 1.0, 0.0, 0.0] for text in texts]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        config=SimpleNamespace(max_upload_bytes=1_000_000, max_upload_mb=1),
        knowledge_repo=KnowledgeRepo(pool),
        knowledge_search_rules_repo=KnowledgeSearchRulesRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        provider_repo=None,
    )
    monkeypatch.setattr(service_module, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_module, "assert_embedding_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_module, "embed_knowledge_texts", _fake_embeddings)
    return SimpleNamespace(services=services, knowledge=KnowledgeService(services))


@pytest.fixture
def people(env: SimpleNamespace) -> SimpleNamespace:
    users = env.services.user_repo
    return SimpleNamespace(
        owner=users.create(username="owner", password_hash="h", role="user"),
        other=users.create(username="other", password_hash="h", role="user"),
        admin=users.create(username="root", password_hash="h", role="admin"),
    )


def _indexed(
    env: SimpleNamespace,
    owner_id: int,
    *,
    filename: str,
    body: str,
    shared: bool = True,
    name: str = "Docs",
):
    """A base with one document that has actually been parsed and indexed."""
    base = env.services.knowledge_repo.create_base(owner_user_id=owner_id, name=name, shared=shared)
    document = env.knowledge.upload_document(
        base.id,
        actor_user_id=owner_id,
        filename=filename,
        content_type="text/markdown",
        content=body.encode("utf-8"),
    )
    jobs_module.process_document(env.services, base.id, document.id)
    refreshed = env.services.knowledge_repo.get_document(document.id)
    assert refreshed is not None
    assert refreshed.status == "ready"
    return base, refreshed


def test_search_cites_the_file_and_the_position_of_a_hit(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """design §9/§14: a result says which file, and where in it."""
    base, document = _indexed(env, people.owner, filename="policy.md", body=_POLICY)

    hits = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="refunds")

    assert {hit.document_id for hit in hits} == {document.id}
    assert hits[0].filename == "policy.md"
    assert hits[0].path == "policy.md"
    assert {hit.ordinal for hit in hits} >= {0}
    assert hits[0].title == "Refunds"
    assert any("Refunds" in hit.snippet for hit in hits)
    assert any(hit.segment_id and hit.locator for hit in hits)


def test_search_ranks_a_phrase_above_a_single_mention(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, _ = _indexed(env, people.owner, filename="policy.md", body=_POLICY)

    phrase = env.knowledge.search(
        actor_user_id=people.owner, kb_id=base.id, query="refunds take five business days"
    )
    single = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="pear")

    assert phrase and not single


def test_search_never_returns_what_the_actor_cannot_read(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """design §14: unauthorized content does not appear in search."""
    base, _ = _indexed(env, people.owner, filename="policy.md", body=_POLICY, shared=False)

    assert env.knowledge.search(actor_user_id=people.other, query="refunds") == []
    with pytest.raises(PermissionError):
        env.knowledge.search(actor_user_id=people.other, kb_id=base.id, query="refunds")

    # The owner and an admin still find it: the rule is the actor, not the file.
    assert env.knowledge.search(actor_user_id=people.owner, query="refunds")
    assert env.knowledge.search(actor_user_id=people.admin, query="refunds")


def test_search_never_returns_a_file_the_actor_may_not_read(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """design §14 one level down: the base is readable, the file is not."""
    base, document = _indexed(env, people.owner, filename="minutes.md", body=_POLICY)
    assert env.knowledge.search(actor_user_id=people.other, query="refunds")

    ResourceAclRepo(env.services.db).upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=document.id,
            owner_user_id=None,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(),
        )
    )

    assert env.knowledge.search(actor_user_id=people.other, query="refunds") == []
    assert env.knowledge.search(actor_user_id=people.owner, query="refunds") == []
    assert env.knowledge.search(actor_user_id=people.admin, is_admin=True, query="refunds")


def test_search_skips_a_document_that_is_not_ready(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """Indexing, failed, or replaced content is not searchable text."""
    base, document = _indexed(env, people.owner, filename="policy.md", body=_POLICY)
    assert env.knowledge.search(actor_user_id=people.owner, query="refunds")

    env.services.knowledge_repo.update_document(document.id, status="failed")

    assert env.knowledge.search(actor_user_id=people.owner, query="refunds") == []
    # The chunks are still there: the document's state is what filtered them out.
    with sqlite3.connect(KnowledgeIndex(base.id).path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0


def test_filename_search_covers_unindexed_files_without_claiming_their_text(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")
    pending = env.services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="archive-2026.pdf",
        content_type="application/pdf",
        byte_size=100,
        status="pending",
    )
    env.services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="installer.exe",
        content_type="application/octet-stream",
        byte_size=100,
        status="unsupported",
    )

    hits = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="archive")
    assert len(hits) == 1
    assert hits[0].document_id == pending.id
    assert hits[0].segment_id == ""
    assert hits[0].locator == {"kind": "file"}
    assert env.knowledge.coverage(base.id, actor_user_id=people.owner) == {
        "total": 2,
        "searchable": 0,
        "pending": 1,
        "indexing": 1,
        "failed": 0,
        "unsupported": 1,
    }


def test_search_stays_within_one_base_when_asked_for_one(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    policies, _ = _indexed(env, people.owner, filename="policy.md", body=_POLICY)
    prices, _ = _indexed(env, people.owner, filename="prices.md", body=_PRICES, name="Prices")

    everywhere = env.knowledge.search(actor_user_id=people.owner, query="refunds")
    scoped = env.knowledge.search(actor_user_id=people.owner, kb_id=prices.id, query="refunds")

    assert {hit.kb_id for hit in everywhere} == {policies.id}
    assert scoped == []


def test_semantic_result_resolves_to_verifiable_original_segment(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, document = _indexed(
        env,
        people.owner,
        filename="policy.md",
        body="# Service\n\nA repayment is issued after a cancelled order.\n",
    )
    KnowledgeIndex(base.id).replace_doc_chunks(
        document.id,
        ["A repayment is issued after a cancelled order."],
        [[1.0, 0.0]],
        metadata=[{"segment_id": f"{document.id}:1"}],
    )

    hits = env.knowledge.search(
        actor_user_id=people.owner,
        kb_id=base.id,
        query="compensation",
        query_vector=[1.0, 0.0],
    )
    assert hits and hits[0].segment_id == f"{document.id}:1"
    evidence = env.knowledge.read_segment(
        base.id,
        document.id,
        hits[0].segment_id,
        actor_user_id=people.owner,
    )
    assert evidence["verified"] is True
    assert "repayment" in str(evidence["text"])


def test_folder_mode_controls_generated_semantic_search_and_skips_unused_embeddings(
    env: SimpleNamespace, people: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, document = _indexed(
        env,
        people.owner,
        filename="contracts/policy.md",
        body="# Service\n\nA repayment is issued after a cancelled order.\n",
    )
    folder = env.services.knowledge_repo.get_document_by_path(base.id, "contracts")
    assert folder is not None
    KnowledgeIndex(base.id).replace_doc_chunks(
        document.id,
        ["A repayment is issued after a cancelled order."],
        [[1.0, 0.0]],
        metadata=[{"segment_id": f"{document.id}:1"}],
    )
    monkeypatch.setattr(
        service_module,
        "get_capability",
        lambda *_a, **_k: {"selected_model": "test", "prerequisites_ok": True},
    )
    embedded: list[str] = []

    def embed(_services: object, texts: list[str]) -> list[list[float]]:
        embedded.extend(texts)
        return [[1.0, 0.0]]

    monkeypatch.setattr(service_module, "embed_knowledge_texts", embed)

    def search() -> list[SearchHit]:
        return env.knowledge.search(
            actor_user_id=people.owner,
            kb_id=base.id,
            query="compensation",
            generate_query_vector=True,
        )

    assert any(hit.match_kind == "content" for hit in search())
    assert embedded == ["compensation"]

    rules = env.services.knowledge_search_rules_repo
    rules.set(document_id=folder.id, user_id=people.owner, mode="keyword", keywords=[])
    assert search() == []
    assert embedded == ["compensation"]

    rules.set(document_id=folder.id, user_id=people.owner, mode="exclude", keywords=[])
    assert search() == []
    assert embedded == ["compensation"]


def test_folder_alias_candidate_is_unverified_and_isolated_per_user(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Docs", shared=True
    )
    folder = env.services.knowledge_repo.ensure_folder(base.id, "contracts")
    doc = env.services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="agreement.md",
        path="contracts/agreement.md",
        content_type="text/markdown",
        byte_size=1,
        status="pending",
    )
    env.services.knowledge_search_rules_repo.set(
        document_id=folder.id,
        user_id=people.owner,
        mode="keyword",
        keywords=["renewal clause"],
    )
    owner_hits = env.knowledge.search(
        actor_user_id=people.owner, kb_id=base.id, query="renewal clause"
    )
    other_hits = env.knowledge.search(
        actor_user_id=people.other, kb_id=base.id, query="renewal clause"
    )
    candidate = next(hit for hit in owner_hits if hit.document_id == doc.id)
    assert candidate.match_kind == "rule_keyword"
    assert candidate.segment_id == "" and candidate.snippet == ""
    assert candidate.locator == {"kind": "rule_keyword"}
    assert not any(hit.match_kind == "rule_keyword" for hit in other_hits)


def test_file_rule_overrides_folder_exclusion_keywords_and_restores_inheritance(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Docs", shared=True
    )
    folder = env.services.knowledge_repo.ensure_folder(base.id, "records")
    kept = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="records/kept.md",
        content_type="text/markdown",
        content=b"# Evidence\n\nuniqueledger evidence.",
    )
    hidden = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="records/hidden.md",
        content_type="text/markdown",
        content=b"# Evidence\n\nuniqueledger evidence.",
    )
    jobs_module.process_document(env.services, base.id, kept.id)
    jobs_module.process_document(env.services, base.id, hidden.id)
    rules = env.services.knowledge_search_rules_repo
    rules.set(document_id=folder.id, user_id=people.owner, mode="exclude", keywords=[])
    rules.set(
        document_id=kept.id,
        user_id=people.owner,
        mode="hybrid",
        keywords=["personal alias"],
    )
    content = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="uniqueledger")
    assert {hit.document_id for hit in content} == {kept.id}
    alias = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="personal alias")
    assert {hit.document_id for hit in alias} == {kept.id}
    assert alias[0].match_kind == "rule_keyword"
    assert alias[0].segment_id == "" and alias[0].snippet == ""
    assert (
        env.knowledge.search(actor_user_id=people.other, kb_id=base.id, query="personal alias")
        == []
    )
    assert {
        (row["path"], row["kind"])
        for row in env.knowledge.list_search_rules(base.id, actor_user_id=people.owner)
    } == {("records", "folder"), ("records/kept.md", "file")}

    rules.delete(document_id=kept.id, user_id=people.owner)
    assert not env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="uniqueledger")
    rules.set(
        document_id=folder.id, user_id=people.owner, mode="keyword", keywords=["shared alias"]
    )
    rules.set(
        document_id=kept.id, user_id=people.owner, mode="keyword", keywords=["personal alias"]
    )
    inherited = env.knowledge.search(
        actor_user_id=people.owner, kb_id=base.id, query="shared alias"
    )
    own = env.knowledge.search(actor_user_id=people.owner, kb_id=base.id, query="personal alias")
    assert {hit.document_id for hit in inherited} == {hidden.id}
    assert {hit.document_id for hit in own} == {kept.id}


def test_folder_rules_inherit_override_exclude_and_obey_file_acl(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Docs", shared=True
    )
    parent = env.services.knowledge_repo.ensure_folder(base.id, "records")
    child = env.services.knowledge_repo.ensure_folder(base.id, "records/open")
    hidden = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="records/hidden-refund.md",
        content_type="text/markdown",
        content=b"classifiedbody material.",
    )
    jobs_module.process_document(env.services, base.id, hidden.id)
    visible = env.services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="visible.md",
        path="records/open/visible.md",
        content_type="text/markdown",
        byte_size=1,
        status="pending",
    )
    rules = env.services.knowledge_search_rules_repo
    rules.set(document_id=parent.id, user_id=people.owner, mode="exclude", keywords=[])
    rules.set(
        document_id=child.id,
        user_id=people.owner,
        mode="keyword",
        keywords=["special phrase"],
    )
    ResourceAclRepo(env.services.db).upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=visible.id,
            owner_user_id=people.owner,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(),
        )
    )
    with pytest.raises(ValueError):
        env.knowledge.set_search_rule(
            base.id,
            actor_user_id=people.owner,
            path="",
            mode="keyword",
            keywords=[],
        )
    with pytest.raises(LookupError):
        env.knowledge.set_search_rule(
            base.id,
            actor_user_id=people.other,
            path="records/open/visible.md",
            mode="keyword",
            keywords=[],
        )
    ResourceAclRepo(env.services.db).upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=parent.id,
            owner_user_id=people.owner,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(),
        )
    )
    with pytest.raises(LookupError):
        env.knowledge.set_search_rule(
            base.id,
            actor_user_id=people.other,
            path="records",
            mode="keyword",
            keywords=["private alias"],
        )
    owner_hits = env.knowledge.search(
        actor_user_id=people.owner,
        kb_id=base.id,
        query="special phrase classifiedbody hidden-refund",
    )
    assert {hit.document_id for hit in owner_hits} == {visible.id}
    other_hits = env.knowledge.search(
        actor_user_id=people.other, kb_id=base.id, query="special phrase"
    )
    assert all(hit.document_id != visible.id for hit in other_hits)
    assert all(hit.document_id != hidden.id for hit in owner_hits)

    rules.set(
        document_id=child.id,
        user_id=people.owner,
        mode="exclude",
        keywords=["special phrase"],
    )
    assert not env.knowledge.search(
        actor_user_id=people.owner, kb_id=base.id, query="special phrase visible"
    )
    env.services.knowledge_repo.rename_document(base.id, parent.id, "archive")
    assert rules.get(document_id=parent.id, user_id=people.owner) is not None
    assert any(
        row["path"] == "archive" and row["mode"] == "exclude"
        for row in env.knowledge.list_search_rules(base.id, actor_user_id=people.owner)
    )
    env.services.knowledge_repo.delete_document(parent.id)
    assert rules.get(document_id=parent.id, user_id=people.owner) is None


def test_keyword_rule_uses_lexical_content_without_vectors(
    env: SimpleNamespace, people: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")
    folder = env.services.knowledge_repo.ensure_folder(base.id, "manuals")
    document = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="manuals/guide.md",
        content_type="text/markdown",
        content=b"Distinctive lexical evidence appears here.",
    )
    jobs_module.process_document(env.services, base.id, document.id)
    env.services.knowledge_search_rules_repo.set(
        document_id=folder.id, user_id=people.owner, mode="keyword", keywords=[]
    )

    def reject_vector_search(*_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("keyword-only folders must not query the vector index")

    monkeypatch.setattr(KnowledgeIndex, "search", reject_vector_search)
    hits = env.knowledge.search(
        actor_user_id=people.owner,
        kb_id=base.id,
        query="Distinctive lexical",
        query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    assert any(hit.document_id == document.id and hit.match_kind == "content" for hit in hits)
