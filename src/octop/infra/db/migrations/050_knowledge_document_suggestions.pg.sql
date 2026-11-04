-- Schema v50: AI enrichment suggestions awaiting human review.
CREATE TABLE IF NOT EXISTS knowledge_document_suggestions (
  id             BIGSERIAL PRIMARY KEY,
  suggestion_id  TEXT NOT NULL UNIQUE,
  document_id    TEXT NOT NULL
                 REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  kb_id          TEXT NOT NULL,
  content_hash   TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'pending',
  summary        TEXT NOT NULL DEFAULT '',
  keywords_json  TEXT NOT NULL DEFAULT '[]',
  fields_json    TEXT NOT NULL DEFAULT '{}',
  model          TEXT NOT NULL DEFAULT '',
  error          TEXT,
  created_at     BIGINT NOT NULL,
  updated_at     BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_document
  ON knowledge_document_suggestions(document_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_kb
  ON knowledge_document_suggestions(kb_id, status, created_at);

UPDATE _schema_version SET version = 50;
