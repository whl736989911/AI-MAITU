-- A user can belong to more than one department: sharing decisions must union
-- every unit the account holds, not just its primary one. ``users.org_unit``
-- stays as the "primary" department for display, and this table is the source
-- of truth for scope resolution. Existing rows are backfilled so nothing
-- changes for single-department accounts.
CREATE TABLE IF NOT EXISTS user_org_units (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unit_key TEXT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT extract(epoch FROM now())::bigint,
    PRIMARY KEY (user_id, unit_key)
);

CREATE INDEX IF NOT EXISTS idx_user_org_units_unit ON user_org_units(unit_key);

INSERT INTO user_org_units(user_id, unit_key)
SELECT id, org_unit FROM users WHERE org_unit IS NOT NULL AND org_unit <> ''
ON CONFLICT DO NOTHING;

UPDATE _schema_version SET version = 47;
