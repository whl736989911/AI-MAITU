"""Migration 046: feature data record responsibility (effective writer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db import migrate as migrate_module
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool

_RESPONSIBILITY_COLUMNS = {"responsible_user_id"}


def _columns(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _indexes(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _version(pool: SqlitePool) -> int:
    with pool.connect() as conn:
        row = conn.execute("SELECT version FROM _schema_version").fetchone()
    return int(row[0])


def test_fresh_migrations_create_responsibility_schema(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)

    assert _columns(pool, "feature_data_records") >= _RESPONSIBILITY_COLUMNS
    assert "idx_feature_data_records_responsible" in _indexes(pool, "feature_data_records")
    assert _version(pool) >= 46


def test_upgrade_from_applied_045_adds_the_column(tmp_path: Path) -> None:
    """A local deployment that already applied 045 must still receive v46."""
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 45],
        )
        run_migrations(pool)
    assert _version(pool) == 45
    assert "responsible_user_id" not in _columns(pool, "feature_data_records")

    run_migrations(pool)

    assert _version(pool) >= 46
    assert "responsible_user_id" in _columns(pool, "feature_data_records")
    assert "idx_feature_data_records_responsible" in _indexes(pool, "feature_data_records")


def test_stamped_46_without_ddl_still_converges(tmp_path: Path) -> None:
    """A watermark clamped to 46 by an intermediate build gets the DDL anyway."""
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 45],
        )
        run_migrations(pool)
    with pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 46")

    run_migrations(pool)

    assert "responsible_user_id" in _columns(pool, "feature_data_records")


def test_ensure_helper_is_idempotent(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_migrations(pool)
    migrate_module._ensure_feature_data_responsibility_schema(pool)

    assert _columns(pool, "feature_data_records") >= _RESPONSIBILITY_COLUMNS
    assert _version(pool) >= 46


def test_upgrade_preserves_existing_rows(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    discovered = migrate_module._discover
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= 45],
        )
        run_migrations(pool)
        with pool.connect() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at) "
                "VALUES (7, 'keeper', 'h', 'user', 0)"
            )
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, kind, created_at, updated_at) "
                "VALUES ('feat-keep', 7, 'keep', 'feature', 0, 0)"
            )
            conn.execute(
                "INSERT INTO feature_data_records("
                "id, agent_id, object_key, title, values_json, visibility, revision, "
                "created_by, created_at, updated_at) "
                "VALUES ('rec-keep', 'feat-keep', 'ledger', 't', '{}', 'feature', 1, 7, 0, 0)"
            )

    run_migrations(pool)

    with pool.connect() as conn:
        row = conn.execute(
            "SELECT created_by, responsible_user_id FROM feature_data_records WHERE id = 'rec-keep'"
        ).fetchone()
    assert row is not None
    # Legacy rows fall back to their creator: the column stays NULL by design.
    assert int(row["created_by"]) == 7
    assert row["responsible_user_id"] is None


def test_postgresql_pair_declares_the_same_shapes() -> None:
    base = Path(migrate_module.__file__).parent / "migrations"
    sqlite_sql = (base / "046_feature_data_responsibility.sql").read_text(encoding="utf-8")
    pg_sql = (base / "046_feature_data_responsibility.pg.sql").read_text(encoding="utf-8")

    assert "responsible_user_id" in sqlite_sql
    assert "responsible_user_id" in pg_sql
    assert "idx_feature_data_records_responsible" in sqlite_sql
    assert "idx_feature_data_records_responsible" in pg_sql
    assert "UPDATE _schema_version SET version = 46" in sqlite_sql
    assert "UPDATE _schema_version SET version = 46" in pg_sql
    # Only the PostgreSQL file may rely on ADD COLUMN IF NOT EXISTS; the SQLite
    # numbered file runs exactly once, guarded by the watermark.
    assert "IF NOT EXISTS" not in sqlite_sql.split("ALTER TABLE")[1].split(";")[0]
