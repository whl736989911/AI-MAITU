"""AI enrichment stores pending suggestions and writes back only on apply."""

from __future__ import annotations

import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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


def test_concurrent_reject_cannot_succeed_after_review_is_applied(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, kb, document, _knowledge = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    writing = threading.Event()
    release = threading.Event()
    reject_progress: queue.Queue[None] = queue.Queue()
    original_update = KnowledgeService.update_document_metadata
    original_lock = service_module._knowledge_mutation_lock

    def paused_update(knowledge, *args, **kwargs):
        writing.set()
        if not release.wait(5):
            raise TimeoutError("reviewed metadata write was not released")
        return original_update(knowledge, *args, **kwargs)

    @contextmanager
    def observed_lock(kb_id):
        if threading.current_thread().name.startswith("review-reject"):
            reject_progress.put(None)
        with original_lock(kb_id):
            yield

    def reject():
        try:
            return enrichment.reject_suggestion(
                kb.id, suggestion["suggestion_id"], actor_user_id=owner
            )
        finally:
            reject_progress.put(None)

    monkeypatch.setattr(KnowledgeService, "update_document_metadata", paused_update)
    monkeypatch.setattr(service_module, "_knowledge_mutation_lock", observed_lock)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="review-apply") as apply_pool,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="review-reject") as reject_pool,
    ):
        applying = apply_pool.submit(
            enrichment.apply_suggestion, kb.id, suggestion["suggestion_id"], actor_user_id=owner
        )
        try:
            assert writing.wait(5)
            rejecting = reject_pool.submit(reject)
            reject_progress.get(timeout=5)
        finally:
            release.set()
        assert applying.result(timeout=5)["status"] == "applied"
        with pytest.raises(OctopError) as conflict:
            rejecting.result(timeout=5)
    assert conflict.value.code is ErrorCode.KNOWLEDGE_REQUEST_INVALID
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "applied"
    )
    assert env.services.knowledge_repo.get_document(document.id).metadata == {"drawing_no": "A-102"}


@pytest.mark.parametrize("status", ["pending", "ready"])
def test_enrichment_refuses_a_document_without_a_source_version(
    env: SimpleNamespace, status: str
) -> None:
    owner, kb, document, _knowledge = _setup(env)
    env.services.knowledge_repo.update_document(document.id, status=status, content_hash="")
    enrichment = KnowledgeEnrichmentService(env.services)

    with pytest.raises(OctopError) as error:
        enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())

    assert error.value.code is ErrorCode.KNOWLEDGE_INDEX_NOT_READY
    assert enrichment.list_suggestions(kb.id, actor_user_id=owner) == []


def test_legacy_unversioned_suggestion_cannot_apply_to_changed_content(
    env: SimpleNamespace,
) -> None:
    owner, kb, document, _knowledge = _setup(env)
    repo = env.services.knowledge_repo
    suggestion = repo.create_suggestion(
        suggestion_id="legacy-unversioned",
        document_id=document.id,
        kb_id=kb.id,
        content_hash="",
        summary="Old suggestion",
        keywords=[],
        fields={"drawing_no": "OLD"},
        model="test/model",
    )
    changed = b"changed document"
    write_document(kb.id, document.id, document.filename, changed)
    repo.update_document(document.id, status="ready", content_hash=document_digest(changed))

    with pytest.raises(OctopError) as error:
        KnowledgeEnrichmentService(env.services).apply_suggestion(
            kb.id, suggestion["suggestion_id"], actor_user_id=owner
        )

    assert error.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED
    assert repo.get_document(document.id).metadata == {}
    assert repo.get_suggestion(suggestion["suggestion_id"])["status"] == "pending"


def test_versioned_suggestion_cannot_apply_after_document_version_is_cleared(
    env: SimpleNamespace,
) -> None:
    owner, kb, document, _knowledge = _setup(env)
    enrichment = KnowledgeEnrichmentService(env.services)
    suggestion = enrichment.enrich_document(kb.id, document.id, actor_user_id=owner, llm=object())
    env.services.knowledge_repo.update_document(document.id, content_hash="")

    with pytest.raises(OctopError) as error:
        enrichment.apply_suggestion(kb.id, suggestion["suggestion_id"], actor_user_id=owner)

    assert error.value.code is ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED
    assert env.services.knowledge_repo.get_document(document.id).metadata == {}
    assert (
        env.services.knowledge_repo.get_suggestion(suggestion["suggestion_id"])["status"]
        == "pending"
    )
