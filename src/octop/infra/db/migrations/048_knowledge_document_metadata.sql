-- Schema v48: per-knowledge-base metadata schemas and per-document values.
--
-- ``metadata_schema`` is a JSON array of field definitions on the base;
-- ``metadata_json`` stores the values on one document. Both are plain TEXT so
-- SQLite and PostgreSQL share one read path, and both are validated in
-- ``octop.infra.knowledge.metadata`` rather than by database constraints.

ALTER TABLE knowledge_bases ADD COLUMN metadata_schema TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_documents ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 48;
