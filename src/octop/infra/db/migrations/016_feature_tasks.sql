-- Schema v16: enterprise feature task log — one row per feature run
-- (inputs, AI draft, human final).

CREATE TABLE IF NOT EXISTS feature_tasks (
  id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  inputs TEXT NOT NULL,
  draft TEXT,
  final TEXT,
  status TEXT NOT NULL,
  error TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_tasks_user_created
  ON feature_tasks (user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_feature_tasks_feature_id
  ON feature_tasks (feature_id);

UPDATE _schema_version SET version = 16;
