-- Schema v49: typed facts extracted from a document, with their source locator.
--
-- SQLite boots apply this through migrate.py::_ensure_knowledge_facts_schema,
-- which creates the same shapes idempotently; this file is the readable record
-- and is not executed on SQLite.

CREATE TABLE IF NOT EXISTS knowledge_document_facts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id   TEXT NOT NULL
                REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  kb_id         TEXT NOT NULL,
  fact_type     TEXT NOT NULL,
  value         TEXT NOT NULL,
  normalized    TEXT NOT NULL DEFAULT '',
  locator_json  TEXT NOT NULL DEFAULT '{}',
  extractor     TEXT NOT NULL DEFAULT '',
  created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_facts_document
  ON knowledge_document_facts(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_facts_lookup
  ON knowledge_document_facts(kb_id, fact_type, normalized);

UPDATE _schema_version SET version = 49;
