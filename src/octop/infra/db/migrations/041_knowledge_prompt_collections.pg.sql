-- Prompt-driven shared collections over authorized source reads (PostgreSQL).
ALTER TABLE knowledge_collections ADD COLUMN prompt TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN prompt_updated_at BIGINT;
ALTER TABLE knowledge_collections ADD COLUMN generation_state TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE knowledge_collections ADD COLUMN generation_error TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN generated_model TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_collections ADD COLUMN generated_at BIGINT;
UPDATE knowledge_collections AS c
SET prompt = COALESCE(sub.joined, '')
FROM (
  SELECT k.collection_id AS cid, string_agg(j.value, '; ' ORDER BY j.ord) AS joined
  FROM knowledge_collections k
  CROSS JOIN LATERAL json_array_elements_text(k.keywords_json::json) WITH ORDINALITY AS j(value, ord)
  GROUP BY k.collection_id
) AS sub
WHERE c.collection_id = sub.cid;
ALTER TABLE knowledge_collections DROP COLUMN keywords_json;
CREATE TABLE IF NOT EXISTS knowledge_source_reads (
  id BIGSERIAL PRIMARY KEY,
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
  read_by BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  UNIQUE (document_id, source_version, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_reads_scope
  ON knowledge_source_reads(knowledge_base_id, document_id, source_version, created_at);
CREATE TABLE IF NOT EXISTS knowledge_collection_topics (
  id BIGSERIAL PRIMARY KEY,
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
  generated_at BIGINT,
  updated_at BIGINT NOT NULL,
  UNIQUE (collection_id, document_id, source_version),
  FOREIGN KEY (collection_id) REFERENCES knowledge_collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_topics_doc
  ON knowledge_collection_topics(collection_id, document_id);
INSERT INTO knowledge_source_reads(
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
  AND length(trim(e.text)) > 0
ON CONFLICT (document_id, source_version, evidence_id) DO NOTHING;
DROP TABLE IF EXISTS knowledge_collection_members;
DROP TABLE IF EXISTS knowledge_collection_entries;
DROP TABLE IF EXISTS knowledge_search_rule_vectors;
DROP TABLE IF EXISTS knowledge_search_rules;
DROP TABLE IF EXISTS knowledge_folder_search_rules;
-- Retrieval-shape knobs of the removed collection-first retrieval surface; the
-- ordinary lexical/vector search and chunk-size knobs keep their own keys.
DELETE FROM settings WHERE key IN ('knowledge_retrieval_k', 'knowledge_context_char_budget');
UPDATE _schema_version SET version = 41;
