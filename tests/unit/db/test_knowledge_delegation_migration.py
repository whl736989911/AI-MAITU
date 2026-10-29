"""tests/unit/db/test_knowledge_delegation_migration.py

Schema v45: delegation edges, durable access notifications, and the per-grant
``can_delegate`` flag — fresh installs, upgrades from an applied-044 database,
and stamped-by-a-repair-pass convergence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db import migrate as migrate_module
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool

_DELEGATION_COLUMNS = {
    "id",
    "delegation_id",
    "kb_id",
    "document_id",
    "grantor_user_id",
    "grantee_user_id",
    "can_delegate",
    "created_at",
    "updated_at",
}
_NOTIFICATION_COLUMNS = {
    "id",
    "notification_id",
    "recipient_user_id",
    "kb_id",
    "resource_id",
    "resource_path",
    "event_kind",
    "access_retained",
    "payload_json",
    "read_at",
    "created_at",
}


def _columns(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _tables(pool: SqlitePool) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _version(pool: SqlitePool) -> int:
    with pool.connect() as conn:
        row = conn.execute("SELECT version FROM _schema_version").fetchone()
    return int(row[0])


def _indexes(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {str(row["name"]) for row in rows}


def test_fresh_migrations_create_delegation_schema(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)

    assert "knowledge_delegations" in _tables(pool)
    assert "knowledge_access_notifications" in _tables(pool)
    assert _columns(pool, "knowledge_delegations") >= _DELEGATION_COLUMNS
    assert _columns(pool, "knowledge_access_notifications") >= _NOTIFICATION_COLUMNS
    assert "can_delegate" in _columns(pool, "resource_acl_grants")
    assert _version(pool) >= 45
    assert "idx_knowledge_delegations_grantee" in _indexes(pool, "knowledge_delegations")
    assert "idx_knowledge_delegations_kb_document" in _indexes(pool, "knowledge_delegations")
    assert "idx_knowledge_access_notifications_recipient" in _indexes(
        pool, "knowledge_access_notifications"
    )


def test_upgrade_from_applied_044_adds_the_pair(tmp_path: Path) -> None:
    """A local instance that already applied 044 must still receive v45."""
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 44],
        )
        run_migrations(pool)
    assert _version(pool) == 44
    assert "knowledge_delegations" not in _tables(pool)
    assert "can_delegate" not in _columns(pool, "resource_acl_grants")

    run_migrations(pool)

    assert _version(pool) >= 45
    assert _columns(pool, "knowledge_delegations") >= _DELEGATION_COLUMNS
    assert _columns(pool, "knowledge_access_notifications") >= _NOTIFICATION_COLUMNS
    assert "can_delegate" in _columns(pool, "resource_acl_grants")


def test_upgrade_preserves_existing_rows(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 44],
        )
        run_migrations(pool)
        with pool.connect() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, disabled, created_at) "
                "VALUES ('keeper', 'h', 'user', 0, 0)"
            )

    run_migrations(pool)

    with pool.connect() as conn:
        row = conn.execute("SELECT username FROM users WHERE username = 'keeper'").fetchone()
    assert row is not None


def test_stamped_45_without_ddl_still_converges(tmp_path: Path) -> None:
    """A watermark clamped to 45 by an intermediate build gets the DDL anyway."""
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 44],
        )
        run_migrations(pool)
    with pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 45")

    run_migrations(pool)

    assert _columns(pool, "knowledge_delegations") >= _DELEGATION_COLUMNS
    assert "can_delegate" in _columns(pool, "resource_acl_grants")


def test_ensure_helper_is_idempotent(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)

    migrate_module._ensure_knowledge_delegation_schema(pool)
    migrate_module._ensure_knowledge_delegation_schema(pool)

    assert _columns(pool, "knowledge_delegations") >= _DELEGATION_COLUMNS
    assert _columns(pool, "knowledge_access_notifications") >= _NOTIFICATION_COLUMNS
    assert "can_delegate" in _columns(pool, "resource_acl_grants")


def test_delegation_edges_cascade_with_their_document(tmp_path: Path) -> None:
    """The FKs are the no-orphans guarantee: deleting the document is enough."""
    from octop.infra.db.repos.knowledge import KnowledgeRepo
    from octop.infra.db.repos.knowledge_delegations import KnowledgeDelegationRepo
    from octop.infra.db.repos.users import UserRepo

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    friend = users.create(username="friend", password_hash="h", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.create_base(owner_user_id=owner, name="Docs", shared=False)
    document = knowledge.create_document(
        kb_id=base.id, filename="a.md", content_type="text/markdown", byte_size=0
    )

    delegations = KnowledgeDelegationRepo(pool)
    with pool.transaction() as conn:
        row = delegations.upsert(
            kb_id=base.id,
            document_id=document.id,
            grantor_user_id=owner,
            grantee_user_id=friend,
            can_delegate=True,
            conn=conn,
        )
    assert [edge.id for edge in delegations.list_for_kb(base.id)] == [row.id]

    knowledge.delete_document(document.id)

    assert delegations.list_for_kb(base.id) == []
