-- Schema v27: the enterprise knowledge space and the external-folder source model.
--
-- Two changes, both of them the design's first phase
-- (``docs/enterprise-knowledge-and-storage-design.md`` §3.1, §3.2, §4):
--
-- 1. ``knowledge_bases`` becomes the deployment's *single* enterprise knowledge
--    space (design §1: one logical knowledge base per enterprise, and a user
--    cannot create a second one). It therefore has no user owner, so
--    ``owner_user_id`` loses ``NOT NULL`` — a NULL owner is "system-owned", the
--    same meaning ``resource_acl.owner_user_id`` already carries, and
--    ``sharing.can_access`` rule 2 can never match it. Admin rights over the
--    space come from rule 1, and everyone else's from the ACL row the bootstrap
--    writes.
--    Rows that predate this migration keep their owner and stay exactly as
--    reachable as they were: their ACL entries are untouched, and the design's
--    "convert the old per-user bases into the enterprise space" step waits for
--    file/directory ACLs (§12 phase 8) so no document's audience widens.
--
-- 2. ``data_sources`` learns to point at a folder instead of at an uploaded
--    copy: where the share is, who connects to it, whether it is read-only, and
--    what the last scan saw. Credentials never land in ``config_json``: the
--    secret goes to ``credentials_enc`` (Fernet, key in ``secrets``) and the API
--    only ever reports that one exists.
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite runs migrate.py::_ensure_enterprise_knowledge_space and
-- _ensure_data_sources_connection_schema, whose DDL this file mirrors.
-- The singleton row itself is seeded in Python on both dialects, because it
-- needs a generated id and an ACL row.

ALTER TABLE knowledge_bases ALTER COLUMN owner_user_id DROP NOT NULL;

ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS is_enterprise INTEGER NOT NULL DEFAULT 0;

-- At most one enterprise space, enforced by the database rather than by a
-- check every writer has to remember.
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_bases_enterprise
  ON knowledge_bases(is_enterprise) WHERE is_enterprise = 1;

ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS server TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS share TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS root_path TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS username TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS credentials_enc BYTEA;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS read_only INTEGER NOT NULL DEFAULT 1;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS include_globs TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS exclude_globs TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS scan_interval_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS connection_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS connection_error TEXT;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS last_scan_at BIGINT;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS last_scan_ok_at BIGINT;

UPDATE _schema_version SET version = 27;
