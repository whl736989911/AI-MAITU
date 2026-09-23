"""Per-caller overlay text: what one user keeps saying about one feature.

A feature's own workflow is its author's, declared once for everybody, and it lives
with the feature (``.octop/workflow.json`` in its agent's workspace). This is the
other side of that arrangement: text a *caller* writes for themselves — by hand, or
summarised for them out of their own runs — which is injected above the definition
on their runs and on nobody else's.

Two things follow from the key being ``(feature_id, user_id)`` rather than an id:

* the row is replaced, never appended to. A caller has one standing instruction per
  feature, and saying it again is an edit of that instruction, not a second one.
* the feature id is the *feature's* public id (the one the ACL keys on) and not the
  ``feat-`` agent id: the feature is the resource, its agent is how it is reached,
  and the overlay belongs to the former.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


@dataclass(frozen=True)
class FeatureOverlayRow:
    """One caller's overlay on one feature."""

    feature_id: str
    user_id: int
    content: str
    updated_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureOverlayRow:
        return cls(
            feature_id=str(row["feature_id"]),
            user_id=int(row["user_id"]),
            content=str(row["content"]),
            updated_at=int(row["updated_at"]),
        )


class FeatureOverlayRepo:
    """Data-access object for the ``feature_user_overlays`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, *, feature_id: str, user_id: int) -> FeatureOverlayRow | None:
        """This caller's overlay on this feature, or ``None`` when they have none."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_user_overlays WHERE feature_id = ? AND user_id = ?",
                (feature_id, user_id),
            ).fetchone()
        return FeatureOverlayRow.from_row(row) if row else None

    def content(self, *, feature_id: str, user_id: int) -> str:
        """The overlay text alone, ``""`` when there is none — the run path's read."""
        row = self.get(feature_id=feature_id, user_id=user_id)
        return row.content if row is not None else ""

    def set(self, *, feature_id: str, user_id: int, content: str) -> FeatureOverlayRow:
        """Write (or replace) the caller's overlay and return the stored row."""
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_user_overlays(feature_id, user_id, content, updated_at)"
                " VALUES(?, ?, ?, ?)"
                " ON CONFLICT(feature_id, user_id) DO UPDATE SET"
                " content = excluded.content, updated_at = excluded.updated_at",
                (feature_id, user_id, content, ts),
            )
        row = self.get(feature_id=feature_id, user_id=user_id)
        assert row is not None
        return row

    def delete(self, *, feature_id: str, user_id: int) -> None:
        """Drop the caller's overlay. Idempotent: no row is not an error."""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM feature_user_overlays WHERE feature_id = ? AND user_id = ?",
                (feature_id, user_id),
            )

    def count_for_feature(self, *, feature_id: str) -> int:
        """How many callers keep an overlay on this feature.

        The governance answer without reading anybody's text: a feature's author may
        ask "is this run diverging from what I declared, and for how many people"
        without the platform handing them other users' notes.
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM feature_user_overlays WHERE feature_id = ?",
                (feature_id,),
            ).fetchone()
        if row is None:
            return 0
        return int(row["n"] if isinstance(row, Mapping) else row[0])


__all__ = ["FeatureOverlayRepo", "FeatureOverlayRow"]
