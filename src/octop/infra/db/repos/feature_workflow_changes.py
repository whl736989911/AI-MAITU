"""Applied improvements to a feature's workflow — each with its own way back.

A row per applied change, holding the *diff* rather than a copy of the document:
one item per place it touched, with the value that was there and the value it left.
That is what makes the two halves of this feature work with one piece of state —
undo is the same list replayed in reverse (``infra/agents/feature_workflow_changes``),
and the history is readable without diffing two documents nobody kept.

``target`` says whether a change belongs to the feature's definition or a caller's
overlay. The author sees their own definition history; each caller sees only their
own overlay history, so an unpublished draft cannot leak through a diff.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid

TARGET_DEFINITION = "definition"
"""The change edited the feature's own workflow definition."""

TARGET_OVERLAY = "overlay"
"""The change edited one caller's own overlay."""

TARGETS = (TARGET_DEFINITION, TARGET_OVERLAY)

STATUS_APPLIED = "applied"
STATUS_REVERTED = "reverted"


def _items(raw: Any) -> list[dict[str, Any]]:
    """The stored diff, or ``[]`` when the column cannot be read as one."""
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, Mapping)]


@dataclass(frozen=True)
class FeatureChangeRow:
    """One applied (or reverted) improvement."""

    id: str
    feature_id: str
    user_id: int
    run_id: str | None
    target: str
    summary: str
    items: list[dict[str, Any]]
    status: str
    created_at: int
    reverted_at: int | None

    @property
    def is_reverted(self) -> bool:
        return str(self.status) == STATUS_REVERTED

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureChangeRow:
        run_id = row["run_id"]
        reverted_at = row["reverted_at"]
        return cls(
            id=str(row["id"]),
            feature_id=str(row["feature_id"]),
            user_id=int(row["user_id"]),
            run_id=str(run_id) if run_id else None,
            target=str(row["target"]),
            summary=str(row["summary"]),
            items=_items(row["items"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            reverted_at=int(reverted_at) if reverted_at else None,
        )


class FeatureChangeRepo:
    """Data-access object for the ``feature_workflow_changes`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def record(
        self,
        *,
        feature_id: str,
        user_id: int,
        target: str,
        summary: str,
        items: Sequence[Mapping[str, Any]],
        run_id: str | None = None,
    ) -> FeatureChangeRow:
        """Write one applied change. Called only after the document was written."""
        change_id = new_ulid()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_workflow_changes("
                "id, feature_id, user_id, run_id, target, summary, items, status, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    change_id,
                    feature_id,
                    user_id,
                    run_id,
                    target,
                    summary,
                    json.dumps([dict(item) for item in items], ensure_ascii=False),
                    STATUS_APPLIED,
                    now_ts(),
                ),
            )
        row = self.get(change_id)
        assert row is not None
        return row

    def get(self, change_id: str) -> FeatureChangeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_workflow_changes WHERE id = ?",
                (change_id,),
            ).fetchone()
        return FeatureChangeRow.from_row(row) if row else None

    def list_for_feature(
        self,
        *,
        feature_id: str,
        user_id: int,
        limit: int = 20,
    ) -> list[FeatureChangeRow]:
        """Recent changes this caller may see, newest first.

        Both definition and overlay diffs are private to the user who applied them.
        A draft may contain material that must not reach other callers through its
        change history.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_workflow_changes"
                " WHERE feature_id = ? AND user_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (feature_id, user_id, max(1, int(limit))),
            ).fetchall()
        return [FeatureChangeRow.from_row(row) for row in rows]

    def mark_reverted(self, change_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_workflow_changes SET status = ?, reverted_at = ? WHERE id = ?",
                (STATUS_REVERTED, now_ts(), change_id),
            )

    def delete_for_feature(self, feature_id: str) -> None:
        """Drop every change record of a feature — it is gone, so is its history."""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM feature_workflow_changes WHERE feature_id = ?",
                (feature_id,),
            )


__all__ = [
    "STATUS_APPLIED",
    "STATUS_REVERTED",
    "TARGET_DEFINITION",
    "TARGET_OVERLAY",
    "TARGETS",
    "FeatureChangeRepo",
    "FeatureChangeRow",
]
