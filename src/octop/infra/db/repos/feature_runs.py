"""One submitted run of a feature's workflow — what it was given, what it ran under.

A run is read afterwards for one question only: *what produced this?* The submitted
values live here because nothing else keeps them — the message the card sent is a
rendering of them, and the definition may have been edited since — and the
definition is stored as a snapshot for the same reason, so editing a workflow never
rewrites what an earlier run was measured against.

The rows are the evidence an improvement is judged on later ("this same correction
came up in the last three runs"), so they are written on the *run path*, once, as
the run starts, and are never updated afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


def _json_object(raw: Any) -> dict[str, Any]:
    """A stored JSON object, or ``{}`` when the column cannot be read as one."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class FeatureRunRow:
    """One run: its identity, its caller, its input, and its definition snapshot."""

    id: str
    feature_id: str
    agent_id: str
    user_id: int
    thread_id: str | None
    inputs: dict[str, Any]
    definition: dict[str, Any]
    created_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureRunRow:
        thread_id = row["thread_id"]
        return cls(
            id=str(row["id"]),
            feature_id=str(row["feature_id"]),
            agent_id=str(row["agent_id"]),
            user_id=int(row["user_id"]),
            thread_id=str(thread_id) if thread_id else None,
            inputs=_json_object(row["inputs"]),
            definition=_json_object(row["definition"]),
            created_at=int(row["created_at"]),
        )


class FeatureRunRepo:
    """Data-access object for the ``feature_workflow_runs`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def record(
        self,
        *,
        feature_id: str,
        agent_id: str,
        user_id: int,
        thread_id: str | None,
        inputs: Mapping[str, Any],
        definition: Mapping[str, Any],
    ) -> FeatureRunRow:
        """Write one run as it starts. Returns the row, so the id can be handed on."""
        run_id = new_ulid()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_workflow_runs("
                "id, feature_id, agent_id, user_id, thread_id, inputs, definition, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    feature_id,
                    agent_id,
                    user_id,
                    thread_id,
                    json.dumps(dict(inputs), ensure_ascii=False),
                    json.dumps(dict(definition), ensure_ascii=False),
                    now_ts(),
                ),
            )
        row = self.get(run_id)
        assert row is not None
        return row

    def get(self, run_id: str) -> FeatureRunRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return FeatureRunRow.from_row(row) if row else None

    def list_for(
        self,
        *,
        feature_id: str,
        user_id: int,
        limit: int = 20,
    ) -> list[FeatureRunRow]:
        """One caller's most recent runs of one feature, newest first."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_workflow_runs"
                " WHERE feature_id = ? AND user_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (feature_id, user_id, max(1, int(limit))),
            ).fetchall()
        return [FeatureRunRow.from_row(row) for row in rows]

    def delete_for_feature(self, feature_id: str) -> None:
        """Drop every run of a feature — the feature is gone, so is its evidence."""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM feature_workflow_runs WHERE feature_id = ?",
                (feature_id,),
            )


__all__ = ["FeatureRunRepo", "FeatureRunRow"]
