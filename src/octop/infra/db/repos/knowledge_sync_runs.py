"""One row per scan of a folder source (design §8.4).

A scan is a task an administrator wants to look back at: what it saw, what it
did, and — when it failed — why. The per-file outcome stays on the document row,
so a run is a summary and not the only trace of what happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"

RUN_RUNNING = "running"
RUN_OK = "ok"
RUN_FAILED = "failed"

# The counters a finished run records, in the order the columns are updated.
_COUNT_FIELDS = ("scanned", "added", "updated", "removed", "deferred", "failed")


@dataclass(frozen=True)
class SyncRunRow:
    id: str
    data_source_id: str
    trigger: str
    status: str
    started_at: int
    finished_at: int | None
    scanned: int
    added: int
    updated: int
    removed: int
    deferred: int
    failed: int
    error: str | None

    @classmethod
    def from_row(cls, r: DbRow) -> SyncRunRow:
        return cls(
            id=str(r["id"]),
            data_source_id=str(r["data_source_id"]),
            trigger=str(r["trigger"]),
            status=str(r["status"]),
            started_at=int(r["started_at"]),
            finished_at=int(r["finished_at"]) if r["finished_at"] is not None else None,
            scanned=int(r["scanned"]),
            added=int(r["added"]),
            updated=int(r["updated"]),
            removed=int(r["removed"]),
            deferred=int(r["deferred"]),
            failed=int(r["failed"]),
            error=str(r["error"]) if r["error"] is not None else None,
        )


class KnowledgeSyncRunRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def start(self, *, data_source_id: str, trigger: str) -> SyncRunRow:
        """Open a run before any work happens.

        Written first on purpose: a scan that dies mid-way — a crashed worker, a
        killed process — leaves a ``running`` row, which is the only evidence
        that it was interrupted rather than never asked for.
        """
        run_id = new_ulid()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_sync_runs("
                "id, data_source_id, trigger, status, started_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (run_id, data_source_id, trigger, RUN_RUNNING, now_ts()),
            )
        row = self.get(run_id)
        if row is None:
            raise RuntimeError(f"sync run insert failed: {run_id}")
        return row

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        counts: dict[str, int] | None = None,
        error: str | None = None,
    ) -> SyncRunRow:
        """Close a run with its outcome."""
        values = counts or {}
        fields = ["status = ?", "finished_at = ?", "error = ?"]
        params: list[object] = [status, now_ts(), error]
        for field in _COUNT_FIELDS:
            fields.append(f"{field} = ?")
            params.append(int(values.get(field, 0)))
        params.append(run_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE knowledge_sync_runs SET {', '.join(fields)} WHERE id = ?", params)
        row = self.get(run_id)
        if row is None:
            raise RuntimeError(f"sync run {run_id!r} disappeared")
        return row

    def get(self, run_id: str) -> SyncRunRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM knowledge_sync_runs WHERE id = ?", (run_id,)).fetchone()
        return SyncRunRow.from_row(r) if r else None

    def list_for_source(self, data_source_id: str, *, limit: int = 20) -> list[SyncRunRow]:
        """A source's runs, newest first."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_sync_runs WHERE data_source_id = ? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (data_source_id, int(limit)),
            ).fetchall()
        return map_rows(rows, SyncRunRow)
