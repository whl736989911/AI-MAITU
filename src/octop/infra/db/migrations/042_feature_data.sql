-- Generic per-feature business data: records, audit, versioned attachments and reminders.
CREATE TABLE IF NOT EXISTS feature_data_records (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  object_key TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  values_json TEXT NOT NULL DEFAULT '{}',
  visibility TEXT NOT NULL DEFAULT 'private',
  revision INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feature_data_records_list
  ON feature_data_records(agent_id, object_key, deleted_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_feature_data_records_creator
  ON feature_data_records(agent_id, created_by, updated_at);

CREATE TABLE IF NOT EXISTS feature_data_record_changes (
  id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'update',
  summary TEXT NOT NULL DEFAULT '',
  items_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL,
  FOREIGN KEY (record_id) REFERENCES feature_data_records(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feature_data_record_changes
  ON feature_data_record_changes(record_id, created_at);

CREATE TABLE IF NOT EXISTS feature_data_files (
  id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  field_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  uploaded_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  UNIQUE (record_id, field_key, version),
  FOREIGN KEY (record_id) REFERENCES feature_data_records(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feature_data_files_record
  ON feature_data_files(record_id, field_key, created_at);

CREATE TABLE IF NOT EXISTS feature_data_reminders (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  object_key TEXT NOT NULL,
  field_key TEXT NOT NULL,
  due_at TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  next_notify_at INTEGER,
  last_notified_at INTEGER,
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (record_id, field_key, due_at),
  FOREIGN KEY (record_id) REFERENCES feature_data_records(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feature_data_reminders_due
  ON feature_data_reminders(status, next_notify_at);

CREATE TABLE IF NOT EXISTS feature_data_notifications (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  reminder_id TEXT NOT NULL,
  cycle_key TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT 'in_app',
  read_at INTEGER,
  created_at INTEGER NOT NULL,
  UNIQUE (reminder_id, user_id, cycle_key),
  FOREIGN KEY (reminder_id) REFERENCES feature_data_reminders(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feature_data_notifications_user
  ON feature_data_notifications(user_id, read_at, created_at);
UPDATE _schema_version SET version = 42;
