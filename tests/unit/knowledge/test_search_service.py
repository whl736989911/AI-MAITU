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
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.knowledge import jobs as jobs_module
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.index import KnowledgeIndex
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
        repos=RepoBundle.from_pool(pool),
        config=SimpleNamespace(max_upload_bytes=1_000_000, max_upload_mb=1),
        knowledge_repo=KnowledgeRepo(pool),
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


def test_restricted_folder_acl_also_hides_public_file(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:

    base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Docs", shared=True
    )
    folder = env.services.knowledge_repo.ensure_folder(base.id, "private")
    document = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="private/public.md",
        content_type="text/markdown",
        content=b"ancestor secret phrase",
    )
    jobs_module.process_document(env.services, base.id, document.id)
    acl = ResourceAclRepo(env.services.db)
    acl.upsert(AclEntry("knowledge_document", folder.id, None, "private", None, 1))
    acl.upsert(AclEntry("knowledge_document", document.id, None, "public", None, 1))

    assert env.knowledge.list_documents(base.id, actor_user_id=people.other) == []
    assert not env.knowledge.search(
        actor_user_id=people.other, kb_id=base.id, query="ancestor secret"
    )
    with pytest.raises(PermissionError, match="document read"):
        env.knowledge.read_segment(
            base.id,
            document.id,
            f"{document.id}:1",
            actor_user_id=people.other,
        )


def test_file_grant_bypasses_restricted_ancestors_but_not_library_acl(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Docs", shared=True
    )
    folder = env.services.knowledge_repo.ensure_folder(base.id, "sealed")
    document = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="sealed/exception.md",
        content_type="text/markdown",
        content=b"directly granted secret phrase",
    )
    jobs_module.process_document(env.services, base.id, document.id)
    sibling = env.knowledge.upload_document(
        base.id,
        actor_user_id=people.owner,
        filename="sealed/sibling.md",
        content_type="text/markdown",
        content=b"hidden sibling confidential content",
    )
    jobs_module.process_document(env.services, base.id, sibling.id)
    acl = ResourceAclRepo(env.services.db)
    acl.upsert(AclEntry("knowledge_document", folder.id, None, "private", None, 1))
    acl.upsert(
        AclEntry(
            "knowledge_document",
            document.id,
            None,
            "private",
            None,
            1,
            grants=(("user", str(people.other)),),
        )
    )

    assert [
        row.id for row in env.knowledge.list_documents(base.id, actor_user_id=people.other)
    ] == [document.id]
    assert not any(
        row.path == folder.path
        for row in env.knowledge.list_documents(base.id, actor_user_id=people.other)
    )
    hits = env.knowledge.search(
        actor_user_id=people.other,
        kb_id=base.id,
        query="directly granted secret hidden sibling confidential",
    )
    assert {row.document_id for row in hits} == {document.id}
    assert env.knowledge.coverage(base.id, actor_user_id=people.other)["total"] == 1
    hit = next(row for row in hits if row.document_id == document.id)
    evidence = env.knowledge.read_segment(
        base.id, document.id, hit.segment_id, actor_user_id=people.other
    )
    assert evidence["verified"] is True
    assert "directly granted secret phrase" in evidence["text"]

    private_base = env.services.knowledge_repo.create_base(
        owner_user_id=people.owner, name="Private"
    )
    private_file = env.knowledge.upload_document(
        private_base.id,
        actor_user_id=people.owner,
        filename="exception.md",
        content_type="text/markdown",
        content=b"library still required",
    )
    acl.upsert(
        AclEntry(
            "knowledge_document",
            private_file.id,
            None,
            "private",
            None,
            1,
            grants=(("user", str(people.other)),),
        )
    )
    with pytest.raises(PermissionError, match="knowledge base read"):
        env.knowledge.preview_document(private_base.id, private_file.id, actor_user_id=people.other)
