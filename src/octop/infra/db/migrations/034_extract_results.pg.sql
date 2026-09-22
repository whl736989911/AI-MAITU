-- Schema v34: what a template actually produced for a document (design §7.3).
--
-- Read this together with 034_extract_results.sql; the reasoning is there and is
-- identical for both dialects.
--
-- Idempotent throughout: a boot that converges after a skipped watermark re-runs
-- these statements, so every one is ``IF NOT EXISTS``.

CREATE TABLE IF NOT EXISTS knowledge_extract_results (
  id               TEXT PRIMARY KEY,
  document_id      TEXT NOT NULL
                   REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  template_id      TEXT NOT NULL
                   REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
  template_version INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending',
  fields_json      TEXT NOT NULL DEFAULT '{}',
  error            TEXT,
  model            TEXT NOT NULL DEFAULT '',
  parser_version   TEXT NOT NULL DEFAULT '',
  content_hash     TEXT NOT NULL DEFAULT '',
  created_at       BIGINT NOT NULL,
  updated_at       BIGINT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_results_document_template
  ON knowledge_extract_results (document_id, template_id);
CREATE INDEX IF NOT EXISTS idx_extract_results_template
  ON knowledge_extract_results (template_id, status);

UPDATE _schema_version SET version = 34;
