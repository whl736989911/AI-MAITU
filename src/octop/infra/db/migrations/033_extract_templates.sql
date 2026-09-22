-- Schema v33: extraction templates, their versions, and their bindings (design §7).
--
-- Three tables, because the design separates three ideas that have different
-- lifetimes:
--
--   * ``knowledge_extract_templates`` is the identity an administrator manages:
--     a name, a status, and which version is current. Nothing here changes when
--     a template is edited.
--   * ``knowledge_extract_template_versions`` is what an edit produces. Design
--     §7.3 is explicit that editing a template does not overwrite it — a new
--     version is written, so a later extraction can say which one produced it
--     and changing a template cannot silently rewrite history.
--   * ``knowledge_extract_bindings`` is where a template applies. Design §7.4's
--     tree (source → folder → file) is expressed by a single path column rather
--     than three tables or a level column: ``''`` is the whole source, a folder
--     path covers its contents, and a file path covers that file. Which level a
--     binding acts at is therefore a property of the candidate file, not a
--     second thing to keep in step.
--
-- SQLite boots apply this through migrate.py::_ensure_extract_templates_schema
-- (idempotent, re-run every boot so a database whose watermark skipped 33 still
-- converges). The statements below are what that helper executes, listed here so
-- the file stays the readable record of the change; this file is not executed on
-- SQLite.

CREATE TABLE IF NOT EXISTS knowledge_extract_templates (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active',   -- active | disabled
  current_version INTEGER NOT NULL DEFAULT 0,
  created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
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
  created_at    INTEGER NOT NULL
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
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extract_bindings_source
  ON knowledge_extract_bindings (data_source_id, path);
CREATE INDEX IF NOT EXISTS idx_extract_bindings_template
  ON knowledge_extract_bindings (template_id);

UPDATE _schema_version SET version = 33;
