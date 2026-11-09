-- Schema v51: per-user temporary access expiry + custom notice.
--
-- ``access_expires_at`` is an absolute unix timestamp; NULL means the account
-- is permanent. ``access_notice`` is the administrator-authored message shown
-- when the account's access has expired (empty/NULL falls back to the
-- localized errors.ACCESS_EXPIRED default). Both are nullable so existing
-- accounts keep their permanent access.

ALTER TABLE users ADD COLUMN access_expires_at INTEGER;
ALTER TABLE users ADD COLUMN access_notice TEXT;

UPDATE _schema_version SET version = 51;
