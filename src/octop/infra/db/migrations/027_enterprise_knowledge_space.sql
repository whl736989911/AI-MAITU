-- Schema v27: the enterprise knowledge space and the external-folder source model.
--
-- Read this together with 027_enterprise_knowledge_space.pg.sql; the reasoning
-- is there and is identical for both dialects. What differs is only *how*
-- ``owner_user_id`` loses ``NOT NULL``: PostgreSQL drops the constraint in
-- place, SQLite cannot, so the table is rebuilt.
--
-- SQLite boots apply this through
-- migrate.py::_ensure_enterprise_knowledge_space and
-- _ensure_data_sources_connection_schema — both idempotent, and both re-run on
-- every boot so a database whose watermark skipped 27 still converges. The
-- statements below are what those helpers execute, listed here so the file
-- stays the readable record of the change; this file is not executed on SQLite.
--
-- The two pragmas around the rebuild are load-bearing, exactly as in
-- 021_drop_legacy_share_columns.sql: ``foreign_keys = OFF`` keeps the DROP from
-- cascading into ``knowledge_documents.kb_id`` / ``data_sources.knowledge_base_id``,
-- and ``legacy_alter_table = ON`` stops SQLite from rewriting those tables'
-- ``REFERENCES`` clauses to point at the table being dropped.

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

ALTER TABLE knowledge_bases RENAME TO knowledge_bases_legacy;
CREATE TABLE knowledge_bases (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  knowledge_base_id TEXT NOT NULL UNIQUE,
  owner_user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL: system-owned
  name              TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  default_open      INTEGER NOT NULL DEFAULT 0,
  icon_name         TEXT NOT NULL DEFAULT '',
  embedding_model   TEXT NOT NULL DEFAULT '',
  embedding_dim     INTEGER NOT NULL DEFAULT 0,
  doc_count         INTEGER NOT NULL DEFAULT 0,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  max_documents     INTEGER NOT NULL DEFAULT 100,
  is_enterprise     INTEGER NOT NULL DEFAULT 0
);
INSERT INTO knowledge_bases(
  id, knowledge_base_id, owner_user_id, name, description, default_open,
  icon_name, embedding_model, embedding_dim, doc_count, created_at,
  updated_at, max_documents
)
SELECT
  id, knowledge_base_id, owner_user_id, name, description, default_open,
  icon_name, embedding_model, embedding_dim, doc_count, created_at,
  updated_at, max_documents
FROM knowledge_bases_legacy;
DROP TABLE knowledge_bases_legacy;
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner
  ON knowledge_bases(owner_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_bases_enterprise
  ON knowledge_bases(is_enterprise) WHERE is_enterprise = 1;

ALTER TABLE data_sources ADD COLUMN server TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN share TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN root_path TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN username TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN credentials_enc BLOB;
ALTER TABLE data_sources ADD COLUMN read_only INTEGER NOT NULL DEFAULT 1;
ALTER TABLE data_sources ADD COLUMN include_globs TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN exclude_globs TEXT NOT NULL DEFAULT '';
ALTER TABLE data_sources ADD COLUMN scan_interval_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE data_sources ADD COLUMN connection_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE data_sources ADD COLUMN connection_error TEXT;
ALTER TABLE data_sources ADD COLUMN last_scan_at INTEGER;
ALTER TABLE data_sources ADD COLUMN last_scan_ok_at INTEGER;

PRAGMA foreign_keys = ON;

UPDATE _schema_version SET version = 27;
