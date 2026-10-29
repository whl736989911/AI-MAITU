-- Schema v46: feature data record responsibility — the effective writer.
--
-- ``feature_data_records.responsible_user_id`` names the account a shared
-- record currently belongs to for writes, reminders and handover. It is set
-- to the creator on insert and moves only through the atomic handover
-- operation; NULL means "fall back to created_by", which keeps every record
-- that predates this column exactly where it was. Private drafts never carry
-- a different responsible user, so an enterprise manager still cannot reach
-- them.
--
-- SQLite boots apply this through
-- migrate.py::_ensure_feature_data_responsibility_schema, which re-applies the
-- same shapes idempotently; this file is not executed on SQLite and is the
-- readable record of the change.
ALTER TABLE feature_data_records ADD COLUMN responsible_user_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_feature_data_records_responsible
  ON feature_data_records(agent_id, responsible_user_id);

UPDATE _schema_version SET version = 46;
