-- Schema v28: the file index a scan writes, and the record of the scan itself.
--
-- Design §3.3 (``KnowledgeFile``) and §8 (sync and processing tasks).
--
-- ``knowledge_documents`` already holds a file's path, size, content hash and
-- status, so an external source's files live in the same tree the uploaded
-- documents do rather than in a parallel one. What it learns here is where the
-- file came from (``data_source_id`` is NULL for everything the platform holds
-- itself) and the state a scan needs between two runs:
--
--   * ``source_size`` / ``source_modified_at`` — what the file looked like when
--     it was last *processed*, which is what change detection compares against.
--   * ``obs_size`` / ``obs_modified_at`` / ``obs_at`` — what the previous scan
--     *observed*, and when. A file still being copied changes between scans; it
--     is only processed once its size and modification time have held still for
--     the debounce window, which is design §8.3's rule read as "has this file
--     stopped moving" rather than "has a second scan happened" — so a manual
--     sync of a settled folder processes it on the spot.
--   * ``delete_pending_since`` — a file that vanished is not removed at once.
--     A share that blinked must not read as "the folder was emptied".
--
-- ``data_source_id`` cascades: a source's index is meaningless without the
-- source, and a row left pointing at a deleted one would be a file nobody can
-- read or rescan. The service deletes a source's files explicitly first, so
-- ``knowledge_bases.doc_count`` stays honest; the cascade is the guarantee that
-- nothing is orphaned if some other path deletes a source.
--
-- ``knowledge_sync_runs`` is the task record design §8.4 asks for: one row per
-- scan, with the counts and the reason it stopped. The per-file outcome stays
-- on the document row, so a run is a summary and never the only trace.
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite runs migrate.py::_ensure_knowledge_file_index_schema and
-- _ensure_knowledge_sync_runs_schema, whose DDL this file mirrors.

ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS data_source_id TEXT
  REFERENCES data_sources(id) ON DELETE CASCADE;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS source_path TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS source_size BIGINT NOT NULL DEFAULT 0;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS source_modified_at BIGINT;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS obs_size BIGINT;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS obs_modified_at BIGINT;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS obs_at BIGINT;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS delete_pending_since BIGINT;

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_source
  ON knowledge_documents (data_source_id, source_path);

CREATE TABLE IF NOT EXISTS knowledge_sync_runs (
  id             TEXT PRIMARY KEY,
  data_source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  trigger        TEXT NOT NULL,   -- manual | scheduled
  status         TEXT NOT NULL,   -- running | ok | failed | cancelled
  started_at     BIGINT NOT NULL,
  finished_at    BIGINT,
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
