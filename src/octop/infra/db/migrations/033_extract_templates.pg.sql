-- Schema v33: extraction templates, their versions, and their bindings (design §7).
--
-- Read this together with 033_extract_templates.sql; the reasoning is there and
-- is identical for both dialects.
--
-- Idempotent throughout: a boot that converges after a skipped watermark re-runs
-- these statements, so every one is ``IF NOT EXISTS``.

CREATE TABLE IF NOT EXISTS knowledge_extract_templates (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active',
  current_version INTEGER NOT NULL DEFAULT 0,
  created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_extract_template_versions (
  id            TEXT PRIMARY KEY,
  template_id   TEXT NOT NULL
                REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
  version       INTEGER NOT NULL,
  fields_json   TEXT NOT NULL DEFAULT '[]',
  instruction   TEXT NOT NULL DEFAULT '',
  applies_to    TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at    BIGINT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_template_versions_unique
  ON knowledge_extract_template_versions (template_id, version);

CREATE TABLE IF NOT EXISTS knowledge_extract_bindings (
  id             TEXT PRIMARY KEY,
  template_id    TEXT NOT NULL
                 REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
  data_source_id TEXT NOT NULL
                 REFERENCES data_sources(id) ON DELETE CASCADE,
  path           TEXT NOT NULL DEFAULT '',
  extension      TEXT NOT NULL DEFAULT '',
  mime_type      TEXT NOT NULL DEFAULT '',
  name_pattern   TEXT NOT NULL DEFAULT '',
  match_regex    TEXT NOT NULL DEFAULT '',
  created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at     BIGINT NOT NULL,
  updated_at     BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extract_bindings_source
  ON knowledge_extract_bindings (data_source_id, path);
CREATE INDEX IF NOT EXISTS idx_extract_bindings_template
  ON knowledge_extract_bindings (template_id);

UPDATE _schema_version SET version = 33;
