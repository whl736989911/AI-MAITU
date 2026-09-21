"""Data-source rows — the ingest sources attached to a knowledge base.

A data source has no ACL of its own: it is visible exactly when its knowledge
base is (``octop.infra.sharing``), so this repo never filters by user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_short_id

KIND_UPLOAD = "upload"
KIND_URL = "url"
KIND_CONNECTOR = "connector"
KINDS = (KIND_UPLOAD, KIND_URL, KIND_CONNECTOR)

SYNC_IDLE = "idle"
SYNC_RUNNING = "running"
SYNC_OK = "ok"
SYNC_FAILED = "failed"


@dataclass(frozen=True)
class DataSourceRow:
    id: str
    knowledge_base_id: str
    name: str
    kind: str
    config_json: str
    created_by: int | None
    sync_status: str
    sync_error: str | None
    last_synced_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> DataSourceRow:
        return cls(
            id=str(r["id"]),
            knowledge_base_id=str(r["knowledge_base_id"]),
            name=str(r["name"]),
            kind=str(r["kind"]),
            config_json=str(r["config_json"] or "{}"),
            created_by=int(r["created_by"]) if r["created_by"] is not None else None,
            sync_status=str(r["sync_status"]),
            sync_error=str(r["sync_error"]) if r["sync_error"] is not None else None,
            last_synced_at=(int(r["last_synced_at"]) if r["last_synced_at"] is not None else None),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )

    @property
    def config(self) -> dict[str, Any]:
        """Parsed ``config_json``; an unreadable payload degrades to ``{}``."""
        try:
            decoded = json.loads(self.config_json or "{}")
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}


class DataSourceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def _allocate_id(self) -> str:
        for _ in range(16):
            ds_id = new_short_id()
            if self.get(ds_id) is None:
                return ds_id
        raise RuntimeError("failed to allocate unique data source id")

    def create(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        kind: str,
        config: dict[str, Any] | None = None,
        created_by: int | None = None,
    ) -> DataSourceRow:
        ds_id = self._allocate_id()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO data_sources("
                "id, knowledge_base_id, name, kind, config_json, created_by, sync_status, "
                "sync_error, last_synced_at, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    ds_id,
                    knowledge_base_id,
                    name,
                    kind,
                    json.dumps(config or {}, ensure_ascii=False, separators=(",", ":")),
                    created_by,
                    SYNC_IDLE,
                    ts,
                    ts,
                ),
            )
        row = self.get(ds_id)
        if row is None:
            raise RuntimeError(f"data source insert failed: {ds_id}")
        return row

    def get(self, ds_id: str) -> DataSourceRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM data_sources WHERE id = ?", (ds_id,)).fetchone()
        return DataSourceRow.from_row(r) if r else None

    def list_for_base(self, kb_id: str) -> list[DataSourceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM data_sources WHERE knowledge_base_id = ? ORDER BY name, id",
                (kb_id,),
            ).fetchall()
        return map_rows(rows, DataSourceRow)

    def mark_sync(
        self,
        ds_id: str,
        *,
        status: str,
        error: str | None = None,
        synced_at: int | None = None,
    ) -> None:
        """Record the outcome of one sync attempt.

        ``last_synced_at`` is only written when the caller passes *synced_at*,
        so a failed attempt never looks like a successful one.
        """
        ts = now_ts()
        fields = ["sync_status = ?", "sync_error = ?", "updated_at = ?"]
        params: list[object] = [status, error, ts]
        if synced_at is not None:
            fields.append("last_synced_at = ?")
            params.append(synced_at)
        params.append(ds_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE data_sources SET {', '.join(fields)} WHERE id = ?", params)

    def delete(self, ds_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM data_sources WHERE id = ?", (ds_id,))
