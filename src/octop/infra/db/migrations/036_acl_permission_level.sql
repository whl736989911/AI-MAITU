-- Schema v36: an ACL entry says what sharing it lets people *do* — read or write.
--
-- Read this together with 036_acl_permission_level.pg.sql; the reasoning is
-- there and is identical for both dialects.
--
-- SQLite boots apply this through migrate.py::_ensure_acl_permission_level,
-- which adds the column only when it is missing: ``ALTER TABLE ... ADD COLUMN``
-- has no ``IF NOT EXISTS`` here, and the helper is also what repairs a database
-- whose recorded version skipped 036. The statement below is what that helper
-- executes, listed here so the file stays the readable record of the change;
-- this file is not executed on SQLite.
--
-- ``read`` is the default and the backfill in one word: every row that exists
-- when this runs keeps exactly the reach it had, and gains no write. That is
-- what makes the ``public`` row a knowledge space is seeded with (v27) mean
-- "everyone may read this", never "everyone may write this".
--
-- No ``CHECK`` constraint on ``permission``: it mirrors the column next to it
-- (``visibility TEXT NOT NULL``), and the value is validated where it enters —
-- the API's ``Permission`` literal and ``ResourceAclRepo._validate_permission``.
-- A level the rules cannot read is answered by ``can_write`` as "no write".

ALTER TABLE resource_acl ADD COLUMN permission TEXT NOT NULL DEFAULT 'read';

-- One caller's own standing text on one feature's workflow (schema v36).
--
-- A feature's workflow is declared once, by its author, for everybody; this is
-- the layer above it — a caller's own wording, injected *after* the definition
-- and stated to win where the two disagree. Keyed by ``(feature_id, user_id)``
-- because that is exactly what it is: one person's instruction on one feature,
-- never the feature's configuration, and never another caller's to read.
--
-- ``feature_id`` is the feature's public id (the one the ACL keys on), not the
-- ``feat-`` agent id: the feature is the resource, the agent is how it is reached.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_overlay_schema,
-- which creates the table on every boot (idempotent), so a database whose
-- watermark skipped 036 gets it too; this file is not executed on SQLite.
CREATE TABLE IF NOT EXISTS feature_user_overlays (
  feature_id TEXT NOT NULL,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (feature_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_feature_user_overlays_user
  ON feature_user_overlays(user_id);

-- One submitted run of a feature's workflow (schema v36).
--
-- The evidence layer: what a run was given, and which definition it ran under.
-- Without it "this draft came out wrong" has no answer — the card's submission is
-- the only record of what the run actually received, and a definition edited
-- afterwards must not rewrite what an earlier run was measured against (hence the
-- snapshot column, which is the definition as it stood, not a live reference).
--
-- ``inputs`` is the submitted values as JSON; ``thread_id`` is the conversation the
-- run happened in, so the card and its result can be found again.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_run_schema; this file
-- is not executed on SQLite.
CREATE TABLE IF NOT EXISTS feature_workflow_runs (
  id         TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL,
  agent_id   TEXT NOT NULL,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  thread_id  TEXT,
  inputs     TEXT NOT NULL,
  definition TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_workflow_runs_feature_user
  ON feature_workflow_runs(feature_id, user_id, created_at);

-- One applied improvement to a feature's workflow — what it changed, and whether it
-- still stands (schema v36).
--
-- An improvement is a diff: ``items`` is JSON, one entry per place it touched
-- (``{"path": "/rules/1", "before": null, "after": "金额逐行核对"}``). ``before``
-- is what makes the change *undoable* without a second code path that "knows" how
-- to reverse a particular edit, and it is what makes applying safe: the value the
-- change expected must still be there, or the batch is refused rather than
-- overwriting an edit made elsewhere.
--
-- ``target`` is ``definition`` (its author's document) or ``overlay`` (one caller's
-- own text), so a revert knows which document to rebuild. ``run_id`` ties a change
-- back to the run whose corrections it was summarised from — the evidence, not a
-- copy of it.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_change_schema; this
-- file is not executed on SQLite.
CREATE TABLE IF NOT EXISTS feature_workflow_changes (
  id          TEXT PRIMARY KEY,
  feature_id  TEXT NOT NULL,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  run_id      TEXT,
  target      TEXT NOT NULL,
  summary     TEXT NOT NULL,
  items       TEXT NOT NULL,
  status      TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  reverted_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_feature_workflow_changes_feature
  ON feature_workflow_changes(feature_id, created_at);

UPDATE _schema_version SET version = 36;
