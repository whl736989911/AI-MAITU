-- Cache personal keyword embeddings by rule, keyword, and embedding model.
CREATE TABLE IF NOT EXISTS knowledge_search_rule_vectors (
  document_id TEXT NOT NULL,
  user_id BIGINT NOT NULL,
  model_key TEXT NOT NULL,
  keyword TEXT NOT NULL,
  embedding BYTEA NOT NULL,
  PRIMARY KEY (document_id, user_id, model_key, keyword),
  FOREIGN KEY (document_id, user_id) REFERENCES knowledge_search_rules(document_id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_search_rule_vectors_user_model
  ON knowledge_search_rule_vectors(user_id, model_key);
UPDATE _schema_version SET version = 39;
