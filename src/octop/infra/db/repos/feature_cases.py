"""Promoted feature cases — the curated few-shot library.

A case is a *reference*: the row points at a ``feature_tasks`` row that a human
chose to promote, and the content (``inputs`` / ``final`` / ``diff_json``) stays
in that table so the two copies cannot drift. Promotion is a human decision —
nothing accumulates here automatically, because cases are fed back into prompts
and an unnoticed mediocre sample is worse than a missing one.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts

# The content columns are read through the join, never copied into feature_cases.
# Only finalized tasks can be cases, so ``t.final`` is non-NULL on every row.
_CASE_SELECT = (
    "SELECT c.task_id, c.feature_id, c.promoted_by, c.promoted_at, c.note, "
    "t.inputs, t.final, t.diff_json "
    "FROM feature_cases c JOIN feature_tasks t ON t.id = c.task_id"
)


@dataclass(frozen=True)
class FeatureCaseRow:
    """One promoted case, joined with the task content it points at."""

    task_id: str
    feature_id: str
    promoted_by: int
    promoted_at: int
    note: str | None
    inputs: str
    final: str
    diff_json: str | None

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureCaseRow:
        return cls(
            task_id=str(r["task_id"]),
            feature_id=str(r["feature_id"]),
            promoted_by=int(r["promoted_by"]),
            promoted_at=int(r["promoted_at"]),
            note=r["note"],
            inputs=str(r["inputs"]),
            final=str(r["final"]),
            diff_json=r["diff_json"],
        )


class FeatureCaseRepo:
    """Data-access object for the feature_cases table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def promote(
        self,
        task_id: str,
        *,
        feature_id: str,
        promoted_by: int,
        note: str | None = None,
    ) -> FeatureCaseRow | None:
        """Promote *task_id* into the case library; ``None`` when the task is absent.

        Idempotent: re-promoting an existing case changes nothing (the first
        promotion keeps its author, timestamp and note) and returns the stored
        row, so a double click cannot silently rewrite the provenance.
        """
        with self._db.transaction() as conn:
            task = conn.execute("SELECT id FROM feature_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                return None
            conn.execute(
                "INSERT INTO feature_cases(task_id, feature_id, promoted_by, promoted_at, note) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (task_id) DO NOTHING",
                (task_id, feature_id, promoted_by, now_ts(), note),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> FeatureCaseRow | None:
        with self._db.connect() as conn:
            r = conn.execute(f"{_CASE_SELECT} WHERE c.task_id = ?", (task_id,)).fetchone()
        return FeatureCaseRow.from_row(r) if r else None

    def list_for_feature(self, feature_id: str, limit: int = 50) -> list[FeatureCaseRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                f"{_CASE_SELECT} WHERE c.feature_id = ? "
                "ORDER BY c.promoted_at DESC, c.task_id DESC LIMIT ?",
                (feature_id, limit),
            ).fetchall()
        return map_rows(rows, FeatureCaseRow)
