-- Schema v21: drop the three legacy global share booleans
-- (``agents.is_shared`` / ``connectors.shared`` / ``knowledge_bases.shared``).
--
-- ``resource_acl`` has been authoritative since v18, and v18 backfilled every
-- legacy flag into it, so the columns only ever mirrored the ACL — and a share
-- applied through the sharing pipeline never reached them at all. Dropping them
-- removes the last "looks like the truth, is not" data source.
--
-- SQLite keeps no ``DROP COLUMN`` here: both flag indexes are partial indexes on
-- the flag itself, and a rebuilt table is the only way to be sure the remaining
-- columns survive. Each table is therefore re-listed explicitly — every column
-- present in v20, ``max_documents`` (v10) and the profile columns (v7)
-- included — and the rows are copied across by name.
--
-- SQLite boots apply this through migrate.py::_drop_legacy_share_columns, which
-- mirrors the flags into ``resource_acl`` *before* dropping them and is
-- idempotent; the rebuild below is what the helper executes, listed here so the
-- file stays the readable record of the change. PostgreSQL drops the columns in
-- 021_drop_legacy_share_columns.pg.sql.

PRAGMA foreign_keys = OFF;
-- ``foreign_keys = OFF`` is not enough: SQLite rewrites the ``REFERENCES``
-- clauses of the tables pointing at a renamed table unless
-- ``legacy_alter_table`` is on, which would leave ``threads.agent_id`` and
-- ``knowledge_documents.kb_id`` pointing at a table this file drops.
PRAGMA legacy_alter_table = ON;

ALTER TABLE agents RENAME TO agents_legacy;
CREATE TABLE agents (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id            TEXT NOT NULL UNIQUE,
  user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
  name                TEXT NOT NULL,
  description         TEXT,
  persona_mbti        TEXT,
  default_model       TEXT,
  system_prompt       TEXT,
  enabled             INTEGER NOT NULL DEFAULT 1,
  config_json         TEXT,
  last_state          TEXT,
  last_error          TEXT,
  icon                TEXT,
  template_name       TEXT,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  color               TEXT,
  icon_name           TEXT,
  icon_url            TEXT,
  skill_package_ids   TEXT,
  published_expert_id TEXT,
  welcome_message     TEXT,
  knowledge_base_ids  TEXT,
  mcp_servers         TEXT
);
INSERT INTO agents(
  id, agent_id, user_id, name, description, persona_mbti, default_model,
  system_prompt, enabled, config_json, last_state, last_error, icon,
  template_name, created_at, updated_at, color, icon_name, icon_url,
  skill_package_ids, published_expert_id, welcome_message, knowledge_base_ids,
  mcp_servers
)
SELECT
  id, agent_id, user_id, name, description, persona_mbti, default_model,
  system_prompt, enabled, config_json, last_state, last_error, icon,
  template_name, created_at, updated_at, color, icon_name, icon_url,
  skill_package_ids, published_expert_id, welcome_message, knowledge_base_ids,
  mcp_servers
FROM agents_legacy;
DROP TABLE agents_legacy;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_user_name
  ON agents(user_id, name) WHERE user_id IS NOT NULL;

ALTER TABLE connectors RENAME TO connectors_legacy;
CREATE TABLE connectors (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id           TEXT NOT NULL UNIQUE,
  user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind                  TEXT NOT NULL,
  display_name          TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'active',
  mcp_server_name       TEXT NOT NULL UNIQUE,
  credential_blob       BLOB,
  credential_expires_at INTEGER,
  credential_rotated_at INTEGER,
  config_json           TEXT,
  created_at            INTEGER NOT NULL,
  updated_at            INTEGER NOT NULL
);
INSERT INTO connectors(
  id, instance_id, user_id, kind, display_name, status, mcp_server_name,
  credential_blob, credential_expires_at, credential_rotated_at, config_json,
  created_at, updated_at
)
SELECT
  id, instance_id, user_id, kind, display_name, status, mcp_server_name,
  credential_blob, credential_expires_at, credential_rotated_at, config_json,
  created_at, updated_at
FROM connectors_legacy;
DROP TABLE connectors_legacy;
CREATE INDEX IF NOT EXISTS idx_connectors_user ON connectors(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_user_display_name
  ON connectors(user_id, display_name) WHERE kind <> 'custom-mcp';

ALTER TABLE knowledge_bases RENAME TO knowledge_bases_legacy;
CREATE TABLE knowledge_bases (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  knowledge_base_id TEXT NOT NULL UNIQUE,
  owner_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  default_open      INTEGER NOT NULL DEFAULT 0,
  icon_name         TEXT NOT NULL DEFAULT '',
  embedding_model   TEXT NOT NULL DEFAULT '',
  embedding_dim     INTEGER NOT NULL DEFAULT 0,
  doc_count         INTEGER NOT NULL DEFAULT 0,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  max_documents     INTEGER NOT NULL DEFAULT 100,
  UNIQUE(owner_user_id, name)
);
INSERT INTO knowledge_bases(
  id, knowledge_base_id, owner_user_id, name, description, default_open,
  icon_name, embedding_model, embedding_dim, doc_count, created_at,
  updated_at, max_documents
)
SELECT
  id, knowledge_base_id, owner_user_id, name, description, default_open,
  icon_name, embedding_model, embedding_dim, doc_count, created_at,
  updated_at, max_documents
FROM knowledge_bases_legacy;
DROP TABLE knowledge_bases_legacy;
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner
  ON knowledge_bases(owner_user_id);

PRAGMA foreign_keys = ON;

UPDATE _schema_version SET version = 21;
