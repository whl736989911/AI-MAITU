-- Schema v26: an agent says what it *is* (``agents.kind``), and the deleted
-- feature subsystem's tables go.
--
-- An agent row now has one of two kinds — the user's own agents (``agent``, the
-- default, the experts the product has always had) and a feature's own agent
-- (``feature``, created with the feature and owned by its author). Ownership
-- cannot express the difference any more: a feature's agent has an author, and
-- the author *is* an owner, so "who owns this" and "what is this" are two
-- columns. The one predicate that used to be derived from ownership — a shared
-- agent's workspace ``MEMORY.md`` is not written by a run (it serves every
-- caller of the feature, and its author owns the configuration, not the
-- context) — reads ``kind`` from here on.
--
-- The backfill is the one statement whose scope is historical: before this
-- column existed, an app-owned row (``user_id IS NULL``) could only ever be a
-- feature's own agent — the feature subsystem was the only creation path that
-- left the owner NULL — and its memory was frozen on exactly that predicate.
-- Marking those rows keeps every one of them behaving as it did, which is why
-- the UPDATE is guarded to the single moment the column appears (see
-- ``migrate.py::_ensure_agent_kind_column``): re-running it would flip an
-- app-owned row created *afterwards*.
--
-- The dropped tables are the old feature implementation's persistence —
-- definitions, run logs, promoted cases and induced rules. Nothing reads them
-- any more (``src/octop/infra/features/**`` and its router are gone), and they
-- are not a shape the rebuilt model keeps: a feature is an agent now. Migrations
-- 016–025 stay in the chain as the record of how those tables were built;
-- SQLite boots skip their DDL (see ``_apply_sqlite_migration``), PostgreSQL
-- runs them and drops them again here.
--
-- SQLite boots apply this through migrate.py::_ensure_agent_kind_column and
-- ::_drop_feature_tables — ``ALTER TABLE ADD COLUMN`` is not idempotent and the
-- backfill must run exactly once; the statements below are the readable record
-- of the change. PostgreSQL runs ``026_agent_kind.pg.sql``.

ALTER TABLE agents ADD COLUMN kind TEXT NOT NULL DEFAULT 'agent';

UPDATE agents SET kind = 'feature' WHERE user_id IS NULL;

-- Children first: every one of them references ``feature_tasks``/``feature_runs``.
DROP TABLE IF EXISTS feature_step_dispatches;
DROP TABLE IF EXISTS feature_step_edits;
DROP TABLE IF EXISTS feature_step_runs;
DROP TABLE IF EXISTS feature_runs;
DROP TABLE IF EXISTS feature_cases;
DROP TABLE IF EXISTS feature_rules;
DROP TABLE IF EXISTS feature_tasks;

UPDATE _schema_version SET version = 26;
