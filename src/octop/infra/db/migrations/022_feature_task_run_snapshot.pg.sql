-- Schema v22: run snapshot for feature_tasks — what produced this draft.
--
-- ``agent_id`` is the agent the run actually used, and ``injected_rule_ids``
-- the approved rules that rode along on its prompt. Both are written for
-- failed runs too: "this draft came out wrong" is only answerable with the
-- run's own inputs, which nothing else records.
--
-- PostgreSQL runs this file directly (``IF NOT EXISTS`` keeps it idempotent);
-- SQLite runs migrate.py::_ensure_feature_task_snapshot_schema with the same DDL.

ALTER TABLE feature_tasks ADD COLUMN IF NOT EXISTS agent_id TEXT;
ALTER TABLE feature_tasks ADD COLUMN IF NOT EXISTS injected_rule_ids TEXT;

UPDATE _schema_version SET version = 22;
