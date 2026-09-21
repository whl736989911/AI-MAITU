-- Schema v21: drop the three legacy global share booleans
-- (``agents.is_shared`` / ``connectors.shared`` / ``knowledge_bases.shared``).
--
-- ``resource_acl`` has been authoritative since v18, and v18 backfilled every
-- legacy flag into it, so the columns only ever mirrored the ACL — and a share
-- applied through the sharing pipeline never reached them at all. Dropping them
-- removes the last "looks like the truth, is not" data source.
--
-- The legacy values are the last surviving record of "this was published", so
-- they are mirrored into ``resource_acl`` first: dropping a column before
-- copying it would silently turn every published resource private. The
-- statements below are the same ones 018 ran (idempotent, never overwriting a
-- later ACL edit), so a database whose watermark skipped the v18 DML still
-- keeps its shares.
--
-- WHY EACH MIRROR IS WRAPPED IN A "DOES THE COLUMN STILL EXIST" GUARD.
-- Do not delete that guard as redundant -- it is what makes this file
-- re-appliable, and a rollback plus a redeploy is enough to need it.
--
-- ``_schema_version`` is rewound to the highest migration the *running build*
-- ships whenever that is below the stored value
-- (``migrate.py::_reconcile_pre_squash_schema_version``). Rolling a deployment
-- back to a build without 021 therefore rewinds the watermark 21 -> 20, and
-- re-deploying the 021 build applies this file again -- to a database whose
-- flag columns 021 has already dropped. PostgreSQL resolves column references
-- when it *parses* a statement, so a row-level guard
-- (``WHERE is_shared IS NOT NULL``) would not help: the statement has to be
-- skipped entirely, not filtered. Hence ``EXECUTE`` under an ``IF EXISTS``,
-- which reproduces exactly the semantics of the SQLite path
-- (``migrate.py::_drop_legacy_share_columns`` skips a table whose flag is
-- already gone). Unguarded, the second application raises ``UndefinedColumn``
-- and PostgreSQL never boots again; SQLite converges instead, so local
-- development and CI cannot see the difference.
--
-- PostgreSQL drops the column directly (``DROP COLUMN`` takes the partial flag
-- index with it); SQLite needs a table rebuild, which
-- migrate.py::_drop_legacy_share_columns performs after the same mirror.

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

-- Only now: anything still asking "was it published?" must read
-- ``resource_acl.visibility = 'public'``.
ALTER TABLE agents DROP COLUMN IF EXISTS is_shared;
DROP INDEX IF EXISTS idx_agents_shared;
ALTER TABLE connectors DROP COLUMN IF EXISTS shared;
DROP INDEX IF EXISTS idx_connectors_shared;
ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS shared;

UPDATE _schema_version SET version = 21;
