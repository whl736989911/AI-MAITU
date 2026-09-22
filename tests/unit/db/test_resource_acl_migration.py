"""tests/unit/db/test_resource_acl_migration.py"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo

_ACL_TABLES = ("resource_acl", "resource_acl_grants", "resource_acl_changes")


def _table_names(pool: SqlitePool) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def _columns(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _version(pool: SqlitePool) -> int:
    with pool.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def _acl_rows(pool: SqlitePool) -> list[tuple]:
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT resource_type, resource_id, owner_user_id, visibility, unit_key, version "
            "FROM resource_acl ORDER BY resource_type, resource_id"
        ).fetchall()
    return [tuple(r) for r in rows]


def _space_id(pool: SqlitePool) -> str:
    """The v27 enterprise space's id."""
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1"
        ).fetchone()
    return str(row["knowledge_base_id"])


def _legacy_acl_rows(pool: SqlitePool) -> list[tuple]:
    """``_acl_rows`` without the enterprise space.

    The space is seeded by a later migration than the one under test, and it
    has no legacy flag to mirror, so it is not part of what these assertions
    are about. Its own row is pinned in ``test_enterprise_space_acl_is_public``.
    """
    space = _space_id(pool)
    return [row for row in _acl_rows(pool) if row[1] != space]


def _legacy_v17_db(tmp_path: Path) -> SqlitePool:
    """A v17-shaped database: no ACL tables, legacy boolean flags populated."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        for table in ("resource_acl_changes", "resource_acl_grants", "resource_acl"):
            conn.execute(f"DROP TABLE {table}")
        # Schema v21 dropped these three; a v17 database still had them, so put
        # them back to keep the simulated upgrade faithful.
        conn.execute("ALTER TABLE agents ADD COLUMN is_shared INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE connectors ADD COLUMN shared INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE knowledge_bases ADD COLUMN shared INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'alice', 'h', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (2, 'bob', 'h', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, created_at, updated_at) "
            "VALUES ('ag_shared', 1, 'Shared', 1, 1, 10, 10)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, created_at, updated_at) "
            "VALUES ('ag_private', 1, 'Private', 1, 0, 11, 11)"
        )
        conn.execute(
            "INSERT INTO connectors(instance_id, user_id, kind, display_name, mcp_server_name, "
            "shared, created_at, updated_at) VALUES ('cn_shared', 2, 'custom-mcp', 'Conn', 'mc-1', 1, 12, 12)"
        )
        conn.execute(
            "INSERT INTO knowledge_bases(knowledge_base_id, owner_user_id, name, shared, created_at, updated_at) "
            "VALUES ('kb_shared', 2, 'KB', 1, 13, 13)"
        )
        conn.execute("UPDATE _schema_version SET version = 17")
    return pool


def test_run_migrations_creates_acl_tables(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)

    assert _version(pool) == 27
    assert set(_ACL_TABLES).issubset(_table_names(pool))
    assert _columns(pool, "resource_acl") == {
        "resource_type",
        "resource_id",
        "owner_user_id",
        "visibility",
        "unit_key",
        "version",
        "updated_at",
    }
    assert _columns(pool, "resource_acl_grants") == {
        "resource_type",
        "resource_id",
        "grantee_type",
        "grantee_id",
    }
    assert _columns(pool, "resource_acl_changes") == {
        "id",
        "resource_type",
        "resource_id",
        "actor_user_id",
        "from_version",
        "to_version",
        "before_json",
        "after_json",
        "impact_scope",
        "status",
        "reason",
        "created_at",
    }


def test_acl_tables_reject_duplicate_rows(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'alice', 'h', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO resource_acl(resource_type, resource_id, owner_user_id, visibility, "
            "unit_key, version, updated_at) VALUES ('agent', 'ag1', 1, 'private', NULL, 1, 1)"
        )
        conn.execute(
            "INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, grantee_id) "
            "VALUES ('agent', 'ag1', 'user', '2')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO resource_acl(resource_type, resource_id, owner_user_id, visibility, "
                "unit_key, version, updated_at) VALUES ('agent', 'ag1', 1, 'public', NULL, 1, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, grantee_id) "
                "VALUES ('agent', 'ag1', 'user', '2')"
            )


def test_enterprise_space_acl_is_public(tmp_path: Path) -> None:
    """The space is seeded with the row that makes it readable.

    Access is granted by an ACL row and never by its absence, so a seeded space
    without this row would be an enterprise knowledge base nobody could read.
    """
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)

    assert _acl_rows(pool) == [("knowledge_base", _space_id(pool), None, "public", None, 1)]


def test_upgrade_from_v17_backfills_legacy_shared_flags(tmp_path: Path) -> None:
    pool = _legacy_v17_db(tmp_path)

    run_migrations(pool)

    assert _version(pool) == 27
    assert _legacy_acl_rows(pool) == [
        ("agent", "ag_private", 1, "private", None, 1),
        ("agent", "ag_shared", 1, "public", None, 1),
        ("connector", "cn_shared", 2, "public", None, 1),
        ("knowledge_base", "kb_shared", 2, "public", None, 1),
    ]


def test_backfill_runs_when_watermark_skipped_018(tmp_path: Path) -> None:
    """A database already stamped 18 (clamp / parallel build) still gets the rows."""
    pool = _legacy_v17_db(tmp_path)
    with pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 18")

    run_migrations(pool)

    assert set(_ACL_TABLES).issubset(_table_names(pool))
    assert len(_legacy_acl_rows(pool)) == 4


def test_backfill_covers_ownerless_agents_as_system_owned(tmp_path: Path) -> None:
    """``owner_user_id`` is nullable: shared system agents must not vanish."""
    pool = _legacy_v17_db(tmp_path)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, created_at, updated_at) "
            "VALUES ('ag_orphan_shared', NULL, 'Orphan', 1, 1, 14, 14)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, created_at, updated_at) "
            "VALUES ('ag_orphan_private', NULL, 'Orphan2', 1, 0, 15, 15)"
        )

    run_migrations(pool)

    assert _legacy_acl_rows(pool) == [
        ("agent", "ag_orphan_private", None, "private", None, 1),
        ("agent", "ag_orphan_shared", None, "public", None, 1),
        ("agent", "ag_private", 1, "private", None, 1),
        ("agent", "ag_shared", 1, "public", None, 1),
        ("connector", "cn_shared", 2, "public", None, 1),
        ("knowledge_base", "kb_shared", 2, "public", None, 1),
    ]


def test_backfill_never_overwrites_a_later_acl_edit(tmp_path: Path) -> None:
    pool = _legacy_v17_db(tmp_path)
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO org_units(key, label_zh, label_en, created_at) "
            "VALUES ('sales', '销售', 'Sales', 1)"
        )
        conn.execute(
            "UPDATE resource_acl SET visibility = 'unit', unit_key = 'sales', version = 4 "
            "WHERE resource_type = 'agent' AND resource_id = 'ag_shared'"
        )

    run_migrations(pool)

    with pool.connect() as conn:
        row = conn.execute(
            "SELECT visibility, unit_key, version FROM resource_acl "
            "WHERE resource_type = 'agent' AND resource_id = 'ag_shared'"
        ).fetchone()
    assert tuple(row) == ("unit", "sales", 4)
    assert len(_legacy_acl_rows(pool)) == 4


# ----------------------------------------------------------------------
# Schema v21: the legacy share booleans are gone
# ----------------------------------------------------------------------

_SHARE_COLUMNS = (
    ("agents", "is_shared"),
    ("connectors", "shared"),
    ("knowledge_bases", "shared"),
)


def _legacy_v20_db(tmp_path: Path) -> SqlitePool:
    """A v20-shaped database: legacy flags populated, no ACL rows for them.

    Emptying ``resource_acl`` models the database the v18 DML never reached, so
    the upgrade cannot lean on a backfill that already happened.
    """
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute("DELETE FROM resource_acl")
        # Schema v21 drops these three; v20 still had them.
        conn.execute("ALTER TABLE agents ADD COLUMN is_shared INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE connectors ADD COLUMN shared INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE knowledge_bases ADD COLUMN shared INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'alice', 'h', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (2, 'bob', 'h', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, color, "
            "welcome_message, mcp_servers, created_at, updated_at) "
            "VALUES ('ag_shared', 1, 'Shared', 1, 1, 'red', 'hi', '[]', 10, 10)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, enabled, is_shared, created_at, updated_at) "
            "VALUES ('ag_private', 1, 'Private', 1, 0, 11, 11)"
        )
        conn.execute(
            "INSERT INTO connectors(instance_id, user_id, kind, display_name, mcp_server_name, "
            "shared, config_json, credential_expires_at, created_at, updated_at) "
            "VALUES ('cn_shared', 2, 'custom-mcp', 'Conn', 'mc-1', 1, '{\"a\": 1}', 99, 12, 12)"
        )
        conn.execute(
            "INSERT INTO knowledge_bases(knowledge_base_id, owner_user_id, name, shared, "
            "max_documents, embedding_dim, created_at, updated_at) "
            "VALUES ('kb_shared', 2, 'KB', 1, 42, 384, 13, 13)"
        )
        conn.execute(
            "INSERT INTO knowledge_documents(document_id, kb_id, path, filename, content_type, "
            "byte_size, created_at, updated_at) "
            "VALUES ('doc_1', 'kb_shared', 'a.txt', 'a.txt', 'text/plain', 3, 14, 14)"
        )
        conn.execute(
            "INSERT INTO channels(channel_id, agent_id, user_id, kind, name, config_json, "
            "created_at, updated_at) VALUES ('ch_1', 'ag_shared', 1, 'im', 'IM', '{}', 15, 15)"
        )
        conn.execute("UPDATE _schema_version SET version = 20")
    return pool


def test_v21_drops_the_share_columns_and_no_other_column(tmp_path: Path) -> None:
    """The SQLite drop is a rebuild: a column missing from the list is lost."""
    pool = _legacy_v20_db(tmp_path)
    before = {table: _columns(pool, table) for table, _ in _SHARE_COLUMNS}

    run_migrations(pool)

    assert _version(pool) == 27
    for table, column in _SHARE_COLUMNS:
        assert column in before[table]
        assert _columns(pool, table) == before[table] - {column}
    # Columns appended by ALTER after the original DDL — and the v10
    # ``max_documents`` that a rebuild used to lose — must survive.
    assert {"max_documents", "description", "default_open"} <= _columns(pool, "knowledge_bases")
    assert {"color", "icon_name", "mcp_servers", "knowledge_base_ids"} <= _columns(pool, "agents")
    assert {"credential_expires_at", "config_json"} <= _columns(pool, "connectors")


def test_v21_keeps_the_row_data_it_rebuilt(tmp_path: Path) -> None:
    pool = _legacy_v20_db(tmp_path)
    with pool.connect() as conn:
        agent_pk = conn.execute("SELECT id FROM agents WHERE agent_id = 'ag_shared'").fetchone()[0]

    run_migrations(pool)

    with pool.connect() as conn:
        base = conn.execute(
            "SELECT name, max_documents, embedding_dim FROM knowledge_bases "
            "WHERE knowledge_base_id = 'kb_shared'"
        ).fetchone()
        agent = conn.execute(
            "SELECT color, welcome_message, mcp_servers, id FROM agents WHERE agent_id = 'ag_shared'"
        ).fetchone()
        connector = conn.execute(
            "SELECT config_json, credential_expires_at FROM connectors "
            "WHERE instance_id = 'cn_shared'"
        ).fetchone()
        doc_count = conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0]

    assert tuple(base) == ("KB", 42, 384)
    assert tuple(agent) == ("red", "hi", "[]", agent_pk)
    assert tuple(connector) == ('{"a": 1}', 99)
    assert doc_count == 1


def test_v21_mirrors_the_flags_before_dropping_them(tmp_path: Path) -> None:
    """Published stays published: the last copy of the flag lands in the ACL."""
    pool = _legacy_v20_db(tmp_path)

    run_migrations(pool)

    assert _legacy_acl_rows(pool) == [
        ("agent", "ag_private", 1, "private", None, 1),
        ("agent", "ag_shared", 1, "public", None, 1),
        ("connector", "cn_shared", 2, "public", None, 1),
        ("knowledge_base", "kb_shared", 2, "public", None, 1),
    ]
    assert {row.agent_id for row in AgentRepo(pool).list_shared()} == {"ag_shared"}
    assert ConnectorRepo(pool).public_instance_ids() == {"cn_shared"}


def test_v21_leaves_acl_visibility_alone(tmp_path: Path) -> None:
    """A unit share is not clobbered by the mirror, and stays visible after it."""
    pool = _legacy_v20_db(tmp_path)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO org_units(key, label_zh, label_en, created_at) "
            "VALUES ('sales', '销售', 'Sales', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, org_unit, created_at) "
            "VALUES (3, 'carol', 'h', 'user', 'sales', 1)"
        )
        conn.execute(
            "INSERT INTO resource_acl(resource_type, resource_id, owner_user_id, visibility, "
            "unit_key, version, updated_at) "
            "VALUES ('knowledge_base', 'kb_shared', 2, 'unit', 'sales', 4, 20)"
        )
    before = {row.id for row in KnowledgeRepo(pool).list_visible(3)}

    run_migrations(pool)

    assert before == {"kb_shared"}
    assert {row.id for row in KnowledgeRepo(pool).list_visible(3)} == before
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT visibility, unit_key, version FROM resource_acl "
            "WHERE resource_type = 'knowledge_base' AND resource_id = 'kb_shared'"
        ).fetchone()
    assert tuple(row) == ("unit", "sales", 4)


def test_v21_rebuild_keeps_the_tables_pointing_at_the_rebuilt_ones(tmp_path: Path) -> None:
    """``threads`` / ``knowledge_documents`` must not end up on the legacy copy."""
    pool = _legacy_v20_db(tmp_path)

    run_migrations(pool)

    with pool.connect() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "agents" in {
            row["table"] for row in conn.execute("PRAGMA foreign_key_list(channels)")
        }
        assert "knowledge_bases" in {
            row["table"] for row in conn.execute("PRAGMA foreign_key_list(knowledge_documents)")
        }
        # Enforcement is live: the cascade still reaches the rebuilt parents.
        conn.execute("DELETE FROM knowledge_bases WHERE knowledge_base_id = 'kb_shared'")
        conn.execute("DELETE FROM agents WHERE agent_id = 'ag_shared'")
        doc_count = conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0]
        channel_count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]

    assert (doc_count, channel_count) == (0, 0)
