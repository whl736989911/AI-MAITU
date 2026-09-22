-- Schema v34: what a template actually produced for a document (design §7.3).
--
-- One row per (document, template), replaced on a re-run. The design lists what
-- every result has to record — template id, template version, model, parser
-- version, file hash, time, status — and each is here for one reason:
--
--   * ``template_version`` is what makes an edit safe (§7.3). Editing a template
--     writes a new version and touches nothing else, so a result keeps pointing
--     at the version that produced it, and "stale" is simply a result whose
--     version is no longer the template's current one.
--   * ``content_hash`` is what makes the *file* side of the same question
--     answerable: a document whose bytes changed since its result was produced
--     no longer describes that file.
--   * ``model`` is provenance for the numbers themselves: two models asked the
--     same question do not answer it identically.
--
-- The result is stored as JSON keyed by field name, because the fields are the
-- template's decision and not the schema's: a new field type must not need a
-- migration.
--
-- SQLite boots apply this through migrate.py::_ensure_extract_results_schema
-- (idempotent, re-run every boot so a database whose watermark skipped 34 still
-- converges). The statements below are what that helper executes, listed here so
-- the file stays the readable record of the change; this file is not executed on
-- SQLite.

CREATE TABLE IF NOT EXISTS knowledge_extract_results (
  id               TEXT PRIMARY KEY,
  document_id      TEXT NOT NULL
                   REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  template_id      TEXT NOT NULL
                   REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
  template_version INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|succeeded|failed
  fields_json      TEXT NOT NULL DEFAULT '{}',
  error            TEXT,
  model            TEXT NOT NULL DEFAULT '',
  parser_version   TEXT NOT NULL DEFAULT '',
  content_hash     TEXT NOT NULL DEFAULT '',
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_results_document_template
  ON knowledge_extract_results (document_id, template_id);
CREATE INDEX IF NOT EXISTS idx_extract_results_template
  ON knowledge_extract_results (template_id, status);

UPDATE _schema_version SET version = 34;
