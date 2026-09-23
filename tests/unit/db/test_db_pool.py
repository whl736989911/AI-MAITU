"""tests/unit/test_db_pool.py"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def test_db_pool_creates_file(tmp_path: Path):
    db_path = tmp_path / "octop.db"
    SqlitePool(db_path)
    assert db_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only mode bits")
def test_db_pool_file_is_0600(tmp_path: Path):
    db_path = tmp_path / "octop.db"
    SqlitePool(db_path)
    mode = db_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_run_migrations_creates_tables(db: SqlitePool):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "users",
        "user_policies",
        "agents",
        "providers",
        "channels",
        "cron_jobs",
        "sessions",
        "threads",
        "connectors",
        "connector_oauth_states",
        "secrets",
        "audit_log",
        "_schema_version",
        "storage_backends",
        "usage_log",
        "settings",
        "voice_providers",
        "proactive_care_config",
        "care_push_records",
        "skill_packages",
        "published_experts",
        "knowledge_bases",
        "knowledge_documents",
        "data_sources",
        "feature_user_overlays",
        "feature_workflow_runs",
        "feature_workflow_changes",
        "sso_providers",
        "sso_login_states",
        "trajectory_events",
        "org_units",
        "org_unit_permissions",
        "resource_acl",
        "resource_acl_grants",
        "resource_acl_changes",
    }
    assert expected.issubset(names)
    assert "knowledge_base_members" not in names
    # Schema v26 drops the deleted feature subsystem's tables: a fresh database
    # never has them, and one that did (v16-v25) loses them on the way to 26. The
    # check names them instead of matching the ``feature_`` prefix, because a later
    # table may legitimately carry that prefix — ``feature_user_overlays`` is the
    # calling user's own text above a feature's declared workflow.
    assert {
        "feature_tasks",
        "feature_cases",
        "feature_rules",
        "feature_runs",
        "feature_step_runs",
        "feature_step_edits",
        "feature_step_dispatches",
    }.isdisjoint(names)


def test_run_migrations_idempotent(db: SqlitePool):
    run_migrations(db)
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        cron_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
        thread_cols = {r["name"] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
        agent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
        table_names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        kb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
        doc_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(knowledge_documents)").fetchall()
        }
        connector_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(connectors)").fetchall()
        }
        connector_indexes = {
            r["name"] for r in conn.execute("PRAGMA index_list(connectors)").fetchall()
        }
        sso_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sso_providers)").fetchall()}
        sso_indexes = {
            r["name"] for r in conn.execute("PRAGMA index_list(sso_providers)").fetchall()
        }
    assert v == 36
    assert "login_failed_count" in cols
    assert "login_locked_until" in cols
    assert "preferences_json" in cols
    assert "permissions" in cols
    assert {"org_unit", "denied_permissions"}.issubset(cols)
    assert {"org_units", "org_unit_permissions"}.issubset(table_names)
    assert "workspace_root_dir" not in cols
    assert "token_quota" not in cols
    assert "user_policies" in table_names
    assert {"email", "sso_provider_id", "sso_subject"}.issubset(cols)
    assert {"kind", "extra"}.issubset(sso_cols)
    assert "idx_sso_providers_kind" in sso_indexes
    assert "user_invites" in table_names
    assert {"thread_messages", "thread_history_projection", "trajectory_events"}.issubset(
        table_names
    )
    assert "task_type" in cron_cols
    assert "mcp_servers" in cron_cols
    assert "name" in cron_cols
    # Schema v21 dropped the legacy global share booleans (visibility lives in
    # ``resource_acl``); ``max_documents`` must survive the SQLite rebuild.
    assert "shared" not in connector_cols
    assert "shared" not in kb_cols
    assert "is_shared" not in agent_cols
    assert "max_documents" in kb_cols
    assert "idx_connectors_user_display_name" in connector_indexes
    assert {"model_ref", "reasoning_mode", "reasoning_effort", "artifacts"}.issubset(thread_cols)
    assert {
        "color",
        "icon_name",
        "icon_url",
        "skill_package_ids",
        "published_expert_id",
        "welcome_message",
        "knowledge_base_ids",
        "mcp_servers",
    }.issubset(agent_cols)
    assert "skill_packages" in table_names
    assert "knowledge_base_id" in kb_cols
    assert {"document_id", "path", "is_dir"}.issubset(doc_cols)
    # Schema v26: what an agent row *is* — the marker that separates a feature's
    # own agent from the user's experts, which ownership can no longer express.
    assert "kind" in agent_cols


def test_watermark_at_25_gets_agent_kind_and_loses_the_feature_tables(tmp_path: Path) -> None:
    """A v25 database lands on v26: the marker is added and the old tables go.

    The column arrives on a database that holds the rows it was introduced for —
    an app-owned agent, which before v26 could only ever be a feature's own, and
    an expert — and it is dropped here first, because a row layout without it is
    the only state the backfill runs in. The feature tables the deleted subsystem
    wrote to must not survive the upgrade.
    """
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute("ALTER TABLE agents DROP COLUMN kind")
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'author', 'x', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at) "
            "VALUES ('feat-legacy', NULL, 'legacy feature', 1, 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at) "
            "VALUES ('expert-legacy', 1, 'legacy expert', 1, 1)"
        )
        conn.execute("CREATE TABLE feature_tasks (id TEXT PRIMARY KEY)")
        conn.execute("UPDATE _schema_version SET version = 25")

    run_migrations(pool)

    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        kinds = {
            r["agent_id"]: r["kind"]
            for r in conn.execute("SELECT agent_id, kind FROM agents").fetchall()
        }
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert version == 36
    assert kinds["feat-legacy"] == "feature"
    assert kinds["expert-legacy"] == "agent"
    assert "feature_tasks" not in tables


def test_watermark_at_20_without_data_sources_is_repaired(tmp_path: Path) -> None:
    """A DB stamped 20 by a clamp or a parallel build still gets ``data_sources``.

    The table is written by the data-source endpoints, so a missing one would
    only surface as a failed create at runtime.
    """
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("UPDATE _schema_version SET version = 20")

    run_migrations(pool)

    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(data_sources)").fetchall()}
    assert version == 36
    assert "data_sources" in tables
    assert {"knowledge_base_id", "kind", "config_json", "sync_status"}.issubset(cols)


def test_repair_legacy_schema_ensures_columns(tmp_path: Path) -> None:
    """DBs missing columns must be repaired on boot even if schema version is ahead."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        # Simulate a schema version ahead of what the columns reflect
        conn.execute("UPDATE _schema_version SET version = 2")
    run_migrations(pool)
    with pool.connect() as conn:
        cron_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
    assert "task_type" in cron_cols
    assert "name" in cron_cols


def test_migration_002_idempotent_when_column_already_present(tmp_path: Path) -> None:
    """Partial upgrade: mcp_servers exists but version still 1 must not fail."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("ALTER TABLE cron_jobs ADD COLUMN mcp_servers TEXT NOT NULL DEFAULT '[]'")
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        cron_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
    assert v == 36
    assert "mcp_servers" in cron_cols
    assert "skill_packages" in {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_migration_005_preserves_populated_users_and_constraints(tmp_path: Path) -> None:
    """A populated v4 DB keeps users and their foreign-key references at v5."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    initial_migration = (
        Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations/001_initial.sql"
    )
    with pool.connect() as conn:
        conn.executescript(initial_migration.read_text())
        conn.execute("UPDATE _schema_version SET version = 4")
        conn.execute(
            """
            INSERT INTO users(
              id, username, password_hash, role, display_name, disabled, locale,
              created_at, login_failed_count, login_locked_until, preferences_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (101, "legacy-user", "legacy-hash", "admin", "Legacy User", 1, "en", 10, 2, 20, "{}"),
        )
        conn.execute(
            """
            INSERT INTO agents(agent_id, user_id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-agent", 101, "Legacy Agent", 10, 20),
        )

    run_migrations(pool)

    with pool.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (101,)).fetchone()
        agent = conn.execute(
            "SELECT agent_id, user_id, name FROM agents WHERE agent_id = ?",
            ("legacy-agent",),
        ).fetchone()
        assert user is not None
        assert dict(user) == {
            "id": 101,
            "username": "legacy-user",
            "password_hash": "legacy-hash",
            "role": "admin",
            "display_name": "Legacy User",
            "disabled": 1,
            "locale": "en",
            "created_at": 10,
            "login_failed_count": 2,
            "login_locked_until": 20,
            "preferences_json": "{}",
            "email": None,
            "sso_provider_id": None,
            "sso_subject": None,
            "permissions": "[]",
            "org_unit": None,
            "denied_permissions": None,
        }
        assert dict(agent) == {
            "agent_id": "legacy-agent",
            "user_id": 101,
            "name": "Legacy Agent",
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        user_indexes = {
            row["name"]: row["partial"] for row in conn.execute("PRAGMA index_list(users)")
        }
        assert user_indexes["idx_users_email"] == 1
        assert user_indexes["idx_users_sso"] == 1
        user_foreign_keys = conn.execute("PRAGMA foreign_key_list(users)").fetchall()
        assert any(
            row["from"] == "sso_provider_id" and row["table"] == "sso_providers"
            for row in user_foreign_keys
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO agents(agent_id, user_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("invalid-agent", 999, "Invalid Agent", 10, 20),
            )

        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at, email) VALUES (?, ?, ?, ?, ?)",
            ("passwordless", None, "user", 30, "person@example.com"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users(username, password_hash, role, created_at, email) VALUES (?, ?, ?, ?, ?)",
                ("duplicate-email", None, "user", 30, "person@example.com"),
            )

        conn.execute(
            """
            INSERT INTO sso_providers(
              id, enabled, display_name, issuer, client_id, scopes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, "SSO", "https://issuer.example", "octop-client", "openid", 30, 30),
        )
        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, created_at, sso_provider_id, sso_subject)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("sso-user", None, "user", 30, 1, "subject-1"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO users(username, password_hash, role, created_at, sso_provider_id, sso_subject)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("duplicate-sso", None, "user", 30, 1, "subject-1"),
            )


def test_legacy_users_rebuild_preserves_org_unit_columns(tmp_path: Path) -> None:
    """A pre-005 DB rebuilds ``users``; the v17 scope columns must be carried over."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    migrations = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
    with pool.connect() as conn:
        conn.executescript((migrations / "001_initial.sql").read_text(encoding="utf-8"))
        conn.execute("UPDATE _schema_version SET version = 4")
        # Simulate a DB that already carries the v17 columns with data; the
        # legacy SSO rebuild recreates ``users`` from an explicit column list.
        conn.execute("ALTER TABLE users ADD COLUMN org_unit TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN denied_permissions TEXT")
        conn.execute(
            """
            INSERT INTO users(
              id, username, password_hash, role, display_name, disabled, locale,
              created_at, login_failed_count, login_locked_until, preferences_json,
              org_unit, denied_permissions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                "unit-user",
                "hash",
                "user",
                "Unit User",
                0,
                "zh",
                10,
                0,
                0,
                "{}",
                "ops",
                '["browser"]',
            ),
        )

    run_migrations(pool)

    with pool.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (7,)).fetchone()
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert {"org_unit", "denied_permissions"}.issubset(columns)
    assert row is not None
    assert row["org_unit"] == "ops"
    assert row["denied_permissions"] == '["browser"]'


def test_stuck_version_6_without_permissions_column_is_repaired(tmp_path: Path) -> None:
    """Pre-squash version clamp can leave schema at 6 without users.permissions."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("UPDATE _schema_version SET version = 6")
    run_migrations(pool)
    with pool.connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert version == 36
    assert "permissions" in cols


def test_schema_v10_without_projection_tables_is_repaired(tmp_path: Path) -> None:
    """DBs that bumped to v10 via a colliding 010 file still get projection tables."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("UPDATE _schema_version SET version = 10")
    run_migrations(pool)
    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        table_names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        kb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
        cron_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
    assert version == 36
    assert {"thread_messages", "thread_history_projection", "trajectory_events"}.issubset(
        table_names
    )
    assert "max_documents" in kb_cols
    assert "name" in cron_cols


def test_ahead_of_max_schema_version_clamps_to_max(tmp_path: Path) -> None:
    """A DB whose watermark is ahead of discovered migrations clamps on boot.

    When the recorded version equals an older max (here 8) and a newer migration
    (9) exists, ``run_migrations`` applies the gap instead of clamping.
    """
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("UPDATE _schema_version SET version = 8")
    run_migrations(pool)
    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        pkg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_packages)").fetchall()}
        pub_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(published_experts)").fetchall()
        }
        invite_tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert version == 36
    assert "skill_package_id" in pkg_cols
    assert "published_expert_id" in pub_cols
    assert "user_invites" in invite_tables


def test_current_watermark_repairs_pre_v13_connectors_schema(tmp_path: Path) -> None:
    """A folded v13 schema change is repaired even when migration 013 is skipped."""
    pool = SqlitePool(tmp_path / "octop.db")
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        conn.execute("UPDATE _schema_version SET version = 13")
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES ('owner', 'hash', 'admin', 1)"
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()[0]
        conn.execute(
            "INSERT INTO connectors("
            "instance_id, user_id, kind, display_name, mcp_server_name, created_at, updated_at"
            ") VALUES ('instance-1', ?, 'github', 'GitHub', 'connector_github_1', 1, 1)",
            (user_id,),
        )

    run_migrations(pool)

    with pool.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(connectors)").fetchall()}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(connectors)").fetchall()}
        user_id = conn.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()[0]
        conn.execute(
            "INSERT INTO connectors("
            "instance_id, user_id, kind, display_name, mcp_server_name, created_at, updated_at"
            ") VALUES ('instance-2', ?, 'github', 'GitHub Work', 'connector_github_2', 1, 1)",
            (user_id,),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM connectors WHERE user_id = ? AND kind = 'github'",
            (user_id,),
        ).fetchone()[0]

    # The v13 rebuild no longer creates the retired share flag, and the unique
    # display-name index is what makes two same-named connectors legal.
    assert {"instance_id", "mcp_server_name"}.issubset(columns)
    assert "shared" not in columns
    assert "idx_connectors_user_display_name" in indexes
    assert count == 2
    pool.close()


def test_foreign_keys_enabled(db: SqlitePool):
    with db.connect() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_pre_squash_schema_version_clamped_and_knowledge_tables_filled(
    tmp_path: Path,
) -> None:
    """Develop DBs that applied split 005–009 must clamp to consolidated v5."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    with pool.connect() as conn:
        conn.executescript(
            (
                Path(__file__).resolve().parents[3]
                / "src/octop/infra/db/migrations/001_initial.sql"
            ).read_text()
        )
        # Simulate a develop DB whose recorded version is ahead of the
        # consolidated max, so reconcile clamps and repairs knowledge tables.
        conn.execute("UPDATE _schema_version SET version = 13")
        conn.execute("ALTER TABLE agents ADD COLUMN is_shared INTEGER NOT NULL DEFAULT 0")

    run_migrations(pool)

    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert version == 36
    assert "permissions" in user_cols
    assert {
        "published_experts",
        "sso_providers",
        "sso_login_states",
        "knowledge_bases",
        "knowledge_documents",
    }.issubset(tables)
    kb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
    pkg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_packages)").fetchall()}
    pub_cols = {r["name"] for r in conn.execute("PRAGMA table_info(published_experts)").fetchall()}
    assert "knowledge_base_id" in kb_cols
    assert "skill_package_id" in pkg_cols
    assert "published_expert_id" in pub_cols


def test_transaction_rolls_back_on_exception(db: SqlitePool):
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 0)",
            ("a", "h", "user"),
        )
        raise RuntimeError("boom")
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert n == 0


def test_v7_sql_files_declare_identity_rebuild() -> None:
    """The numbered v7 pair must contain the table rebuild, not only ADD COLUMN."""
    root = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
    sqlite_sql = (root / "007_resource_identity_and_profile.sql").read_text(encoding="utf-8")
    pg_sql = (root / "007_resource_identity_and_profile.pg.sql").read_text(encoding="utf-8")
    for sql in (sqlite_sql, pg_sql):
        for token in (
            "knowledge_base_id",
            "document_id",
            "skill_package_id",
            "published_expert_id",
            "ALTER TABLE knowledge_bases RENAME",
            "ALTER TABLE skill_packages RENAME",
            "ALTER TABLE published_experts RENAME",
            "DROP TABLE IF EXISTS knowledge_base_members",
            "ADD COLUMN",
            "welcome_message",
            "artifacts",
        ):
            assert token in sql


def test_v7_sqlite_sql_upgrades_legacy_text_pks(tmp_path: Path) -> None:
    """A clean v6-shaped DB can execute 007.sql without going through Python helpers."""
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    migrations = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
    with pool.connect() as conn:
        conn.executescript((migrations / "001_initial.sql").read_text(encoding="utf-8"))
        conn.executescript(
            """
            CREATE TABLE skill_packages (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              skill_count INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              icon_url TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE published_experts (
              id TEXT PRIMARY KEY,
              slug TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              source_agent_id TEXT,
              icon_name TEXT NOT NULL DEFAULT '',
              color TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE knowledge_bases (
              id TEXT PRIMARY KEY,
              owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              default_open INTEGER NOT NULL DEFAULT 0,
              shared INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              embedding_model TEXT NOT NULL DEFAULT '',
              embedding_dim INTEGER NOT NULL DEFAULT 0,
              doc_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE knowledge_base_members (
              kb_id TEXT NOT NULL,
              user_id INTEGER NOT NULL,
              role TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY (kb_id, user_id)
            );
            CREATE TABLE knowledge_documents (
              id TEXT PRIMARY KEY,
              kb_id TEXT NOT NULL,
              filename TEXT NOT NULL,
              content_type TEXT NOT NULL,
              byte_size INTEGER NOT NULL,
              content_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              error_message TEXT NOT NULL DEFAULT '',
              chunk_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 1)",
            ("owner", "h", "user"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()[0]
        conn.execute(
            "INSERT INTO skill_packages(id, name, created_by, created_at, updated_at) "
            "VALUES ('pkg1', 'Pack', 'owner', '1', '1')"
        )
        conn.execute(
            "INSERT INTO published_experts(id, slug, name, created_by, created_at, updated_at) "
            "VALUES ('pub1', 'slug', 'Expert', 'owner', 1, 1)"
        )
        conn.execute(
            "INSERT INTO knowledge_bases("
            "id, owner_user_id, name, embedding_model, embedding_dim, "
            "doc_count, created_at, updated_at) VALUES (?, ?, 'Docs', '', 0, 1, 1, 1)",
            ("kb1", user_id),
        )
        conn.execute(
            "INSERT INTO knowledge_base_members(kb_id, user_id, role, created_at) "
            "VALUES ('kb1', ?, 'owner', 1)",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO knowledge_documents("
            "id, kb_id, filename, content_type, byte_size, created_at, updated_at) "
            "VALUES ('doc1', 'kb1', 'a.md', 'text/markdown', 1, 1, 1)"
        )
        conn.executescript((migrations / "007_resource_identity_and_profile.sql").read_text())

    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        pkg = conn.execute("SELECT * FROM skill_packages").fetchone()
        pub = conn.execute("SELECT * FROM published_experts").fetchone()
        kb = conn.execute("SELECT * FROM knowledge_bases").fetchone()
        doc = conn.execute("SELECT * FROM knowledge_documents").fetchone()
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        agent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
        thread_cols = {r["name"] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
    assert version == 7
    assert "knowledge_base_members" not in tables
    assert "welcome_message" in agent_cols
    assert "artifacts" in thread_cols
    assert pkg["skill_package_id"] == "pkg1"
    assert pub["published_expert_id"] == "pub1"
    assert kb["knowledge_base_id"] == "kb1"
    assert doc["document_id"] == "doc1"
    assert doc["path"] == "a.md"
    assert doc["filename"] == "a.md"


def test_v14_to_v15_adds_sso_provider_kind_without_rebuilding(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_sso_providers_kind")
        conn.execute("ALTER TABLE sso_providers DROP COLUMN extra")
        conn.execute("ALTER TABLE sso_providers DROP COLUMN kind")
        conn.execute("UPDATE _schema_version SET version = 14")
        conn.execute(
            """
            INSERT INTO sso_providers(
              enabled, display_name, issuer, client_id, scopes, created_at, updated_at
            ) VALUES (1, 'Company', 'https://issuer.example', 'client', 'openid', 1, 1)
            """
        )
        provider_id = conn.execute("SELECT id FROM sso_providers").fetchone()[0]
        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, created_at, sso_provider_id, sso_subject)
            VALUES ('sso-admin', 'x', 'admin', 1, ?, 'sub-1')
            """,
            (provider_id,),
        )
    run_migrations(pool)
    run_migrations(pool)
    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        row = conn.execute("SELECT id, kind, extra FROM sso_providers").fetchone()
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list(sso_providers)").fetchall()}
        bound = conn.execute(
            "SELECT sso_provider_id FROM users WHERE username = 'sso-admin'"
        ).fetchone()[0]
    assert version == 36
    assert int(row["id"]) == int(provider_id)
    assert row["kind"] == "oidc"
    assert row["extra"] == "{}"
    assert "idx_sso_providers_kind" in indexes
    assert int(bound) == int(provider_id)
