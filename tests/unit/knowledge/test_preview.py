"""Bounded indexed previews and stale/unindexed document behavior."""

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle, SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.files import write_document
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.jobs import process_document
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def preview_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "control.sqlite")
    run_migrations(pool)
    services = SharedServices(PathLayout.from_env(), OctopConfig(), RepoBundle.from_pool(pool))
    services.settings_repo.set("knowledge_bases_enabled", "true")
    yield services
    pool.close()


def test_indexed_preview_preserves_boundaries_and_refuses_changed_source(preview_services):
    services = preview_services
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Preview")
    knowledge = KnowledgeService(services)
    document = knowledge.upload_document(
        base.id,
        actor_user_id=owner,
        filename="notes.md",
        content_type="text/markdown",
        content=b"# Heading\nFirst paragraph.\nSecond paragraph.",
    )
    process_document(services, base.id, document.id)
    preview = knowledge.preview_document(base.id, document.id, actor_user_id=owner)
    assert preview["text"] == "# Heading\nFirst paragraph.\nSecond paragraph."
    assert preview["title"] == "Heading"
    assert preview["sections"] == ["Heading"]
    write_document(base.id, document.id, document.filename, b"Changed source")
    with pytest.raises(OctopError) as refused:
        knowledge.preview_document(base.id, document.id, actor_user_id=owner)
    assert refused.value.code == ErrorCode.KNOWLEDGE_INDEX_NOT_READY


def test_preview_blocks_carry_source_locators(preview_services):
    """Every previewed paragraph points back to its own source line."""
    services = preview_services
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Located")
    knowledge = KnowledgeService(services)
    document = knowledge.upload_document(
        base.id,
        actor_user_id=owner,
        filename="ledger.txt",
        content_type="text/plain",
        content=b"line one\n\nline three\nline four",
    )
    process_document(services, base.id, document.id)

    preview = knowledge.preview_document(base.id, document.id, actor_user_id=owner)

    assert preview["text"] == "line one\nline three\nline four"
    assert [(block["text"], block["locator"]) for block in preview["blocks"]] == [
        ("line one", {"line": 1}),
        ("line three", {"line": 3}),
        ("line four", {"line": 4}),
    ]


def test_unindexed_pdf_preview_waits_for_index_without_parsing(preview_services):
    services = preview_services
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Scans")
    knowledge = KnowledgeService(services)
    document = knowledge.upload_document(
        base.id,
        actor_user_id=owner,
        filename="scan.pdf",
        content_type="application/pdf",
        content=b"not a PDF; preview must not invoke a parser",
    )
    with pytest.raises(OctopError) as refused:
        knowledge.preview_document(base.id, document.id, actor_user_id=owner)
    assert refused.value.code == ErrorCode.KNOWLEDGE_INDEX_NOT_READY
    assert refused.value.status == 409
    assert KnowledgeIndex(base.id).document_preview(document.id, max_chars=100) is None


def test_unindexed_text_preview_is_bounded(preview_services):
    services = preview_services
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Draft")
    knowledge = KnowledgeService(services)
    document = knowledge.upload_document(
        base.id,
        actor_user_id=owner,
        filename="draft.txt",
        content_type="text/plain",
        content=("正文" * 110_000 + "不应出现在预览中的尾部").encode(),
    )
    preview = knowledge.preview_document(base.id, document.id, actor_user_id=owner)
    assert preview["text"] == "正文" * 100_000
