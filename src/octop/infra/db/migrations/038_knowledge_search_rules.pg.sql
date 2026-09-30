-- Canonical v37 -> v38 DDL. migrate.py applies its idempotent equivalent
-- so databases with a restored watermark preserve both old and new rule rows.
-- Keep existing directory search rules while allowing rules on individual documents.
ALTER TABLE knowledge_folder_search_rules RENAME TO knowledge_search_rules;
ALTER TABLE knowledge_search_rules RENAME COLUMN folder_document_id TO document_id;
DROP INDEX IF EXISTS idx_knowledge_folder_search_rules_user;
CREATE INDEX IF NOT EXISTS idx_knowledge_search_rules_user ON knowledge_search_rules(user_id);

-- Extraction templates are independent of indexing/search and have been removed.
-- Drop their historical versions, bindings, and results in dependency order.
DROP TABLE IF EXISTS knowledge_extract_results;
DROP TABLE IF EXISTS knowledge_extract_bindings;
DROP TABLE IF EXISTS knowledge_extract_template_versions;
DROP TABLE IF EXISTS knowledge_extract_templates;
UPDATE _schema_version SET version = 38;
