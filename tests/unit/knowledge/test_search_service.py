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
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import jobs as jobs_module
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.service import KnowledgeService

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
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        provider_repo=None,
    )
    monkeypatch.setattr(service_module, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_module, "assert_knowledge_usable", lambda *_a, **_k: None)
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

    assert [hit.document_id for hit in hits] == [document.id]
    assert hits[0].filename == "policy.md"
    assert hits[0].path == "policy.md"
    assert hits[0].ordinal == 0
    assert hits[0].title == "Refunds"
    assert "Refunds" in hits[0].snippet


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


def test_search_stays_within_one_base_when_asked_for_one(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    policies, _ = _indexed(env, people.owner, filename="policy.md", body=_POLICY)
    prices, _ = _indexed(env, people.owner, filename="prices.md", body=_PRICES, name="Prices")

    everywhere = env.knowledge.search(actor_user_id=people.owner, query="refunds")
    scoped = env.knowledge.search(actor_user_id=people.owner, kb_id=prices.id, query="refunds")

    assert {hit.kb_id for hit in everywhere} == {policies.id}
    assert scoped == []
