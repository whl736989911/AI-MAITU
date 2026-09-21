-- Schema v17: organizational units (flat, optional parent) plus unit-level
-- module permission grants, and the per-user unit/deny scope columns.

ALTER TABLE users ADD COLUMN org_unit TEXT;
ALTER TABLE users ADD COLUMN denied_permissions TEXT;

CREATE TABLE IF NOT EXISTS org_units (
  key        TEXT PRIMARY KEY,
  label_zh   TEXT NOT NULL,
  label_en   TEXT NOT NULL,
  parent_key TEXT REFERENCES org_units(key) ON DELETE SET NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS org_unit_permissions (
  unit_key       TEXT NOT NULL,
  permission_key TEXT NOT NULL,
  PRIMARY KEY (unit_key, permission_key)
);

CREATE INDEX IF NOT EXISTS idx_org_units_parent ON org_units (parent_key);

UPDATE _schema_version SET version = 17;
