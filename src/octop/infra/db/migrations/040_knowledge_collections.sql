-- Owner-private independent file/folder collections and source-located materializations.
CREATE TABLE IF NOT EXISTS knowledge_collections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  collection_id TEXT NOT NULL UNIQUE,
  knowledge_base_id TEXT NOT NULL,
  owner_user_id INTEGER NOT NULL,
  target_document_id TEXT NOT NULL,
  target_path TEXT NOT NULL,
  target_kind TEXT NOT NULL CHECK (target_kind IN ('file', 'folder')),
  name TEXT NOT NULL DEFAULT '',
  keywords_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (knowledge_base_id, owner_user_id, target_document_id),
  FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
  FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collections_owner
  ON knowledge_collections(owner_user_id, knowledge_base_id);
CREATE TABLE IF NOT EXISTS knowledge_collection_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  collection_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  locator_json TEXT NOT NULL DEFAULT '{}',
  text TEXT NOT NULL DEFAULT '',
  extractive_summary TEXT NOT NULL DEFAULT '',
  read INTEGER NOT NULL DEFAULT 0,
  keyword_matches_json TEXT NOT NULL DEFAULT '[]',
  metadata_matches_json TEXT NOT NULL DEFAULT '[]',
  updated_at INTEGER NOT NULL,
  UNIQUE (collection_id, document_id, source_version, segment_id),
  FOREIGN KEY (collection_id) REFERENCES knowledge_collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_entries_collection
  ON knowledge_collection_entries(collection_id, document_id, ordinal);
CREATE TABLE IF NOT EXISTS knowledge_collection_members (
  collection_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  PRIMARY KEY (collection_id, document_id),
  FOREIGN KEY (collection_id) REFERENCES knowledge_collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_members_document
  ON knowledge_collection_members(document_id, collection_id);
UPDATE _schema_version SET version = 40;
