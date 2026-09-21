-- Schema v18: unified resource access control — one ACL entry per shareable
-- resource, extra grants, and a change/approval audit trail.
--
-- SQLite boots apply this through migrate.py::_ensure_resource_acl_schema so
-- the legacy-flag backfill also runs for databases whose watermark skipped 18.

CREATE TABLE IF NOT EXISTS resource_acl (
  resource_type TEXT NOT NULL,
  resource_id   TEXT NOT NULL,
  owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  visibility    TEXT NOT NULL,
  unit_key      TEXT REFERENCES org_units(key) ON DELETE SET NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (resource_type, resource_id)
);

CREATE TABLE IF NOT EXISTS resource_acl_grants (
  resource_type TEXT NOT NULL,
  resource_id   TEXT NOT NULL,
  grantee_type  TEXT NOT NULL,
  grantee_id    TEXT NOT NULL,
  PRIMARY KEY (resource_type, resource_id, grantee_type, grantee_id)
);

CREATE TABLE IF NOT EXISTS resource_acl_changes (
  id            TEXT PRIMARY KEY,
  resource_type TEXT NOT NULL,
  resource_id   TEXT NOT NULL,
  actor_user_id INTEGER NOT NULL,
  from_version  INTEGER NOT NULL,
  to_version    INTEGER NOT NULL,
  before_json   TEXT NOT NULL,
  after_json    TEXT NOT NULL,
  impact_scope  TEXT NOT NULL,
  status        TEXT NOT NULL,
  reason        TEXT,
  created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resource_acl_owner ON resource_acl (owner_user_id);
CREATE INDEX IF NOT EXISTS idx_resource_acl_unit ON resource_acl (unit_key);
CREATE INDEX IF NOT EXISTS idx_resource_acl_grants_grantee
  ON resource_acl_grants (grantee_type, grantee_id);
CREATE INDEX IF NOT EXISTS idx_resource_acl_changes_resource
  ON resource_acl_changes (resource_type, resource_id, created_at);
CREATE INDEX IF NOT EXISTS idx_resource_acl_changes_status
  ON resource_acl_changes (status, created_at);

-- Backfill the legacy global booleans. Idempotent: a row already in
-- resource_acl (a later edit, or an earlier boot) is never overwritten.
-- Every legacy row gets an ACL row, ownerless ones included: a NULL
-- ``owner_user_id`` means "system-owned", and ``can_access`` rule 2 can never
-- match it (NULL never equals a user id), so those rows stay admin-only unless
-- the legacy flag publishes them.
--
-- ``WHERE 1 = 1`` on each SELECT is load-bearing: SQLite cannot parse an upsert
-- clause directly after ``INSERT ... SELECT`` without a WHERE (the ON could
-- belong to a join), and that failure only surfaces at runtime.
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'agent', agent_id, user_id,
  CASE WHEN is_shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, CAST(strftime('%s', 'now') AS INTEGER)
FROM agents
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING;

INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'connector', instance_id, user_id,
  CASE WHEN shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, CAST(strftime('%s', 'now') AS INTEGER)
FROM connectors
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING;

INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'knowledge_base', knowledge_base_id, owner_user_id,
  CASE WHEN shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, CAST(strftime('%s', 'now') AS INTEGER)
FROM knowledge_bases
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING;

UPDATE _schema_version SET version = 18;
