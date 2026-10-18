-- Prompt-driven shared collections over authorized source reads.
-- v40 owner-private keyword collections become prompt-registered collections whose
-- access derives from the source ACL; legacy verified reads survive as evidence.
ALTER TABLE knowledge_collections ADD COLUMN prompt TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN prompt_updated_at INTEGER;
ALTER TABLE knowledge_collections ADD COLUMN generation_state TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE knowledge_collections ADD COLUMN generation_error TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN generated_model TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN generated_at INTEGER;
-- Old keyword lists migrate into the freeform prompt that replaces them. The keyword
-- MATCH entries themselves are not prompt-backed synthesis and are not migrated as
-- topic results; collections regenerate topics from real source reads.
UPDATE knowledge_collections
SET prompt = COALESCE((
  SELECT group_concat(je.value, '; ')
  FROM json_each(knowledge_collections.keywords_json) AS je
), '')
WHERE EXISTS (SELECT 1 FROM json_each(knowledge_collections.keywords_json) AS je);
ALTER TABLE knowledge_collections DROP COLUMN keywords_json;
-- Platform-accumulated evidence of actual authorized reads, independent of any index.
CREATE TABLE IF NOT EXISTS knowledge_source_reads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evidence_id TEXT NOT NULL,
  knowledge_base_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',
  status TEXT NOT NULL DEFAULT 'legacy',
  locator_json TEXT NOT NULL DEFAULT '{}',
  text TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  images_json TEXT NOT NULL DEFAULT '[]',
  read_by INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (document_id, source_version, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_reads_scope
  ON knowledge_source_reads(knowledge_base_id, document_id, source_version, created_at);
-- Per-collection prompt-directed topic results for one source version.
CREATE TABLE IF NOT EXISTS knowledge_collection_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  collection_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  content TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  citations_json TEXT NOT NULL DEFAULT '[]',
  evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  corrections_json TEXT NOT NULL DEFAULT '[]',
  conflicts_json TEXT NOT NULL DEFAULT '[]',
  omissions_json TEXT NOT NULL DEFAULT '[]',
  model_ref TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  generated_at INTEGER,
  updated_at INTEGER NOT NULL,
  UNIQUE (collection_id, document_id, source_version),
  FOREIGN KEY (collection_id) REFERENCES knowledge_collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_topics_doc
  ON knowledge_collection_topics(collection_id, document_id);
-- Preserve v40 verified reading entries only where they are still truthful: the
-- source document must still exist and still hash to the version that was read.
-- They keep legacy status until a fresh source read re-verifies them.
INSERT OR IGNORE INTO knowledge_source_reads(
  evidence_id, knowledge_base_id, document_id, source_version, kind, status,
  locator_json, text, summary, images_json, read_by, created_at, updated_at
)
SELECT
  'legacy:' || e.document_id || ':' || substr(e.source_version, 1, 16) || ':' || e.segment_id,
  c.knowledge_base_id,
  e.document_id,
  e.source_version,
  'text',
  'legacy',
  e.locator_json,
  e.text,
  e.extractive_summary,
  '[]',
  NULL,
  e.updated_at,
  e.updated_at
FROM knowledge_collection_entries AS e
JOIN knowledge_collections AS c ON c.collection_id = e.collection_id
JOIN knowledge_documents AS d
  ON d.document_id = e.document_id AND d.content_hash = e.source_version
WHERE e.read = 1
  AND e.segment_id <> ''
  AND length(trim(e.text)) > 0;
-- Membership was derived index state; scope now derives from the target path at render time.
DROP TABLE IF EXISTS knowledge_collection_members;
DROP TABLE IF EXISTS knowledge_collection_entries;
-- Per-file/folder search rules are fully removed with their runtime.
DROP TABLE IF EXISTS knowledge_search_rule_vectors;
DROP TABLE IF EXISTS knowledge_search_rules;
DROP TABLE IF EXISTS knowledge_folder_search_rules;
-- Retrieval-shape knobs of the removed collection-first retrieval surface; the
-- ordinary lexical/vector search and chunk-size knobs keep their own keys.
DELETE FROM settings WHERE key IN ('knowledge_retrieval_k', 'knowledge_context_char_budget');
UPDATE _schema_version SET version = 41;
