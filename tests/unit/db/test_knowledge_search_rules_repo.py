"""Migration and persistence tests for per-user knowledge search rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_search_rules import KnowledgeSearchRulesRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def pool(tmp_path: Path) -> SqlitePool:
    result = SqlitePool(tmp_path / "octop.db")
    run_migrations(result)
    return result


def test_rules_are_user_scoped_and_cascade_with_documents(pool: SqlitePool) -> None:
    users = UserRepo(pool)
    owner = users.create(username="rules-owner", password_hash="h", role="user")
    other = users.create(username="rules-other", password_hash="h", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.get_enterprise_space()
    assert base is not None
    folder = knowledge.ensure_folder(base.id, "contracts")
    file = knowledge.create_document(
        kb_id=base.id,
        filename="terms.md",
        path="contracts/terms.md",
        content_type="text/markdown",
        byte_size=1,
    )
    rules = KnowledgeSearchRulesRepo(pool)
    rules.set(document_id=folder.id, user_id=owner, mode="keyword", keywords=["renewal"])
    rules.set(document_id=file.id, user_id=owner, mode="exclude", keywords=[])
    rules.set(document_id=file.id, user_id=other, mode="hybrid", keywords=[])

    assert rules.get(document_id=folder.id, user_id=owner).mode == "keyword"
    assert rules.get(document_id=file.id, user_id=owner).mode == "exclude"
    assert rules.get(document_id=file.id, user_id=other).mode == "hybrid"
    renamed = knowledge.rename_document(base.id, file.id, "renewed.md")
    assert renamed is not None and renamed.path == "contracts/renewed.md"
    assert rules.get(document_id=file.id, user_id=owner) is not None
    knowledge.delete_document(file.id)
    assert rules.get(document_id=file.id, user_id=owner) is None
    assert rules.get(document_id=file.id, user_id=other) is None
    assert rules.get(document_id=folder.id, user_id=owner) is not None
    knowledge.delete_document(folder.id)
    assert rules.get(document_id=folder.id, user_id=owner) is None


def test_rule_vectors_survive_rename_but_not_keyword_edit_or_rule_removal(
    pool: SqlitePool,
) -> None:
    owner = UserRepo(pool).create(username="vector-owner", password_hash="h", role="user")
    other = UserRepo(pool).create(username="vector-other", password_hash="h", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.get_enterprise_space()
    assert base is not None
    folder = knowledge.ensure_folder(base.id, "contracts")
    rules = KnowledgeSearchRulesRepo(pool)
    rules.set(document_id=folder.id, user_id=owner, mode="keyword", keywords=["劳动合同"])
    rules.cache_vectors(
        document_id=folder.id,
        user_id=owner,
        model_key="onnx/model-a",
        vectors={"劳动合同": [1.0, 0.0]},
    )
    key = (folder.id, "劳动合同")
    assert rules.cached_vectors(user_id=owner, model_key="onnx/model-a")[key] == (
        1.0,
        0.0,
    )
    assert rules.cached_vectors(user_id=other, model_key="onnx/model-a") == {}
    knowledge.rename_document(base.id, folder.id, "legal")
    rules.set(document_id=folder.id, user_id=owner, mode="hybrid", keywords=["劳动合同"])
    assert key in rules.cached_vectors(user_id=owner, model_key="onnx/model-a")
    rules.set(document_id=folder.id, user_id=owner, mode="hybrid", keywords=["雇佣协议"])
    assert rules.cached_vectors(user_id=owner, model_key="onnx/model-a") == {}
    rules.cache_vectors(
        document_id=folder.id,
        user_id=owner,
        model_key="onnx/model-b",
        vectors={"雇佣协议": [0.0, 1.0]},
    )
    knowledge.delete_document(folder.id)
    assert rules.cached_vectors(user_id=owner, model_key="onnx/model-b") == {}


def test_current_watermark_repairs_missing_rule_vector_cache(pool: SqlitePool) -> None:
    with pool.connect() as conn:
        conn.execute("DROP TABLE knowledge_search_rule_vectors")
    run_migrations(pool)
    with pool.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_search_rule_vectors'"
        ).fetchone()


def test_v37_upgrade_preserves_folder_rules_and_removes_extraction_data(
    tmp_path: Path, upgrade_through, monkeypatch
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    upgrade_through(pool, 37)
    users = UserRepo(pool)
    owner = users.create(username="upgrade-owner", password_hash="h", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.get_enterprise_space()
    assert base is not None
    folder = knowledge.ensure_folder(base.id, "contracts")
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO knowledge_folder_search_rules "
            "(folder_document_id, user_id, mode, keywords_json, updated_at) "
            "VALUES (?, ?, 'keyword', '[\"renewal\"]', 1)",
            (folder.id, owner),
        )
        conn.execute(
            "INSERT INTO knowledge_extract_templates (id, name, created_at, updated_at) "
            "VALUES ('old-template', 'Obsolete', 1, 1)"
        )
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 37

    monkeypatch.undo()
    run_migrations(pool)
    run_migrations(pool)
    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_list(knowledge_search_rules)").fetchall()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert version == 40
    assert {row["table"] for row in foreign_keys} == {"knowledge_documents", "users"}
    assert "knowledge_folder_search_rules" not in tables
    assert {
        "knowledge_extract_templates",
        "knowledge_extract_template_versions",
        "knowledge_extract_bindings",
        "knowledge_extract_results",
    }.isdisjoint(tables)
    rules = KnowledgeSearchRulesRepo(pool)
    assert rules.get(document_id=folder.id, user_id=owner).keywords == ("renewal",)


def test_stale_watermark_merges_both_rule_tables_without_replacing_current_rows(
    pool: SqlitePool,
) -> None:
    owner = UserRepo(pool).create(username="stale-rules", password_hash="h", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.get_enterprise_space()
    assert base is not None
    current = knowledge.ensure_folder(base.id, "contracts")
    legacy = knowledge.ensure_folder(base.id, "reports")
    rules = KnowledgeSearchRulesRepo(pool)
    rules.set(document_id=current.id, user_id=owner, mode="hybrid", keywords=["current"])
    with pool.connect() as conn:
        conn.execute(
            "CREATE TABLE knowledge_folder_search_rules ("
            "folder_document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id), "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "mode TEXT NOT NULL, keywords_json TEXT NOT NULL, updated_at INTEGER NOT NULL, "
            "PRIMARY KEY (folder_document_id, user_id))"
        )
        conn.execute(
            "INSERT INTO knowledge_folder_search_rules VALUES (?, ?, 'exclude', '[]', 1)",
            (current.id, owner),
        )
        conn.execute(
            "INSERT INTO knowledge_folder_search_rules VALUES (?, ?, ?, ?, 1)",
            (legacy.id, owner, "keyword", '["legacy"]'),
        )
        conn.execute("UPDATE _schema_version SET version = 37")

    run_migrations(pool)
    run_migrations(pool)
    assert rules.get(document_id=current.id, user_id=owner).mode == "hybrid"
    assert rules.get(document_id=legacy.id, user_id=owner).keywords == ("legacy",)
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='knowledge_folder_search_rules'"
            ).fetchone()
            is None
        )
