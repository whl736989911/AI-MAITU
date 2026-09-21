-- Schema v24: a feature run's steps — where it is, what each step produced, and
-- every artifact a human changed.
--
-- The run is a state machine that has to survive a gate: a `confirm` gate returns
-- from the request, and approving it later has to resume *the same run* knowing
-- which step it stopped at and which artifacts the earlier steps produced. That
-- state lives here, not in memory.
--
--   feature_runs       one row per run: status, current step, the gate to wait on,
--                      the definition frozen at start, and the run snapshot
--                      (agent, model, capability, rules) that design §4 requires.
--   feature_step_runs  one row per step of that frozen plan: status, the artifact
--                      it produced (typed, as JSON), attempts, timing, error.
--   feature_step_edits the human write log: every edit and every rewind void, with
--                      the value before and after. "Who changed the cutting speed
--                      from 120 to 100" is answerable from here and nowhere else.
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite runs migrate.py::_ensure_feature_step_runs_schema with the same DDL.

-- The frozen plan and the snapshot are JSON text: a run is audited as it ran, so
-- editing the definition afterwards must not rewrite history.
CREATE TABLE IF NOT EXISTS feature_runs (
  task_id      TEXT PRIMARY KEY REFERENCES feature_tasks(id) ON DELETE CASCADE,
  feature_id   TEXT NOT NULL,
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status       TEXT NOT NULL,
  current_step TEXT,
  current_seq  INTEGER,
  pending_gate TEXT,
  plan         TEXT NOT NULL,
  snapshot     TEXT,
  error        TEXT,
  created_at   BIGINT NOT NULL,
  updated_at   BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_runs_feature
  ON feature_runs (feature_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feature_runs_user
  ON feature_runs (user_id, created_at);

-- One row per step of the frozen plan. ``seq`` is the position in the plan and
-- the rewind unit: rewinding to a step voids every row at or after it, which is
-- what "作废它之后的所有产物" means in the database.
CREATE TABLE IF NOT EXISTS feature_step_runs (
  task_id         TEXT NOT NULL REFERENCES feature_runs(task_id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  step_id         TEXT NOT NULL,
  status          TEXT NOT NULL,
  artifact_name   TEXT,
  artifact_schema TEXT,
  artifact_value  TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  error           TEXT,
  started_at      BIGINT,
  ended_at        BIGINT,
  PRIMARY KEY (task_id, seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_step_runs_step
  ON feature_step_runs (task_id, step_id);

-- ``kind`` is 'edit' (a human supplied or corrected a value) or 'void' (a rewind
-- threw a step's artifact away — the discarded value is kept in before_value, so
-- the rerun's audit still shows what was replaced).
CREATE TABLE IF NOT EXISTS feature_step_edits (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL REFERENCES feature_runs(task_id) ON DELETE CASCADE,
  step_id      TEXT NOT NULL,
  artifact     TEXT NOT NULL,
  before_value TEXT,
  after_value  TEXT,
  by_user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  source       TEXT NOT NULL,
  created_at   BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_step_edits_task
  ON feature_step_edits (task_id, created_at);

UPDATE _schema_version SET version = 24;
