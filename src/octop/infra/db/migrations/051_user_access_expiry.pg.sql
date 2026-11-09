-- Schema v51: per-user temporary access expiry + custom notice.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS access_expires_at BIGINT;
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS access_notice TEXT;

UPDATE _schema_version SET version = 51;
