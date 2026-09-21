"""Feature task log — one row per enterprise feature run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts

# ``feature_tasks.status`` once the initiator has approved the run output.
FINALIZED_STATUS = "finalized"


@dataclass(frozen=True)
class FeatureTaskRow:
    id: str
    feature_id: str
    user_id: int
    inputs: str
    draft: str | None
    final: str | None
    status: str
    error: str | None
    created_at: int
    diff_json: str | None
    finalized_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureTaskRow:
        return cls(
            id=str(r["id"]),
            feature_id=str(r["feature_id"]),
            user_id=int(r["user_id"]),
            inputs=str(r["inputs"]),
            draft=r["draft"],
            final=r["final"],
            status=str(r["status"]),
            error=r["error"],
            created_at=int(r["created_at"]),
            diff_json=r["diff_json"],
            finalized_at=None if r["finalized_at"] is None else int(r["finalized_at"]),
        )


class FeatureTaskRepo:
    """Data-access object for the feature_tasks table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        feature_id: str,
        user_id: int,
        inputs: str,
        status: str,
        draft: str | None = None,
        final: str | None = None,
        error: str | None = None,
        diff_json: str | None = None,
        finalized_at: int | None = None,
    ) -> FeatureTaskRow:
        task_id = str(uuid.uuid4())
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_tasks("
                "id, feature_id, user_id, inputs, draft, final, status, error, created_at, "
                "diff_json, finalized_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    feature_id,
                    user_id,
                    inputs,
                    draft,
                    final,
                    status,
                    error,
                    now_ts(),
                    diff_json,
                    finalized_at,
                ),
            )
        row = self.get(task_id)
        if row is None:
            raise RuntimeError(f"feature task insert failed: {task_id}")
        return row

    def get(self, task_id: str) -> FeatureTaskRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM feature_tasks WHERE id = ?", (task_id,)).fetchone()
        return FeatureTaskRow.from_row(r) if r else None

    def list_for_user(self, user_id: int, limit: int = 50) -> list[FeatureTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_tasks WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return map_rows(rows, FeatureTaskRow)

    def list_for_feature(self, feature_id: str, limit: int = 50) -> list[FeatureTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_tasks WHERE feature_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (feature_id, limit),
            ).fetchall()
        return map_rows(rows, FeatureTaskRow)

    def finalize(self, task_id: str, *, final: str, diff_json: str) -> FeatureTaskRow | None:
        """Stamp the human-approved text once; ``None`` when already finalized.

        ``finalized_at IS NULL`` in the WHERE clause makes the UPDATE itself the
        guard: a second submission (or a concurrent one) changes zero rows
        instead of overwriting the human's decision — a check-then-write would
        race and double-write. Finalizing is the anchor of the learning signal,
        so it is one-way.
        """
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_tasks SET final = ?, diff_json = ?, finalized_at = ?, status = ? "
                "WHERE id = ? AND finalized_at IS NULL",
                (final, diff_json, now_ts(), FINALIZED_STATUS, task_id),
            )
            if int(cursor.rowcount or 0) == 0:
                return None
        return self.get(task_id)
