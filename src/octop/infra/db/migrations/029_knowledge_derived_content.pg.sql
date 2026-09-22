-- Schema v29: the derived content a parsed document yields.
--
-- Read this together with 029_knowledge_derived_content.sql; the reasoning is
-- there and is identical for both dialects. What differs is only that
-- PostgreSQL can add a column in place, so there is nothing to rebuild.
--
-- Idempotent: ``IF NOT EXISTS`` keeps a re-run (a boot that converges after a
-- skipped watermark) from failing on the column it already added.

ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS derived_json TEXT NOT NULL DEFAULT '';

UPDATE _schema_version SET version = 29;
