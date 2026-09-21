-- Schema v22: run snapshot for feature_tasks — what produced this draft.
--
-- ``agent_id`` is the agent the run actually used, and ``injected_rule_ids``
-- the approved rules that rode along on its prompt. Both are written for
-- failed runs too: "this draft came out wrong" is only answerable with the
-- run's own inputs, which nothing else records.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_task_snapshot_schema:
-- ``ALTER TABLE ADD COLUMN`` is not idempotent, and the columns must also reach
-- databases whose watermark skipped 22.

ALTER TABLE feature_tasks ADD COLUMN agent_id TEXT;
ALTER TABLE feature_tasks ADD COLUMN injected_rule_ids TEXT;

UPDATE _schema_version SET version = 22;
