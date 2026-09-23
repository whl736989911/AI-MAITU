-- Schema v36: an ACL entry says what sharing it lets people *do* — read or write.
--
-- Read this together with 036_acl_permission_level.pg.sql; the reasoning is
-- there and is identical for both dialects.
--
-- SQLite boots apply this through migrate.py::_ensure_acl_permission_level,
-- which adds the column only when it is missing: ``ALTER TABLE ... ADD COLUMN``
-- has no ``IF NOT EXISTS`` here, and the helper is also what repairs a database
-- whose recorded version skipped 036. The statement below is what that helper
-- executes, listed here so the file stays the readable record of the change;
-- this file is not executed on SQLite.
--
-- ``read`` is the default and the backfill in one word: every row that exists
-- when this runs keeps exactly the reach it had, and gains no write. That is
-- what makes the ``public`` row a knowledge space is seeded with (v27) mean
-- "everyone may read this", never "everyone may write this".
--
-- No ``CHECK`` constraint on ``permission``: it mirrors the column next to it
-- (``visibility TEXT NOT NULL``), and the value is validated where it enters —
-- the API's ``Permission`` literal and ``ResourceAclRepo._validate_permission``.
-- A level the rules cannot read is answered by ``can_write`` as "no write".

ALTER TABLE resource_acl ADD COLUMN permission TEXT NOT NULL DEFAULT 'read';

UPDATE _schema_version SET version = 36;
