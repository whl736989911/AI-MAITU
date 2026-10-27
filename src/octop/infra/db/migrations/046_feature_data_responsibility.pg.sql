-- Schema v46: feature data record responsibility — the effective writer.
-- Mirrors 046_feature_data_responsibility.sql. PostgreSQL applies this file;
-- migrate.py::_ensure_feature_data_responsibility_schema re-applies the same
-- shapes idempotently so a clamped watermark or repair pass cannot fail on
-- them.

ALTER TABLE feature_data_records ADD COLUMN IF NOT EXISTS responsible_user_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_feature_data_records_responsible
  ON feature_data_records(agent_id, responsible_user_id);

UPDATE _schema_version SET version = 46;
