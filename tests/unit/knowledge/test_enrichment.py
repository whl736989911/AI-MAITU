"""AI enrichment stores pending suggestions and writes back only on apply."""

from __future__ import annotations

import json
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
from octop.infra.knowledge.enrichment import KnowledgeEnrichmentService
from octop.infra.knowledge.files import document_digest, write_document
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.utils.paths import PathLayout

MODEL_JSON = json.dumps(
    {
        "summary": "法兰图纸摘要",
        "keywords": ["法兰", "φ120", "检验"],
        "fields": {"drawing_no": "A-102", "sheet": "A9", "unknown": "x"},
    },
    ensure_ascii=False,
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
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
    monkeypatch.setattr(
        KnowledgeService,
        "preview_document",
        lambda self, kb_id, doc_id, **kwargs: {
            "title": "法兰图纸",
            "filename": "drawing.pdf",
            "text": "法兰 φ120，第5.3条 检验方法",
            "blocks": [],
        },
    )

    async def fake_ainvoke(_llm, _messages, timeout=None):
        return MODEL_JSON

    monkeypatch.setattr("octop.infra.utils.llm_text.ainvoke_text", fake_ainvoke)
    return SimpleNamespace(services=services, pool=pool)


def _setup(env: SimpleNamespace):
    services = env.services
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    kb = services.knowledge_repo.create_base(owner_user_id=owner, name="Enrichment")
    document = services.knowledge_repo.create_document(
        kb_id=kb.id, filename="drawing.pdf", content_type="application/pdf", byte_size=1
    )
    content = b"original document"
    write_document(kb.id, document.id, document.filename, content)
    services.knowledge_repo.update_document(
        document.id, status="ready", content_hash=document_digest(content)
    )
    service = KnowledgeService(services)
    service.update_metadata_schema(
        kb.id,
        actor_user_id=owner,
        schema=[
            {"key": "drawing_no", "label": "Drawing", "type": "text"},
            {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A0", "A1"]},
        ],
    )
    return owner, kb, document, service


def test_enrichment_stores_pending_suggestion_and_filters_fields(env: SimpleNamespace) -> None:
    owner, kb, document, _service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)

    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    assert suggestion["status"] == "pending"
    assert suggestion["summary"] == "法兰图纸摘要"
    assert suggestion["keywords"] == ["法兰", "φ120", "检验"]
    assert suggestion["fields"] == {"drawing_no": "A-102"}
    assert document.metadata == {}
    assert enrichment.list_suggestions(kb.id, actor_user_id=owner) == [suggestion]


def test_apply_writes_metadata_and_rejects_a_stale_suggestion(env: SimpleNamespace) -> None:
    owner, kb, document, service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    applied = enrichment.apply_suggestion(
        kb.id,
        suggestion["suggestion_id"],
        actor_user_id=owner,
        fields={"drawing_no": "A-102", "sheet": "A0"},
    )
    assert applied["status"] == "applied"
    assert service._services.knowledge_repo.get_document(document.id).metadata == {
        "drawing_no": "A-102",
        "sheet": "A0",
    }

    second = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    env.services.knowledge_repo.update_document(document.id, content_hash="v2")
    with pytest.raises(OctopError) as stale:
        enrichment.apply_suggestion(kb.id, second["suggestion_id"], actor_user_id=owner)
    assert stale.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED


def test_explicit_empty_review_value_clears_existing_metadata(env: SimpleNamespace) -> None:
    owner, kb, document, service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    service.update_document_metadata(
        kb.id, document.id, actor_user_id=owner, metadata={"drawing_no": "A-OLD"}
    )

    enrichment.apply_suggestion(
        kb.id,
        suggestion["suggestion_id"],
        actor_user_id=owner,
        fields={"drawing_no": ""},
    )

    assert service._services.knowledge_repo.get_document(document.id).metadata == {}


def test_reject_and_permission_checks(env: SimpleNamespace) -> None:
    owner, kb, document, _service = _setup(env)
    viewer = env.services.user_repo.create(username="viewer", password_hash="h", role="user")
    env.services.repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=kb.id,
            owner_user_id=owner,
            visibility="public",
            unit_key=None,
            version=1,
            permission="read",
        )
    )
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    rejected = enrichment.reject_suggestion(kb.id, suggestion["suggestion_id"], actor_user_id=owner)
    assert rejected["status"] == "rejected"

    with pytest.raises(PermissionError):
        enrichment.enrich_document(kb.id, document.id, actor_user_id=viewer, llm=object())
    pending = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    with pytest.raises(PermissionError):
        enrichment.apply_suggestion(kb.id, pending["suggestion_id"], actor_user_id=viewer)


def test_regenerating_supersedes_the_previous_pending_suggestion(env: SimpleNamespace) -> None:
    owner, kb, document, _service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    first = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    second = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    pending = enrichment.list_suggestions(kb.id, actor_user_id=owner, status="pending")
    assert [row["suggestion_id"] for row in pending] == [second["suggestion_id"]]
    assert (
        env.services.knowledge_repo.get_suggestion(first["suggestion_id"])["status"] == "rejected"
    )


def test_enrichment_bounds_model_keywords(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, kb, document, _service = _setup(env)

    async def huge_keyword(_llm, _messages, timeout=None):
        return json.dumps({"summary": "s", "keywords": ["k" * 5000], "fields": {}})

    monkeypatch.setattr("octop.infra.utils.llm_text.ainvoke_text", huge_keyword)
    suggestion = KnowledgeEnrichmentService(env.services).enrich_document(
        kb.id, document.id, actor_user_id=owner, llm=object()
    )
    assert suggestion["keywords"] == ["k" * 80]


def test_apply_rejects_reviewed_values_that_no_longer_match_the_schema(
    env: SimpleNamespace,
) -> None:
    owner, kb, document, service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    service.update_metadata_schema(
        kb.id,
        actor_user_id=owner,
        schema=[
            {"key": "drawing_no", "label": "Drawing", "type": "text"},
            {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A1"]},
        ],
    )

    with pytest.raises(OctopError) as invalid:
        enrichment.apply_suggestion(
            kb.id,
            suggestion["suggestion_id"],
            actor_user_id=owner,
            fields={"sheet": "A0"},
        )
    assert invalid.value.code is ErrorCode.KNOWLEDGE_METADATA_INVALID
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "pending"
    )


def test_apply_rejects_a_document_that_is_waiting_for_reindex(env: SimpleNamespace) -> None:
    owner, kb, document, _service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    env.services.knowledge_repo.update_document(document.id, status="pending")

    with pytest.raises(OctopError) as not_ready:
        enrichment.apply_suggestion(kb.id, suggestion["suggestion_id"], actor_user_id=owner)
    assert not_ready.value.code is ErrorCode.KNOWLEDGE_INDEX_NOT_READY


def test_apply_rejects_unknown_explicit_review_fields(env: SimpleNamespace) -> None:
    owner, kb, document, _service = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    with pytest.raises(OctopError) as invalid:
        enrichment.apply_suggestion(
            kb.id,
            suggestion["suggestion_id"],
            actor_user_id=owner,
            fields={"not_a_field": "x"},
        )
    assert invalid.value.code is ErrorCode.KNOWLEDGE_METADATA_INVALID
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "pending"
    )


def test_changed_document_during_generation_does_not_replace_current_suggestion(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, kb, document, _service = _setup(env)
    repo = env.services.knowledge_repo
    repo.update_document(document.id, content_hash="v1")

    async def document_changes(_llm, _messages, timeout=None):
        repo.update_document(document.id, content_hash="v2")
        repo.create_suggestion(
            suggestion_id="current-v2",
            document_id=document.id,
            kb_id=kb.id,
            content_hash="v2",
            summary="current content",
            keywords=[],
            fields={"drawing_no": "NEW"},
            model="test",
        )
        return MODEL_JSON

    monkeypatch.setattr("octop.infra.utils.llm_text.ainvoke_text", document_changes)
    enrichment = KnowledgeEnrichmentService(env.services)
    with pytest.raises(OctopError) as stale:
        enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    assert stale.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED
    pending = enrichment.list_suggestions(kb.id, actor_user_id=owner, status="pending")
    assert [row["suggestion_id"] for row in pending] == ["current-v2"]


def test_original_changed_during_generation_does_not_store_stale_suggestion(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, kb, document, _service = _setup(env)

    async def original_changes(_llm, _messages, timeout=None):
        write_document(kb.id, document.id, document.filename, b"externally changed")
        return MODEL_JSON

    monkeypatch.setattr("octop.infra.utils.llm_text.ainvoke_text", original_changes)
    enrichment = KnowledgeEnrichmentService(env.services)
    with pytest.raises(OctopError) as stale:
        enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    assert stale.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED
    assert enrichment.list_suggestions(kb.id, actor_user_id=owner) == []


def test_removed_schema_field_cannot_be_silently_applied(env: SimpleNamespace) -> None:
    owner, kb, document, knowledge = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    knowledge.update_metadata_schema(kb.id, actor_user_id=owner, schema=[])
    with pytest.raises(OctopError) as invalid:
        enrichment.apply_suggestion(kb.id, suggestion["suggestion_id"], actor_user_id=owner)
    assert invalid.value.code is ErrorCode.KNOWLEDGE_METADATA_INVALID
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "pending"
    )


def test_apply_checks_actual_original_bytes_not_only_the_cached_hash(env: SimpleNamespace) -> None:
    owner, kb, document, _knowledge = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    write_document(kb.id, document.id, document.filename, b"externally changed")
    with pytest.raises(OctopError) as stale:
        enrichment.apply_suggestion(kb.id, suggestion["suggestion_id"], actor_user_id=owner)
    assert stale.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED
    assert env.services.knowledge_repo.get_document(document.id).metadata == {}
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "pending"
    )
