"""Unit tests for DataSourceRepo and the v20 ``data_sources`` table."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> DataSourceRepo:
    return DataSourceRepo(db)


@pytest.fixture
def owner_id(db: SqlitePool) -> int:
    return UserRepo(db).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def kb_id(db: SqlitePool, owner_id: int) -> str:
    return KnowledgeRepo(db).create_base(owner_user_id=owner_id, name="Docs").id


def test_data_sources_table_migrated(db: SqlitePool) -> None:
    with db.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(data_sources)").fetchall()}
        target = conn.execute("PRAGMA foreign_key_list(data_sources)").fetchall()
    assert version == 21
    assert cols == {
        "id",
        "knowledge_base_id",
        "name",
        "kind",
        "config_json",
        "created_by",
        "sync_status",
        "sync_error",
        "last_synced_at",
        "created_at",
        "updated_at",
    }
    assert ("knowledge_base_id", "knowledge_bases", "CASCADE") in {
        (r["from"], r["table"], r["on_delete"]) for r in target
    }


def test_create_get_and_delete_round_trip(repo: DataSourceRepo, kb_id: str, owner_id: int) -> None:
    row = repo.create(
        knowledge_base_id=kb_id,
        name="Handbook",
        kind="url",
        config={"url": "https://example.com/handbook"},
        created_by=owner_id,
    )

    assert repo.get(row.id) == row
    assert repo.list_for_base(kb_id) == [row]
    assert row.created_by == owner_id
    assert row.config == {"url": "https://example.com/handbook"}
    assert row.sync_status == "idle"
    assert row.sync_error is None
    assert row.last_synced_at is None

    repo.delete(row.id)
    assert repo.get(row.id) is None
    assert repo.list_for_base(kb_id) == []


def test_mark_sync_stamps_time_only_on_a_successful_run(repo: DataSourceRepo, kb_id: str) -> None:
    row = repo.create(knowledge_base_id=kb_id, name="Handbook", kind="upload")

    repo.mark_sync(row.id, status="failed", error="no embedding backend")
    failed = repo.get(row.id)
    assert failed is not None
    assert failed.sync_status == "failed"
    assert failed.sync_error == "no embedding backend"
    # A failed attempt must not look like a successful one.
    assert failed.last_synced_at is None

    repo.mark_sync(row.id, status="ok", error=None, synced_at=1234)
    synced = repo.get(row.id)
    assert synced is not None
    assert synced.sync_status == "ok"
    assert synced.sync_error is None
    assert synced.last_synced_at == 1234


def test_deleting_a_knowledge_base_cascades_to_its_data_sources(
    db: SqlitePool, repo: DataSourceRepo, kb_id: str
) -> None:
    row = repo.create(knowledge_base_id=kb_id, name="Handbook", kind="upload")

    KnowledgeRepo(db).delete_base(kb_id)

    assert repo.get(row.id) is None
    assert repo.list_for_base(kb_id) == []
