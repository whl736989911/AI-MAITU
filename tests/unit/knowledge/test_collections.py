"""Regression coverage for independent owner-private knowledge collections."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.knowledge_search_rules import KnowledgeSearchRulesRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.parse import ParsedBlock
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="x", role="user")
    reader = users.create(username="reader", password_hash="x", role="user")
    knowledge = KnowledgeRepo(pool)
    kb = knowledge.create_base(owner_user_id=owner, name="Research", shared=True)
    folder = knowledge.ensure_folder(kb.id, "reports")
    child_folder = knowledge.ensure_folder(kb.id, "reports/child")
    child = knowledge.create_document(
        kb_id=kb.id,
        filename="match.md",
        path="reports/child/match.md",
        content_type="text/markdown",
        byte_size=80,
        content_hash="v1",
        status="ready",
    )
    sibling = knowledge.create_document(
        kb_id=kb.id,
        filename="other.md",
        path="reports/other.md",
        content_type="text/markdown",
        byte_size=60,
        content_hash="v1",
        status="ready",
    )
    knowledge.update_document(child.id, chunk_count=1)
    knowledge.update_document(sibling.id, chunk_count=1)
    index = KnowledgeIndex(kb.id)
    index.replace_doc_segments(
        child.id, [ParsedBlock("needle source body", "paragraph", {"line": 3})], version="v1"
    )
    index.replace_doc_segments(
        sibling.id, [ParsedBlock("unrelated text", "paragraph", {"line": 8})], version="v1"
    )
    services = SimpleNamespace(
        db=pool,
        user_repo=users,
        knowledge_repo=knowledge,
        knowledge_collections_repo=KnowledgeCollectionsRepo(pool),
        knowledge_search_rules_repo=KnowledgeSearchRulesRepo(pool),
        paths=PathLayout(tmp_path / "home"),
    )
    return SimpleNamespace(
        services=services,
        api=KnowledgeCollectionService(services),
        owner=owner,
        reader=reader,
        kb=kb,
        folder=folder,
        child_folder=child_folder,
        child=child,
        sibling=sibling,
        knowledge=knowledge,
        index=index,
    )


def test_parent_and_explicit_child_are_independent_blank_keyword_scopes(setup) -> None:
    state = setup
    collections = state.api.create(
        state.kb.id,
        [state.folder.id, state.child_folder.id, state.child.id],
        actor_user_id=state.owner,
    )
    assert len(collections) == 3
    assert [item["target_document_id"] for item in collections] == [
        state.folder.id,
        state.child_folder.id,
        state.child.id,
    ]
    for item in collections:
        result = state.api.documents(state.kb.id, item["id"], actor_user_id=state.owner)
        assert result["collection"]["keywords"] == []
        assert "needle source body" not in result["keyword_document"]
        assert "needle source body" not in result["reading_document"]
        assert Path(result["storage_path"], "keyword-index.md").is_file()
        assert Path(result["storage_path"], "reading-index.md").is_file()
        assert state.services.knowledge_collections_repo.entries(item["id"]) == []
    for item in collections:
        state.api.update(state.kb.id, item["id"], actor_user_id=state.owner, keywords=["needle"])
        result = state.api.documents(state.kb.id, item["id"], actor_user_id=state.owner)
        assert "needle source body" in result["keyword_document"]
    hits = state.api.search([state.kb.id], "needle", actor_user_id=state.owner)
    assert [(hit.document_id, hit.ordinal) for hit in hits] == [(state.child.id, 0)]


def test_keyword_partition_reclassification_read_accumulation_and_source_version(setup) -> None:
    state = setup
    collection = state.api.create(state.kb.id, [state.folder.id], actor_user_id=state.owner)[0]
    state.api.update(
        state.kb.id, collection["id"], actor_user_id=state.owner, keywords=["unrelated"]
    )
    reclassified = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert "unrelated text" in reclassified["keyword_document"]
    assert "needle source body" not in reclassified["keyword_document"]
    state.api.update(state.kb.id, collection["id"], actor_user_id=state.owner, keywords=["needle"])

    state.api.record_read(
        state.kb.id,
        state.child.id,
        {
            "verified": True,
            "text": "needle source body",
            "version": "v1",
            "segment_id": f"{state.child.id}:0",
        },
        actor_user_id=state.owner,
    )
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    entries = state.services.knowledge_collections_repo.entries(collection["id"])
    assert any(entry["read"] and entry["extractive_summary"] for entry in entries)
    assert result["reading_document"].count("needle source body") == 0
    assert "needle source body" in result["keyword_document"]

    state.knowledge.update_document(state.child.id, content_hash="v2")
    state.index.replace_doc_segments(
        state.child.id,
        [ParsedBlock("replacement version content", "paragraph", {"line": 9})],
        version="v2",
    )
    state.api.refresh_for_document(state.kb.id, state.child.id)
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert "needle source body" not in result["keyword_document"]
    assert "replacement version content" not in result["reading_document"]


def test_collection_owner_acl_and_personal_exclude_rule(setup) -> None:
    state = setup
    collection = state.api.create(state.kb.id, [state.child.id], actor_user_id=state.owner)[0]
    with pytest.raises((LookupError, PermissionError)):
        state.api.documents(
            state.kb.id,
            collection["id"],
            actor_user_id=state.reader,
            is_admin=True,
        )
    state.api.update(state.kb.id, collection["id"], actor_user_id=state.owner, keywords=["needle"])
    assert state.api.search([state.kb.id], "needle", actor_user_id=state.owner)
    assert state.api.search([state.kb.id], "needle", actor_user_id=state.reader) == []
    state.services.knowledge_search_rules_repo.set(
        document_id=state.folder.id, user_id=state.owner, mode="exclude", keywords=[]
    )
    assert state.api.search([state.kb.id], "needle", actor_user_id=state.owner) == []


def test_renamed_target_uses_stable_document_id_and_metadata_is_not_body_text(setup) -> None:
    state = setup
    collection = state.api.create(state.kb.id, [state.child.id], actor_user_id=state.owner)[0]
    state.index.replace_doc_segments(
        state.child.id,
        [ParsedBlock("unrelated body text", "paragraph", {"line": 3})],
        version="v1",
    )
    state.api.update(
        state.kb.id,
        collection["id"],
        actor_user_id=state.owner,
        keywords=["needle"],
    )
    state.knowledge.update_document(
        state.child.id, filename="needle.md", path="reports/child/needle.md"
    )
    state.api.refresh_for_document(state.kb.id, state.child.id)
    updated = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    entries = state.services.knowledge_collections_repo.entries(collection["id"])
    metadata_entry = next(entry for entry in entries if not entry["segment_id"])
    assert metadata_entry["metadata_matches"] == ["needle"]
    assert metadata_entry["text"] == ""
    assert updated["collection"]["target_document_id"] == state.child.id
    assert updated["collection"]["target_path"] == "reports/child/needle.md"
    assert "needle.md" in updated["keyword_document"]
    assert "unrelated body text" not in updated["keyword_document"]
    hit = state.api.search([state.kb.id], "needle", actor_user_id=state.owner)[0]
    assert hit.document_id == state.child.id
    assert hit.segment_id == ""
    assert hit.locator == {"kind": "file"}
    assert hit.match_kind == "filename"


def test_deleted_target_identity_does_not_attach_to_replacement_at_same_path(setup) -> None:
    state = setup
    collection = state.api.create(state.kb.id, [state.child.id], actor_user_id=state.owner)[0]
    state.knowledge.delete_document(state.child.id)
    replacement = state.knowledge.create_document(
        kb_id=state.kb.id,
        filename="match.md",
        path="reports/child/match.md",
        content_type="text/markdown",
        byte_size=90,
        content_hash="new-version",
        status="ready",
    )
    state.api.refresh_for_document(state.kb.id, state.child.id)
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert replacement.id != collection["target_document_id"]
    assert result["collection"]["coverage"]["total"] == 0
    assert "needle source body" not in result["keyword_document"]
