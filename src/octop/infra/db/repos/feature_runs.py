"""Feature step runs — where a stepped feature run is, and what it produced.

``feature_runs`` holds the run (status, the gate it waits on, the step plan frozen
at start, the run snapshot); ``feature_step_runs`` holds one row per step of that
plan; ``feature_step_edits`` holds the human write log;
``feature_step_dispatches`` holds what each step turn dispatched to its subagents
(7.8's 分解留痕). Nothing here decides anything — the transitions live in
:mod:`octop.infra.features.runs`, and these methods are the only way that module
writes state down.

Two shapes are stored as JSON text on purpose: the **frozen plan** and the run
**snapshot**. A run is audited as it ran, so editing the feature definition after
a run started must not rewrite what that run was: the plan is re-read from the row,
never from the catalog.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import UNSET, DbRow, bool_int, map_rows, now_ts, optional_updates
from octop.infra.features.dispatch import DispatchEntry
from octop.infra.features.steps import Artifact
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FeatureRunRow:
    task_id: str
    feature_id: str
    user_id: int
    status: str
    current_step: str | None
    current_seq: int | None
    pending_gate: str | None
    plan: str
    snapshot: str | None
    error: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureRunRow:
        return cls(
            task_id=str(r["task_id"]),
            feature_id=str(r["feature_id"]),
            user_id=int(r["user_id"]),
            status=str(r["status"]),
            current_step=None if r["current_step"] is None else str(r["current_step"]),
            current_seq=None if r["current_seq"] is None else int(r["current_seq"]),
            pending_gate=None if r["pending_gate"] is None else str(r["pending_gate"]),
            plan=str(r["plan"]),
            snapshot=None if r["snapshot"] is None else str(r["snapshot"]),
            error=r["error"],
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )

    def pending_gate_payload(self) -> dict[str, Any] | None:
        """The gate this run waits on, as written by the engine."""
        if self.pending_gate is None:
            return None
        payload = json.loads(self.pending_gate)
        return payload if isinstance(payload, dict) else None

    def snapshot_payload(self) -> dict[str, Any]:
        """What produced this run (agent, model, capability, rules, plan)."""
        if self.snapshot is None:
            return {}
        payload = json.loads(self.snapshot)
        return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class FeatureStepRunRow:
    task_id: str
    seq: int
    step_id: str
    status: str
    artifact_name: str | None
    artifact_schema: str | None
    artifact_value: str | None
    attempts: int
    error: str | None
    started_at: int | None
    ended_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureStepRunRow:
        return cls(
            task_id=str(r["task_id"]),
            seq=int(r["seq"]),
            step_id=str(r["step_id"]),
            status=str(r["status"]),
            artifact_name=None if r["artifact_name"] is None else str(r["artifact_name"]),
            artifact_schema=None if r["artifact_schema"] is None else str(r["artifact_schema"]),
            artifact_value=None if r["artifact_value"] is None else str(r["artifact_value"]),
            attempts=int(r["attempts"]),
            error=r["error"],
            started_at=None if r["started_at"] is None else int(r["started_at"]),
            ended_at=None if r["ended_at"] is None else int(r["ended_at"]),
        )

    def artifact(self) -> Artifact | None:
        """The artifact this step holds, or ``None`` when it has none (yet/any more)."""
        if self.artifact_name is None or self.artifact_schema is None:
            return None
        return Artifact(
            name=self.artifact_name,
            schema=self.artifact_schema,
            value=None if self.artifact_value is None else json.loads(self.artifact_value),
            step_id=self.step_id,
        )


@dataclass(frozen=True)
class FeatureStepEditRow:
    id: str
    task_id: str
    step_id: str
    artifact: str
    before_value: str | None
    after_value: str | None
    by_user_id: int
    kind: str
    source: str
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureStepEditRow:
        return cls(
            id=str(r["id"]),
            task_id=str(r["task_id"]),
            step_id=str(r["step_id"]),
            artifact=str(r["artifact"]),
            before_value=r["before_value"],
            after_value=r["after_value"],
            by_user_id=int(r["by_user_id"]),
            kind=str(r["kind"]),
            source=str(r["source"]),
            created_at=int(r["created_at"]),
        )

    def before(self) -> Any:
        return None if self.before_value is None else json.loads(self.before_value)

    def after(self) -> Any:
        return None if self.after_value is None else json.loads(self.after_value)


@dataclass(frozen=True)
class FeatureStepDispatchRow:
    """One subagent a step turn dispatched, as the boundary recorded it.

    The rows are written after the turn and only ever read: nothing in the engine
    updates or deletes one, because the record answers "what did this step split
    itself into" and a later turn rewriting it would answer a different question.
    """

    id: str
    task_id: str
    seq: int
    step_id: str
    role: str
    task: str
    status: str
    error: str | None
    result: str | None
    truncated: bool
    waited: bool
    waited_ms: int
    slots: int
    started_at: int
    ended_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> FeatureStepDispatchRow:
        return cls(
            id=str(r["id"]),
            task_id=str(r["task_id"]),
            seq=int(r["seq"]),
            step_id=str(r["step_id"]),
            role=str(r["role"]),
            task=str(r["task"]),
            status=str(r["status"]),
            error=r["error"],
            result=r["result"],
            truncated=bool(r["truncated"]),
            waited=bool(r["waited"]),
            waited_ms=int(r["waited_ms"]),
            slots=int(r["slots"]),
            started_at=int(r["started_at"]),
            ended_at=None if r["ended_at"] is None else int(r["ended_at"]),
            created_at=int(r["created_at"]),
        )


class FeatureRunRepo:
    """Data-access object for ``feature_runs`` and its child tables."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # --- the run ---------------------------------------------------------

    def create(
        self,
        *,
        task_id: str,
        feature_id: str,
        user_id: int,
        status: str,
        plan: Sequence[Mapping[str, Any]],
        snapshot: Mapping[str, Any],
        step_ids: Sequence[str],
    ) -> FeatureRunRow:
        """Start one run: its row plus a ``pending`` row per planned step.

        The step rows exist from the start so "which steps are there" never has to
        be re-derived from the plan, and a step's absence from the API is an empty
        status rather than a missing row.
        """
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_runs("
                "task_id, feature_id, user_id, status, current_step, current_seq, pending_gate, "
                "plan, snapshot, error, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, ?, ?)",
                (
                    task_id,
                    feature_id,
                    user_id,
                    status,
                    json.dumps(list(plan), ensure_ascii=False),
                    json.dumps(dict(snapshot), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            for seq, step_id in enumerate(step_ids):
                conn.execute(
                    "INSERT INTO feature_step_runs(task_id, seq, step_id, status, attempts) "
                    "VALUES (?, ?, ?, 'pending', 0)",
                    (task_id, seq, step_id),
                )
        run = self.get(task_id)
        if run is None:
            raise RuntimeError(f"feature run insert failed: {task_id}")
        return run

    def get(self, task_id: str) -> FeatureRunRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
        return FeatureRunRow.from_row(row) if row else None

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        current_step: str | None | object = UNSET,
        current_seq: int | None | object = UNSET,
        pending_gate: Mapping[str, Any] | None | object = UNSET,
        error: str | None | object = UNSET,
    ) -> None:
        """Move the run to its next state; only the fields given are written.

        ``UNSET`` (the default) means "leave as it is" — a gate keeps the step that
        is *pending* a human while ``current_step`` still names the step the run
        stopped on, so the two cannot be set from one another by accident.
        """
        values: list[tuple[str, object]] = [("status", status)]
        if current_step is not UNSET:
            values.append(("current_step", current_step))
        if current_seq is not UNSET:
            values.append(("current_seq", current_seq))
        if pending_gate is not UNSET:
            values.append(
                (
                    "pending_gate",
                    None if pending_gate is None else json.dumps(pending_gate, ensure_ascii=False),
                )
            )
        if error is not UNSET:
            values.append(("error", error))
        values.append(("updated_at", now_ts()))
        clauses, params = optional_updates(values)
        if not clauses:
            return
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE feature_runs SET {', '.join(clauses)} WHERE task_id = ?",
                (*params, task_id),
            )

    # --- the steps -------------------------------------------------------

    def claim(self, task_id: str, *, expected_status: str, new_status: str) -> bool:
        """Move a run out of the state an action was decided on; ``False`` if taken.

        ``status = ?`` in the WHERE clause makes the UPDATE itself the guard, the
        same shape :meth:`FeatureTaskRepo.finalize` uses: two approvals of one gate
        cannot both walk the run on, so a step is never run twice by accident and no
        second set of tokens is spent on it. Checking first and writing after would
        race — the read would still say the run is waiting.
        """
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_runs SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                (new_status, now_ts(), task_id, expected_status),
            )
        return int(cursor.rowcount or 0) == 1

    def steps(self, task_id: str) -> list[FeatureStepRunRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_step_runs WHERE task_id = ? ORDER BY seq", (task_id,)
            ).fetchall()
        return map_rows(rows, FeatureStepRunRow)

    def start_step(self, task_id: str, seq: int) -> None:
        """One step's turn begins: count it, and start its clock."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_step_runs SET status = 'running', attempts = attempts + 1, "
                "started_at = ?, ended_at = NULL WHERE task_id = ? AND seq = ?",
                (now_ts(), task_id, seq),
            )

    def finish_step(
        self,
        task_id: str,
        seq: int,
        *,
        status: str,
        artifact: Artifact | None,
        error: str | None,
    ) -> None:
        """One step's outcome: its artifact (or none), its error, its end time."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_step_runs SET status = ?, artifact_name = ?, artifact_schema = ?, "
                "artifact_value = ?, error = ?, ended_at = ? WHERE task_id = ? AND seq = ?",
                (
                    status,
                    None if artifact is None else artifact.name,
                    None if artifact is None else artifact.schema,
                    None if artifact is None else json.dumps(artifact.value, ensure_ascii=False),
                    error,
                    now_ts(),
                    task_id,
                    seq,
                ),
            )

    def set_artifact(self, task_id: str, seq: int, artifact: Artifact, *, status: str) -> None:
        """Store an artifact a human supplied, and mark the step satisfied.

        The step's ``error`` is left as it was: it is why a human had to step in,
        and the edit log alone would not say.
        """
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_step_runs SET status = ?, artifact_name = ?, artifact_schema = ?, "
                "artifact_value = ? WHERE task_id = ? AND seq = ?",
                (
                    status,
                    artifact.name,
                    artifact.schema,
                    json.dumps(artifact.value, ensure_ascii=False),
                    task_id,
                    seq,
                ),
            )

    def void_steps_from(self, task_id: str, seq: int) -> None:
        """Throw away every artifact at or after *seq* — a rewind's own transaction.

        ``attempts`` is deliberately kept: it counts the turns this run spent on
        the step, and a rewind does not un-spend them.
        """
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_step_runs SET status = 'voided', artifact_name = NULL, "
                "artifact_schema = NULL, artifact_value = NULL, error = NULL, "
                "started_at = NULL, ended_at = NULL WHERE task_id = ? AND seq >= ?",
                (task_id, seq),
            )

    # --- the human write log ---------------------------------------------

    def record_edit(
        self,
        task_id: str,
        *,
        step_id: str,
        artifact: str,
        before: Any,
        after: Any,
        by_user_id: int,
        kind: str,
        source: str,
    ) -> None:
        """Record one human write — before and after, always both."""
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_step_edits("
                "id, task_id, step_id, artifact, before_value, after_value, by_user_id, kind, "
                "source, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_ulid(),
                    task_id,
                    step_id,
                    artifact,
                    None if before is None else json.dumps(before, ensure_ascii=False),
                    None if after is None else json.dumps(after, ensure_ascii=False),
                    by_user_id,
                    kind,
                    source,
                    now_ts(),
                ),
            )

    def edits(self, task_id: str) -> list[FeatureStepEditRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_step_edits WHERE task_id = ? ORDER BY created_at, id",
                (task_id,),
            ).fetchall()
        return map_rows(rows, FeatureStepEditRow)

    # --- what a step dispatched ------------------------------------------

    def record_dispatches(
        self,
        task_id: str,
        seq: int,
        *,
        step_id: str,
        entries: Sequence[DispatchEntry],
    ) -> None:
        """The subagents one step turn dispatched, written after the turn — audit rows.

        Written once the turn ended, never while a subagent is still out: the list
        is the turn's own record, and appending to it mid-flight would make a
        half-finished step look like a finished one. A turn that dispatched nothing
        writes nothing — the absence of rows is the honest record, and the step's
        own declaration is what says whether a decomposition was expected.
        """
        if not entries:
            return
        timestamp = now_ts()
        with self._db.transaction() as conn:
            for entry in entries:
                conn.execute(
                    "INSERT INTO feature_step_dispatches("
                    "id, task_id, seq, step_id, role, task, status, error, result, truncated, "
                    "waited, waited_ms, slots, started_at, ended_at, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_ulid(),
                        task_id,
                        seq,
                        step_id,
                        entry.role,
                        entry.task,
                        entry.status,
                        entry.error,
                        entry.result,
                        bool_int(entry.truncated),
                        bool_int(entry.waited),
                        entry.waited_ms,
                        entry.slots,
                        entry.started_at,
                        entry.ended_at,
                        timestamp,
                    ),
                )

    def dispatches(self, task_id: str) -> list[FeatureStepDispatchRow]:
        """Every dispatch of the run, in dispatch order.

        ``started_at`` is the order a subagent went out in — not the order the rows
        were written, because concurrent dispatches are written as they return.
        ``now_ts`` is whole seconds, so it ties; ``id`` (the ULID each row was
        minted with) breaks the tie, the same shape the edit log uses.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_step_dispatches WHERE task_id = ? ORDER BY started_at, id",
                (task_id,),
            ).fetchall()
        return map_rows(rows, FeatureStepDispatchRow)


__all__ = [
    "FeatureRunRepo",
    "FeatureRunRow",
    "FeatureStepDispatchRow",
    "FeatureStepEditRow",
    "FeatureStepRunRow",
]
