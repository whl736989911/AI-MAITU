-- Schema v44: preserve the existing agent personalization surfaces while each
-- capability receives an independently revocable module permission key.
-- migrate.py performs the dialect-independent permission JSON read/modify/write.
UPDATE _schema_version SET version = 44;
