"""Unit tests for knowledge retrieval during a chat turn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import retrieve as retrieve_module
from octop.infra.knowledge.citations import CITATIONS_MARKER_PREFIX
from octop.infra.knowledge.index import KnowledgeIndex


def test_retrieve_context_filters_unreadable_knowledge_base(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
    )
    reader = services.user_repo.create(username="reader", password_hash="h", role="user")
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    allowed = services.knowledge_repo.create_base(owner_user_id=reader, name="Allowed")
    hidden = services.knowledge_repo.create_base(owner_user_id=owner, name="Hidden")
    allowed_doc = services.knowledge_repo.create_document(
        kb_id=allowed.id,
        filename="allowed.md",
        content_type="text/markdown",
        byte_size=1,
        status="ready",
    )
    hidden_doc = services.knowledge_repo.create_document(
        kb_id=hidden.id,
        filename="hidden.md",
        content_type="text/markdown",
        byte_size=1,
        status="ready",
    )
    KnowledgeIndex(allowed.id).replace_doc_chunks(allowed_doc.id, ["allowed fact"], [[1.0, 0.0]])
    KnowledgeIndex(hidden.id).replace_doc_chunks(hidden_doc.id, ["secret fact"], [[1.0, 0.0]])
    services.settings_repo.set("knowledge_embedding_model", "test-model")
    monkeypatch.setattr(retrieve_module, "assert_knowledge_usable", lambda *_args: None)
    monkeypatch.setattr(
        retrieve_module, "embed_knowledge_texts", lambda _services, _texts: [[1.0, 0.0]]
    )

    context = asyncio.run(
        retrieve_module.retrieve_context(
            services,
            user_id=reader,
            query="What facts are available?",
            knowledge_base_ids=[allowed.id, hidden.id],
        )
    )

    assert "allowed fact" in context
    assert "allowed.md" in context
    assert CITATIONS_MARKER_PREFIX in context
    assert "secret fact" not in context
    assert "hidden.md" not in context


def _harness(tmp_path, monkeypatch):
    """A migrated database with the knowledge stubs a unit test can afford."""
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
    )
    services.settings_repo.set("knowledge_embedding_model", "test-model")
    monkeypatch.setattr(retrieve_module, "assert_knowledge_usable", lambda *_args: None)
    return services


def _ready_doc(services, kb_id: str, *, filename: str, chunk: str, embedding: list[float]):
    document = services.knowledge_repo.create_document(
        kb_id=kb_id,
        filename=filename,
        content_type="text/markdown",
        byte_size=1,
        status="ready",
    )
    KnowledgeIndex(kb_id).replace_doc_chunks(document.id, [chunk], [embedding])
    return document


def test_retrieve_context_merges_the_lexical_half(tmp_path, monkeypatch) -> None:
    """design §9: an identifier the embeddings rank elsewhere still comes first."""
    services = _harness(tmp_path, monkeypatch)
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Docs")
    _ready_doc(
        services,
        base.id,
        filename="policy.md",
        chunk="refunds take five business days",
        embedding=[1.0, 0.0],
    )
    _ready_doc(
        services,
        base.id,
        filename="invoice.md",
        chunk="INV-2026-0042 paid in full",
        embedding=[0.0, 1.0],
    )
    # The query vector points at the policy, so the vector half on its own ranks
    # the invoice below it; the words are what put the invoice first.
    monkeypatch.setattr(retrieve_module, "embed_knowledge_texts", lambda _s, _t: [[1.0, 0.0]])

    context = asyncio.run(
        retrieve_module.retrieve_context(
            services,
            user_id=owner,
            query="INV-2026-0042",
            knowledge_base_ids=[base.id],
        )
    )

    assert "INV-2026-0042" in context
    assert context.index("INV-2026-0042") < context.index("refunds take five business days")


def test_a_chunk_both_halves_found_appears_once(tmp_path, monkeypatch) -> None:
    services = _harness(tmp_path, monkeypatch)
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Docs")
    _ready_doc(
        services,
        base.id,
        filename="policy.md",
        chunk="refunds take five business days",
        embedding=[1.0, 0.0],
    )
    monkeypatch.setattr(retrieve_module, "embed_knowledge_texts", lambda _s, _t: [[1.0, 0.0]])

    context = asyncio.run(
        retrieve_module.retrieve_context(
            services,
            user_id=owner,
            query="business days",
            knowledge_base_ids=[base.id],
        )
    )

    assert context.count("refunds take five business days") == 1


def test_retrieve_context_still_answers_without_embeddings(tmp_path, monkeypatch) -> None:
    """design §1: the vector model is optional, so a down backend is not a blank turn."""
    services = _harness(tmp_path, monkeypatch)
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Docs")
    _ready_doc(
        services,
        base.id,
        filename="policy.md",
        chunk="refunds take five business days",
        embedding=[1.0, 0.0],
    )

    def _down(*_args, **_kwargs):
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(retrieve_module, "embed_knowledge_texts", _down)

    context = asyncio.run(
        retrieve_module.retrieve_context(
            services,
            user_id=owner,
            query="business days",
            knowledge_base_ids=[base.id],
        )
    )

    assert "refunds take five business days" in context


def test_retrieve_context_skips_empty_non_text_turn() -> None:
    context = asyncio.run(
        retrieve_module.retrieve_context(
            SimpleNamespace(),
            user_id=1,
            query=None,  # type: ignore[arg-type]
            knowledge_base_ids=["kb-1"],
        )
    )

    assert context == ""
