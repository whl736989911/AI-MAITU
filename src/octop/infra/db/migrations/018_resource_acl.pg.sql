-- Schema v18: unified resource access control — one ACL entry per shareable
-- resource, extra grants, and a change/approval audit trail.

CREATE TABLE IF NOT EXISTS resource_acl (
  resource_type TEXT NOT NULL,
  resource_id   TEXT NOT NULL,
  owner_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
  visibility    TEXT NOT NULL,
  unit_key      TEXT REFERENCES org_units(key) ON DELETE SET NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  updated_at    BIGINT NOT NULL,
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
  actor_user_id BIGINT NOT NULL,
  from_version  INTEGER NOT NULL,
  to_version    INTEGER NOT NULL,
  before_json   TEXT NOT NULL,
  after_json    TEXT NOT NULL,
  impact_scope  TEXT NOT NULL,
  status        TEXT NOT NULL,
  reason        TEXT,
  created_at    BIGINT NOT NULL
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
-- resource_acl (a later edit, or an earlier boot) is never overwritten. Every
-- legacy row gets an ACL row, ownerless ones included: a NULL ``owner_user_id``
-- means "system-owned", and ``can_access`` rule 2 can never match it.
--
-- ``WHERE 1 = 1`` is a no-op for PostgreSQL, kept so this file stays
-- byte-comparable with ``018_resource_acl.sql`` and
-- ``migrate.py::_ensure_resource_acl_schema`` (SQLite needs the clause there).
-- All three must keep the same statements: SQLite boots run the helper.
--
-- The "does the column still exist" guard around each mirror is load-bearing,
-- not defensive: this file is re-applied whenever ``_schema_version`` is
-- rewound (``migrate.py::_reconcile_pre_squash_schema_version``), and by then
-- 021 may already have dropped the flag columns. See the header of
-- ``021_drop_legacy_share_columns.pg.sql`` for the full explanation before
-- removing it.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'agents'
      AND column_name = 'is_shared'
  ) THEN
    EXECUTE $mirror$
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'agent', agent_id, user_id,
  CASE WHEN is_shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, EXTRACT(EPOCH FROM NOW())::BIGINT
FROM agents
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING
$mirror$;
  END IF;
END
$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'connectors'
      AND column_name = 'shared'
  ) THEN
    EXECUTE $mirror$
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'connector', instance_id, user_id,
  CASE WHEN shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, EXTRACT(EPOCH FROM NOW())::BIGINT
FROM connectors
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING
$mirror$;
  END IF;
END
$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'knowledge_bases'
      AND column_name = 'shared'
  ) THEN
    EXECUTE $mirror$
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'knowledge_base', knowledge_base_id, owner_user_id,
  CASE WHEN shared = 1 THEN 'public' ELSE 'private' END,
  NULL, 1, EXTRACT(EPOCH FROM NOW())::BIGINT
FROM knowledge_bases
WHERE 1 = 1
ON CONFLICT (resource_type, resource_id) DO NOTHING
$mirror$;
  END IF;
END
$$;

UPDATE _schema_version SET version = 18;
