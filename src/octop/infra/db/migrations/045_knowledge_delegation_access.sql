-- Schema v45: explicit multilevel knowledge delegation and durable
-- access-revocation notifications, plus the per-grant can_delegate switch.
--
-- ``resource_acl_grants.can_delegate`` marks *individual user* grants whose
-- holder may pass their entry-derived access on through the delegation graph.
-- It never widens direct access; ``sharing.can_access``/``can_write`` ignore it.
--
-- ``knowledge_delegations`` is one delegation edge per (document, grantor,
-- grantee): the authority a base/folder grant lends is always resolved for one
-- concrete document, so an edge can never leak a sibling file's right. The
-- ``id`` integer is the internal surrogate; ``delegation_id`` (public ULID) is
-- the row the API addresses. Edges are hard-deleted on revocation cascade, so
-- a re-enabled root switch cannot resurrect revoked branches.
--
-- ``knowledge_access_notifications`` is the narrow durable inbox for users
-- whose own individual grant or delegation edge was removed. Rows have no FK
-- to knowledge tables on purpose: an inbox must survive KB deletion, module
-- loss, and relogin. ``payload_json`` carries the structured before/after
-- rights and provenance; the UI localizes from these fields.
--
-- SQLite boots apply this through migrate.py::_ensure_knowledge_delegation_schema,
-- which re-applies the same shapes idempotently; this file is not executed on
-- SQLite and is the readable record of the change.
CREATE TABLE IF NOT EXISTS knowledge_delegations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  delegation_id   TEXT NOT NULL UNIQUE,
  kb_id           TEXT NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
  document_id     TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
  grantor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  grantee_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  can_delegate    INTEGER NOT NULL DEFAULT 0,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL,
  UNIQUE (document_id, grantor_user_id, grantee_user_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_delegations_grantee
  ON knowledge_delegations(grantee_user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_delegations_grantor
  ON knowledge_delegations(grantor_user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_delegations_kb_document
  ON knowledge_delegations(kb_id, document_id);

CREATE TABLE IF NOT EXISTS knowledge_access_notifications (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id   TEXT NOT NULL UNIQUE,
  recipient_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kb_id             TEXT NOT NULL,
  resource_id       TEXT NOT NULL,
  resource_path     TEXT NOT NULL DEFAULT '',
  event_kind        TEXT NOT NULL CHECK (event_kind IN
                      ('authorization_revoked', 'delegation_revoked')),
  access_retained   INTEGER NOT NULL DEFAULT 0,
  payload_json      TEXT NOT NULL DEFAULT '{}',
  read_at           INTEGER,
  created_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_access_notifications_recipient
  ON knowledge_access_notifications(recipient_user_id, created_at);

ALTER TABLE resource_acl_grants ADD COLUMN can_delegate INTEGER NOT NULL DEFAULT 0;

UPDATE _schema_version SET version = 45;
