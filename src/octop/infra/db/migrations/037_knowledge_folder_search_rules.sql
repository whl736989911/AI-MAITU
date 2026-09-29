-- Per-user search configuration follows folder identity across path renames.
CREATE TABLE IF NOT EXISTS knowledge_folder_search_rules (
  folder_document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('keyword', 'hybrid', 'exclude')),
  keywords_json TEXT NOT NULL DEFAULT '[]',
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (folder_document_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_folder_search_rules_user
  ON knowledge_folder_search_rules(user_id);
UPDATE _schema_version SET version = 37;
