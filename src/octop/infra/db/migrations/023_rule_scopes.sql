-- Schema v23: rule scope — the layer a rule lives at, and who it belongs to.
--
-- ``scope`` is the layer (personal | unit | global), ``owner_user_id`` the person
-- a personal rule is read for, ``unit_key`` the department a unit rule belongs
-- to. Every pre-v23 rule was feature-wide, which is exactly the global layer, so
-- the DEFAULT backfills the stored rows with the scope they already had.
-- ``unit_key`` deliberately carries no foreign key, like ``users.org_unit``: a
-- deleted unit must not silently turn its rules into someone else's.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_rule_scope_schema:
-- ``ALTER TABLE ADD COLUMN`` is not idempotent, and the columns must also reach
-- databases whose watermark skipped 23.

ALTER TABLE feature_rules ADD COLUMN scope TEXT NOT NULL DEFAULT 'global';
ALTER TABLE feature_rules ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE feature_rules ADD COLUMN unit_key TEXT;

-- The injection query filters on (feature_id, status) and ranks by scope.
CREATE INDEX IF NOT EXISTS idx_feature_rules_feature_scope
  ON feature_rules (feature_id, scope, status);

UPDATE _schema_version SET version = 23;
