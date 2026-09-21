-- Schema v19: feature self-improvement capture — the human edit result, and the
-- case / rule libraries the loop is built from.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_learning_schema:
-- ``ALTER TABLE ADD COLUMN`` is not idempotent, and the added columns/tables
-- must also reach databases whose watermark skipped 19.

ALTER TABLE feature_tasks ADD COLUMN diff_json TEXT;
ALTER TABLE feature_tasks ADD COLUMN finalized_at INTEGER;

-- A promoted case is a *reference* to the task: inputs/final stay in
-- feature_tasks so the two copies cannot drift.
CREATE TABLE IF NOT EXISTS feature_cases (
  task_id     TEXT PRIMARY KEY REFERENCES feature_tasks(id) ON DELETE CASCADE,
  feature_id  TEXT NOT NULL,
  promoted_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  promoted_at INTEGER NOT NULL,
  note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_feature_cases_feature
  ON feature_cases (feature_id, promoted_at);

-- Rules are per-feature: the quote-draft rules must not reach meeting notes.
-- ``source_task_ids`` is required so every rule can be traced back to the diffs
-- it was induced from; the injection query filters on (feature_id, status).
CREATE TABLE IF NOT EXISTS feature_rules (
  id              TEXT PRIMARY KEY,
  feature_id      TEXT NOT NULL,
  rule_text       TEXT NOT NULL,
  status          TEXT NOT NULL,
  source_task_ids TEXT NOT NULL,
  proposed_by     TEXT NOT NULL,
  approved_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at      INTEGER NOT NULL,
  reviewed_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_feature_rules_feature_status
  ON feature_rules (feature_id, status, created_at);

UPDATE _schema_version SET version = 19;
