"""Gated real-PostgreSQL verification for backup rollback.

Set ``OCTOP_TEST_DATABASE_URL`` to a **dedicated** database: these tests reset
the ``public`` schema. They also need ``pg_dump`` and ``pg_restore`` on PATH.
Example::

    export OCTOP_TEST_DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:15432/octop_test'
"""

from __future__ import annotations

import os
import shutil

import pytest

from tests.support.postgresql import requires_postgresql


def _conninfo() -> str:
    return os.environ["OCTOP_TEST_DATABASE_URL"]


def _reset_public_schema(pool: object) -> None:
    with pool.connect() as conn:  # type: ignore[attr-defined]
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
        conn.execute("COMMIT")


def _require_client_tools() -> None:
    missing = [name for name in ("pg_dump", "pg_restore") if not shutil.which(name)]
    if missing:
        pytest.skip(f"missing PostgreSQL client tools: {', '.join(missing)}")


@requires_postgresql
@pytest.mark.postgresql
def test_rollback_cleanup_removes_archive_introduced_objects() -> None:
    """Objects added after the pre-restore inventory are dropped, others stay."""
    from octop.infra.backup.pg_dump import (
        capture_postgres_inventory,
        drop_postgres_objects_added_since,
    )
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool

    pool = PostgresPool(_conninfo())
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("CREATE TABLE live_probe (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO live_probe(id, note) VALUES (1, 'keep')")
            conn.execute(
                "CREATE FUNCTION live_probe_fn(value integer) RETURNS integer"
                " LANGUAGE sql AS 'SELECT $1'"
            )
        before = capture_postgres_inventory(pool)

        with pool.transaction() as conn:
            conn.execute("CREATE TABLE archive_only (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("CREATE INDEX idx_archive_only ON archive_only(note)")
            conn.execute("CREATE INDEX idx_live_probe_archive ON live_probe(note)")
            conn.execute("CREATE VIEW archive_only_view AS SELECT note FROM archive_only")
            conn.execute("CREATE TYPE archive_only_state AS ENUM ('a', 'b')")
            conn.execute(
                "CREATE FUNCTION archive_only_fn() RETURNS integer LANGUAGE sql AS 'SELECT 1'"
            )
            conn.execute("CREATE SCHEMA archive_only_schema")
            conn.execute("CREATE TABLE archive_only_schema.extra (id INTEGER)")

        drop_postgres_objects_added_since(pool, before)

        with pool.connect() as conn:
            for name in (
                "archive_only",
                "archive_only_view",
                "idx_live_probe_archive",
            ):
                row = conn.execute("SELECT to_regclass(?)", (f"public.{name}",)).fetchone()
                assert row is not None and row[0] is None
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM pg_namespace WHERE nspname = 'archive_only_schema'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM pg_type t"
                    " JOIN pg_namespace n ON n.oid = t.typnamespace"
                    " WHERE n.nspname = 'public' AND t.typname = 'archive_only_state'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM pg_proc p"
                    " JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'public' AND p.proname = 'archive_only_fn'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM pg_proc p"
                    " JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'public' AND p.proname = 'live_probe_fn'"
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT note FROM live_probe").fetchone()[0] == "keep"
    finally:
        pool.close()


@requires_postgresql
@pytest.mark.postgresql
def test_postgres_rollback_removes_archive_objects_and_restores_live_dump(
    tmp_path,
) -> None:
    """The full PG rollback drops new objects and replays the pre-restore dump."""
    _require_client_tools()

    from octop.infra.backup.pg_dump import capture_postgres_inventory, dump_postgres
    from octop.infra.backup.system_archive import _rollback_postgres_database
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.settings import SettingsRepo

    conninfo = _conninfo()
    pool = PostgresPool(conninfo)
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        settings = SettingsRepo(pool)
        settings.set("manifest_probe", "live")

        live_dump = tmp_path / "live.dump"
        dump_postgres(conninfo, live_dump)
        before = capture_postgres_inventory(pool)

        # Emulate the post-failure archive state: changed data plus a new table.
        settings.set("manifest_probe", "archive")
        with pool.transaction() as conn:
            conn.execute("CREATE TABLE archive_only (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO archive_only(id) VALUES (1)")

        _rollback_postgres_database(pool, conninfo, live_dump, before)

        assert settings.get("manifest_probe") == "live"
        with pool.connect() as conn:
            assert conn.execute("SELECT to_regclass('public.archive_only')").fetchone()[0] is None
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        pool.close()
