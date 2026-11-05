"""Fact storage, permission filtering and search through the knowledge service."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.utils.paths import PathLayout

FACTS = [
    {
        "fact_type": "diameter",
        "value": "φ120",
        "normalized": "φ120",
        "locator": {"page": 1},
        "extractor": "measurement",
    },
    {
        "fact_type": "clause",
        "value": "5.3",
        "normalized": "5.3",
        "locator": {"page": 2},
        "extractor": "clause",
    },
]


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeService:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        repos=RepoBundle.from_pool(pool),
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        paths=PathLayout.from_env(),
    )
    monkeypatch.setattr(service_module, "assert_knowledge_usable", lambda *_args: None)
    return KnowledgeService(services)


def _upload(service: KnowledgeService, kb_id: str, owner: int, name: str = "drawing.pdf"):
    return service.upload_document(
        kb_id,
        actor_user_id=owner,
        filename=name,
        content_type="application/pdf",
        content=b"%PDF-1.4\n",
    )


def test_document_facts_are_stored_and_searchable(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(owner_user_id=owner, name="Facts")
    document = _upload(service, kb.id, owner)
    service._services.knowledge_repo.replace_document_facts(document.id, kb.id, FACTS)

    stored = service.document_facts(kb.id, document.id, actor_user_id=owner)
    assert {fact["normalized"] for fact in stored} == {"φ120", "5.3"}

    found = service.search_facts(kb.id, actor_user_id=owner, query="120")
    assert [fact["value"] for fact in found] == ["φ120"]
    assert found[0]["locator"] == {"page": 1}
    assert found[0]["document_id"] == document.id

    clauses = service.search_facts(kb.id, actor_user_id=owner, fact_type="clause")
    assert [fact["value"] for fact in clauses] == ["5.3"]


def test_readers_only_see_facts_of_readable_documents(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    viewer = users.create(username="viewer", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(
        owner_user_id=owner, name="Shared facts", shared=True
    )
    visible = _upload(service, kb.id, owner, "visible.pdf")
    hidden = _upload(service, kb.id, owner, "hidden.pdf")
    repo = service._services.knowledge_repo
    repo.replace_document_facts(visible.id, kb.id, FACTS)
    repo.replace_document_facts(hidden.id, kb.id, FACTS)
    acl_repo = service._services.repos.resource_acl_repo
    acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=hidden.id,
            owner_user_id=owner,
            visibility="private",
            unit_key=None,
            version=1,
            permission="read",
        )
    )

    found = service.search_facts(kb.id, actor_user_id=viewer, query="φ120")
    assert {fact["document_id"] for fact in found} == {visible.id}
    with pytest.raises(PermissionError):
        service.document_facts(kb.id, hidden.id, actor_user_id=viewer)


def test_fact_search_treats_like_wildcards_literally(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(owner_user_id=owner, name="Wildcards")
    document = _upload(service, kb.id, owner)
    service._services.knowledge_repo.replace_document_facts(
        document.id,
        kb.id,
        FACTS
        + [
            {
                "fact_type": "tolerance",
                "value": "50%",
                "normalized": "50%",
                "locator": {"page": 3},
                "extractor": "measurement",
            }
        ],
    )

    assert [
        fact["value"] for fact in service.search_facts(kb.id, actor_user_id=owner, query="%")
    ] == ["50%"]
    assert service.search_facts(kb.id, actor_user_id=owner, query="_") == []
