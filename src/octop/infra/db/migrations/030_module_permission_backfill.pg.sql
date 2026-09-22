-- Schema v30: the accounts that already had these modules keep them. See
-- ``030_module_permission_backfill.sql`` for the reasoning; the two files are
-- the same change in each dialect.
--
-- PostgreSQL runs this file's watermark, and the backfill itself runs through
-- migrate.py::_backfill_new_module_permissions — the same read-modify-write of
-- the JSON columns that SQLite uses, so both dialects grant exactly the same
-- keys instead of two hand-written expressions that have to agree. The keys it
-- writes are named in that helper: ``experts`` / ``features`` / ``mbti``, plus
-- every ``channel_<kind>`` for accounts and departments that hold ``channels``.
--
-- The watermark is what keeps the backfill once-only: re-running it would hand
-- back a key an administrator had revoked in the meantime.

UPDATE _schema_version SET version = 30;
