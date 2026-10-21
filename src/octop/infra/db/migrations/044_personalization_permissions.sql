-- Schema v44: preserve the existing agent personalization surfaces while each
-- capability receives an independently revocable module permission key.
-- The permission JSON read/modify/write is implemented once in migrate.py so
-- SQLite and PostgreSQL honor explicit denies identically.
UPDATE _schema_version SET version = 44;
