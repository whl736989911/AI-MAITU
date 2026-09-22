"""Data-source rows — the ingest sources attached to a knowledge base.

A data source has no ACL of its own: it is visible exactly when its knowledge
base is (``octop.infra.sharing``), so this repo never filters by user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    bool_int,
    map_rows,
    now_ts,
    partial_updates,
)
from octop.infra.utils.ulid import new_short_id

KIND_UPLOAD = "upload"
KIND_URL = "url"
KIND_CONNECTOR = "connector"
KIND_LOCAL = "local"
KIND_SMB = "smb"
KIND_NFS = "nfs"

KINDS = (KIND_UPLOAD, KIND_URL, KIND_CONNECTOR, KIND_LOCAL, KIND_SMB, KIND_NFS)
"""Every stored kind. ``local`` / ``smb`` / ``nfs`` name a folder; the rest name content."""

SYNC_IDLE = "idle"
SYNC_RUNNING = "running"
SYNC_OK = "ok"
SYNC_FAILED = "failed"

CONNECTION_UNKNOWN = "unknown"
CONNECTION_OK = "ok"
CONNECTION_FAILED = "failed"
CONNECTIONS = (CONNECTION_UNKNOWN, CONNECTION_OK, CONNECTION_FAILED)


@dataclass(frozen=True)
class DataSourceRow:
    id: str
    knowledge_base_id: str
    name: str
    kind: str
    server: str
    share: str
    root_path: str
    username: str
    has_credentials: bool
    read_only: bool
    include_globs: str
    exclude_globs: str
    scan_interval_seconds: int
    connection_status: str
    connection_error: str | None
    last_scan_at: int | None
    last_scan_ok_at: int | None
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
            server=str(r["server"] or ""),
            share=str(r["share"] or ""),
            root_path=str(r["root_path"] or ""),
            username=str(r["username"] or ""),
            # The secret itself is not on this row: it stays in the repository
            # so no serializer can reach it, and only its existence travels here.
            has_credentials=r["credentials_enc"] is not None,
            read_only=bool(int(r["read_only"])),
            include_globs=str(r["include_globs"] or ""),
            exclude_globs=str(r["exclude_globs"] or ""),
            scan_interval_seconds=int(r["scan_interval_seconds"] or 0),
            connection_status=str(r["connection_status"] or CONNECTION_UNKNOWN),
            connection_error=(
                str(r["connection_error"]) if r["connection_error"] is not None else None
            ),
            last_scan_at=(int(r["last_scan_at"]) if r["last_scan_at"] is not None else None),
            last_scan_ok_at=(
                int(r["last_scan_ok_at"]) if r["last_scan_ok_at"] is not None else None
            ),
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
        server: str = "",
        share: str = "",
        root_path: str = "",
        username: str = "",
        credentials_enc: bytes | None = None,
        read_only: bool = True,
        include_globs: str = "",
        exclude_globs: str = "",
        scan_interval_seconds: int = 0,
    ) -> DataSourceRow:
        """Insert a source, secret included, so it is never half-configured.

        The encrypted secret is a parameter rather than a follow-up write: a
        source that exists without the credential it was created with would be
        a row the next scan cannot use.
        """
        ds_id = self._allocate_id()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO data_sources("
                "id, knowledge_base_id, name, kind, config_json, created_by, sync_status, "
                "sync_error, last_synced_at, created_at, updated_at, server, share, root_path, "
                "username, credentials_enc, read_only, include_globs, exclude_globs, "
                "scan_interval_seconds, connection_status, connection_error, "
                "last_scan_at, last_scan_ok_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, NULL, NULL, NULL)",
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
                    server,
                    share,
                    root_path,
                    username,
                    credentials_enc,
                    bool_int(read_only),
                    include_globs,
                    exclude_globs,
                    scan_interval_seconds,
                    CONNECTION_UNKNOWN,
                ),
            )
        row = self.get(ds_id)
        if row is None:
            raise RuntimeError(f"data source insert failed: {ds_id}")
        return row

    def get_credentials(self, ds_id: str) -> bytes | None:
        """The stored encrypted secret for one source.

        Separate from :class:`DataSourceRow` on purpose: the blob stays in the
        repository so no payload builder can serialize it by accident.
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT credentials_enc FROM data_sources WHERE id = ?", (ds_id,)
            ).fetchone()
        if row is None or row["credentials_enc"] is None:
            return None
        return bytes(row["credentials_enc"])

    def set_credentials(self, ds_id: str, blob: bytes | None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE data_sources SET credentials_enc = ?, updated_at = ? WHERE id = ?",
                (blob, now_ts(), ds_id),
            )

    def update_source_fields(
        self,
        ds_id: str,
        *,
        name: str | None = None,
        server: str | None = None,
        share: str | None = None,
        root_path: str | None = None,
        username: str | None = None,
        read_only: bool | None = None,
        include_globs: str | None = None,
        exclude_globs: str | None = None,
        scan_interval_seconds: int | None = None,
    ) -> None:
        """Patch a folder source's connection settings.

        ``credentials_enc`` is not here: it is written by
        :meth:`set_credentials`, which the service calls with a freshly encrypted
        blob so an empty secret and "leave the secret alone" stay distinct.
        """
        fields, params = partial_updates(
            [
                ("name", name),
                ("server", server),
                ("share", share),
                ("root_path", root_path),
                ("username", username),
                ("read_only", bool_int(read_only) if read_only is not None else None),
                ("include_globs", include_globs),
                ("exclude_globs", exclude_globs),
                ("scan_interval_seconds", scan_interval_seconds),
            ]
        )
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(ds_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE data_sources SET {', '.join(fields)} WHERE id = ?", params)

    def set_connection(self, ds_id: str, *, status: str, error: str | None = None) -> None:
        """Record what a connection test saw.

        ``connection_error`` is cleared on success so a stale failure reason does
        not outlive the failure.
        """
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE data_sources SET connection_status = ?, connection_error = ?, "
                "updated_at = ? WHERE id = ?",
                (status, error, now_ts(), ds_id),
            )

    def mark_scan(self, ds_id: str, *, at: int, ok: bool) -> None:
        """Record that a scan ran, and whether it completed.

        Only a completed scan moves ``last_scan_ok_at``: a scan that aborted
        halfway must not read as the newest consistent view of the source.
        """
        ts = now_ts()
        fields = ["last_scan_at = ?", "updated_at = ?"]
        params: list[object] = [at, ts]
        if ok:
            fields.insert(1, "last_scan_ok_at = ?")
            params.insert(1, at)
        params.append(ds_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE data_sources SET {', '.join(fields)} WHERE id = ?", params)

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

    def update_config(self, ds_id: str, config: dict[str, Any]) -> None:
        """Replace ``config_json`` — sync records the document it wrote here."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE data_sources SET config_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                    now_ts(),
                    ds_id,
                ),
            )

    def delete(self, ds_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM data_sources WHERE id = ?", (ds_id,))
