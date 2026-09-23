-- Schema v26: an agent says what it *is* (``agents.kind``), and the deleted
-- feature subsystem's tables go. See ``026_agent_kind.sql`` for the reasoning;
-- the two files are the same change in each dialect.
--
-- ``IF NOT EXISTS`` because PostgreSQL runs this file where SQLite routes the
-- version through idempotent helpers — but the watermark still makes each of
-- these statements run at most once, which is what keeps the backfill from
-- touching a row created after the column appeared.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'agent';

UPDATE agents SET kind = 'feature' WHERE user_id IS NULL;

DROP TABLE IF EXISTS feature_step_dispatches CASCADE;
DROP TABLE IF EXISTS feature_step_edits CASCADE;
DROP TABLE IF EXISTS feature_step_runs CASCADE;
DROP TABLE IF EXISTS feature_runs CASCADE;
DROP TABLE IF EXISTS feature_cases CASCADE;
DROP TABLE IF EXISTS feature_rules CASCADE;
DROP TABLE IF EXISTS feature_tasks CASCADE;

UPDATE _schema_version SET version = 26;
