-- Schema v44: preserve the existing agent personalization surfaces while each
-- capability receives an independently revocable module permission key.
-- The permission JSON read/modify/write is implemented once in migrate.py so
-- SQLite and PostgreSQL honor explicit denies identically.
--
-- v44 also carries the rollback-source link for deferred ACL rollbacks
-- (``resource_acl_changes.rollback_of``), which v42 shipped without. SQLite
-- boots apply it through migrate.py::_ensure_acl_rollback_link_schema, which
-- adds the column only when it is missing; the statement below is what that
-- helper executes, listed here so the file stays the readable record of the
-- change. This file is not executed on SQLite.
ALTER TABLE resource_acl_changes ADD COLUMN rollback_of TEXT
  REFERENCES resource_acl_changes(id);

UPDATE _schema_version SET version = 44;
