-- Schema v43: a skill package can disable ZIP download for non-owners.
--
-- Read this together with 043_skill_package_allow_download.pg.sql; the
-- reasoning is identical for both dialects. ``1`` (allow) is the default and
-- the backfill in one word: every package that existed before this column
-- keeps exactly the reach it had — anyone with package visibility could
-- export it — and the creator restricts the package afterwards via PATCH.
--
-- SQLite boots apply this through migrate.py::_ensure_skill_packages_schema,
-- which adds the column only when it is missing: ``ALTER TABLE ... ADD
-- COLUMN`` has no ``IF NOT EXISTS`` here, and the helper is also what repairs
-- a database whose recorded version skipped 043. The statement below is what
-- that helper executes, listed here so the file stays the readable record of
-- the change; this file is not executed on SQLite.
--
-- The column lives on ``skill_packages`` itself — one persisted source, no
-- side table — so the identity rebuild helper copies it verbatim and the
-- repo answers ``allow_download`` from every read path the same way.

ALTER TABLE skill_packages ADD COLUMN allow_download INTEGER NOT NULL DEFAULT 1;

UPDATE _schema_version SET version = 43;
