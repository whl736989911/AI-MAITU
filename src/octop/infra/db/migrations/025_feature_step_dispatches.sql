-- Schema v25: what a step turn dispatched to its subagents — 7.8's 分解留痕.
--
-- Design 7.7 leaves the decomposition to the model: whether a step is split, into
-- how many pieces, and who runs them are its calls, made through the harness's own
-- ``task`` tool (one call per subagent). The platform schedules none of it and
-- keeps only two things — the ceiling on how many run at once (``max_parallel``,
-- enforced by the dispatch boundary) and this record. So "这一步把它拆成了什么、
-- 每个子 agent 拿到了什么、谁在等槽位" is answerable after the turn.
--
-- One row per ``task`` call, written **after** the turn, from what the boundary
-- observed around the call. There is no plan row and no ``depends_on`` or output
-- column, because the call cannot carry them: the order subagents are dispatched
-- in *is* the dependency the model expressed, and recording fields the tool cannot
-- express would be fabrication.
--
-- SQLite boots apply this through migrate.py::_ensure_feature_step_dispatches_schema:
-- ``CREATE TABLE IF NOT EXISTS`` is idempotent, but the table must also reach
-- databases whose watermark skipped 25.

-- ``started_at`` / ``ended_at`` are ``now_ts`` (Unix seconds), the clock every
-- other run timestamp uses. ``ended_at`` is NULL for a dispatch that has no end —
-- the turn was cancelled while its subagent was still out — because writing a time
-- the boundary never saw would be worse than the gap.
CREATE TABLE IF NOT EXISTS feature_step_dispatches (
  id         TEXT PRIMARY KEY,          -- new_ulid()
  task_id    TEXT NOT NULL REFERENCES feature_runs(task_id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,          -- the step's position in the frozen plan
  step_id    TEXT NOT NULL,
  role       TEXT NOT NULL,             -- the subagent_type the model dispatched
  task       TEXT NOT NULL,             -- what that subagent was told
  status     TEXT NOT NULL,             -- succeeded | failed
  error      TEXT,
  result     TEXT,                      -- the answer, capped (see truncated)
  truncated  INTEGER NOT NULL DEFAULT 0,
  waited     INTEGER NOT NULL DEFAULT 0,  -- 1 = no free slot at call time
  waited_ms  INTEGER NOT NULL DEFAULT 0,
  slots      INTEGER NOT NULL DEFAULT 0,  -- concurrency when it started
  started_at INTEGER NOT NULL,
  ended_at   INTEGER,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_step_dispatches_step
  ON feature_step_dispatches (task_id, seq, started_at);

UPDATE _schema_version SET version = 25;
