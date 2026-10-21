-- Schema v44: preserve the existing agent personalization surfaces while each
-- capability receives an independently revocable module permission key.
-- migrate.py performs the dialect-independent permission JSON read/modify/write.
--
-- v44 also carries the rollback-source link for deferred ACL rollbacks:
-- v42 shipped without it, so every database upgrading past 42 receives the
-- column here. ``IF NOT EXISTS`` keeps it safe against
-- migrate.py::_ensure_acl_rollback_link_schema having added the column first.
ALTER TABLE resource_acl_changes
  ADD COLUMN IF NOT EXISTS rollback_of TEXT REFERENCES resource_acl_changes(id);

UPDATE _schema_version SET version = 44;
