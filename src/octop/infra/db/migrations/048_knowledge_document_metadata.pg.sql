-- Schema v48: per-knowledge-base metadata schemas and per-document values.

ALTER TABLE knowledge_bases
  ADD COLUMN IF NOT EXISTS metadata_schema TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_documents
  ADD COLUMN IF NOT EXISTS metadata_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 48;
