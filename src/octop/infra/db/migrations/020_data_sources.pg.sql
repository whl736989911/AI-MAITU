-- Schema v20: data sources — a named ingest source attached to a knowledge base.
--
-- A data source inherits its knowledge base's visibility wholesale and has no
-- ``resource_acl`` row of its own: one rule, one place to change it. The FK
-- therefore targets ``knowledge_bases`` (the public ``knowledge_base_id``,
-- exactly like ``knowledge_documents.kb_id``) so deleting a base takes its
-- sources with it.
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite runs migrate.py::_ensure_data_sources_schema with the same DDL.

CREATE TABLE IF NOT EXISTS data_sources (
  id                TEXT PRIMARY KEY,
  knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  kind              TEXT NOT NULL,      -- 'upload' | 'url' | 'connector'
  config_json       TEXT NOT NULL DEFAULT '{}',
  created_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
  sync_status       TEXT NOT NULL DEFAULT 'idle',   -- idle|running|ok|failed
  sync_error        TEXT,
  last_synced_at    BIGINT,
  created_at        BIGINT NOT NULL,
  updated_at        BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_sources_kb
  ON data_sources (knowledge_base_id, name);

UPDATE _schema_version SET version = 20;
