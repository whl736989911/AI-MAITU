"""Metadata schema and value handling through the knowledge service."""

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
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.utils.paths import PathLayout

SCHEMA = [
    {"key": "drawing_no", "label": "Drawing no.", "type": "text", "required": True},
    {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A0", "A1"]},
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


def test_owner_sets_schema_metadata_and_filters_documents(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(owner_user_id=owner, name="Drawings")
    document = _upload(service, kb.id, owner)

    fields = service.update_metadata_schema(kb.id, actor_user_id=owner, schema=SCHEMA)
    assert [field["key"] for field in fields] == ["drawing_no", "sheet"]
    assert service.get_metadata_schema(kb.id, actor_user_id=owner) == fields

    values = service.update_document_metadata(
        kb.id,
        document.id,
        actor_user_id=owner,
        metadata={"drawing_no": "A-102", "sheet": "A0"},
    )
    assert values == {"drawing_no": "A-102", "sheet": "A0"}

    matched = service.list_documents(
        kb.id, actor_user_id=owner, metadata_filters={"drawing_no": "a-102"}
    )
    assert [row.id for row in matched] == [document.id]
    assert (
        service.list_documents(
            kb.id, actor_user_id=owner, metadata_filters={"drawing_no": "missing"}
        )
        == []
    )
    assert service._services.knowledge_repo.get_document(document.id).metadata == values


def test_metadata_validation_rejects_unknown_and_required_gaps(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(owner_user_id=owner, name="Drawings")
    document = _upload(service, kb.id, owner)
    service.update_metadata_schema(kb.id, actor_user_id=owner, schema=SCHEMA)

    with pytest.raises(OctopError) as missing:
        service.update_document_metadata(
            kb.id, document.id, actor_user_id=owner, metadata={"sheet": "A0"}
        )
    assert missing.value.code is ErrorCode.KNOWLEDGE_METADATA_INVALID

    with pytest.raises(OctopError) as unknown:
        service.update_document_metadata(
            kb.id,
            document.id,
            actor_user_id=owner,
            metadata={"drawing_no": "A-1", "typo": "x"},
        )
    assert unknown.value.code is ErrorCode.KNOWLEDGE_METADATA_INVALID


def test_non_owner_cannot_change_schema_or_document_metadata(service: KnowledgeService) -> None:
    users = service._services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    viewer = users.create(username="viewer", password_hash="h", role="user")
    kb = service._services.knowledge_repo.create_base(
        owner_user_id=owner, name="Shared", shared=True
    )
    document = _upload(service, kb.id, owner)
    service.update_metadata_schema(kb.id, actor_user_id=owner, schema=SCHEMA)

    assert service.list_documents(kb.id, actor_user_id=viewer)[0].id == document.id
    with pytest.raises(PermissionError):
        service.update_metadata_schema(kb.id, actor_user_id=viewer, schema=SCHEMA)
    with pytest.raises(PermissionError):
        service.update_document_metadata(
            kb.id,
            document.id,
            actor_user_id=viewer,
            metadata={"drawing_no": "A-1"},
        )
