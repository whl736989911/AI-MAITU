-- Schema v28: the file index a scan writes, and the record of the scan itself.
--
-- Read this together with 028_knowledge_file_index.pg.sql; the reasoning is
-- there and is identical for both dialects. Only the types differ
-- (``BIGINT`` here is SQLite's ``INTEGER``).
--
-- SQLite boots apply this through
-- migrate.py::_ensure_knowledge_file_index_schema and
-- _ensure_knowledge_sync_runs_schema — both idempotent, and both re-run on
-- every boot so a database whose watermark skipped 28 still converges. The
-- statements below are what those helpers execute, listed here so the file
-- stays the readable record of the change; this file is not executed on SQLite.

ALTER TABLE knowledge_documents ADD COLUMN data_source_id TEXT
  REFERENCES data_sources(id) ON DELETE CASCADE;
ALTER TABLE knowledge_documents ADD COLUMN source_path TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_documents ADD COLUMN source_size INTEGER NOT NULL DEFAULT 0;
ALTER TABLE knowledge_documents ADD COLUMN source_modified_at INTEGER;
ALTER TABLE knowledge_documents ADD COLUMN obs_size INTEGER;
ALTER TABLE knowledge_documents ADD COLUMN obs_modified_at INTEGER;
ALTER TABLE knowledge_documents ADD COLUMN obs_at INTEGER;
ALTER TABLE knowledge_documents ADD COLUMN delete_pending_since INTEGER;

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_source
  ON knowledge_documents (data_source_id, source_path);

CREATE TABLE IF NOT EXISTS knowledge_sync_runs (
  id             TEXT PRIMARY KEY,
  data_source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  trigger        TEXT NOT NULL,   -- manual | scheduled
  status         TEXT NOT NULL,   -- running | ok | failed | cancelled
  started_at     INTEGER NOT NULL,
  finished_at    INTEGER,
  scanned        INTEGER NOT NULL DEFAULT 0,
  added          INTEGER NOT NULL DEFAULT 0,
  updated        INTEGER NOT NULL DEFAULT 0,
  removed        INTEGER NOT NULL DEFAULT 0,
  deferred       INTEGER NOT NULL DEFAULT 0,   -- seen, still being copied
  failed         INTEGER NOT NULL DEFAULT 0,
  error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_sync_runs_source
  ON knowledge_sync_runs (data_source_id, started_at);

UPDATE _schema_version SET version = 28;
