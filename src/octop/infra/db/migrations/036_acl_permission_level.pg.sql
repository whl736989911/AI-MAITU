-- Schema v36: an ACL entry says what sharing it lets people *do* — read or write.
--
-- Until now an entry answered one question: *who may reach this resource*
-- (``visibility`` + ``resource_acl_grants``). "Share it" and "share it
-- read-only" are two different decisions, and the second one had nowhere to
-- live, so every share was effectively read-only and only the owner or an
-- administrator could maintain anything. The ``permission`` column is that
-- second decision, on the same row:
--
--   * ``read``  — reaching the resource: list it, open it, search it.
--   * ``write`` — reaching it *and* maintaining it: add, change, remove.
--
-- The rules stay one implementation (``infra/sharing``): ``can_access`` is
-- unchanged by this column, and ``can_write`` is it plus one question — is the
-- owner writing, an administrator, or does the entry say ``write``? So the
-- default below is load-bearing rather than cosmetic: every row that exists
-- when this runs keeps exactly the reach it had and gains no write, which is
-- what makes the ``public`` row a knowledge space is seeded with (v27) mean
-- "everyone may read this", never "everyone may write this".
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite boots apply the same statement through
-- migrate.py::_ensure_acl_permission_level.
--
-- No ``CHECK`` constraint on ``permission``: it mirrors the column next to it
-- (``visibility TEXT NOT NULL``), and the value is validated where it enters —
-- the API's ``Permission`` literal and ``ResourceAclRepo._validate_permission``.
-- A level the rules cannot read is answered by ``can_write`` as "no write".

ALTER TABLE resource_acl ADD COLUMN IF NOT EXISTS permission TEXT NOT NULL DEFAULT 'read';

UPDATE _schema_version SET version = 36;
