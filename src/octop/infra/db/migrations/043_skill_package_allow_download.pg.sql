-- Schema v43: a skill package can disable ZIP download for non-owners.
--
-- Mirrors 043_skill_package_allow_download.sql. PostgreSQL applies this file
-- directly; ``IF NOT EXISTS`` keeps it safe against the ensure helper in
-- migrate.py having added the column first (legacy repair runs before the
-- numbered migrations).
--
-- ``1`` (allow) is the default: existing packages keep the reach they had,
-- and the creator restricts the package afterwards via PATCH.

ALTER TABLE skill_packages ADD COLUMN IF NOT EXISTS allow_download INTEGER NOT NULL DEFAULT 1;

UPDATE _schema_version SET version = 43;
