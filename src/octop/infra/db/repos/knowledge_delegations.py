"""Knowledge delegation edges and durable access-revocation notifications.

Both tables back the explicit multilevel delegation feature (schema v45). The
repository layer is deliberately thin: resolution, cascade, and emission rules
live in :mod:`octop.infra.knowledge.delegations`, and every write here accepts
a caller-owned ``conn`` so a cascade can share the transaction of the mutation
that caused it.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

EVENT_AUTHORIZATION_REVOKED = "authorization_revoked"
EVENT_DELEGATION_REVOKED = "delegation_revoked"
EVENT_KINDS = (EVENT_AUTHORIZATION_REVOKED, EVENT_DELEGATION_REVOKED)


@dataclass(frozen=True)
class KnowledgeDelegationRow:
    """One delegation edge with a public string ID and internal integer key."""

    id: str
    pk: int
    kb_id: str
    document_id: str
    grantor_user_id: int
    grantee_user_id: int
    can_delegate: bool
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> KnowledgeDelegationRow:
        return cls(
            id=str(r["delegation_id"]),
            pk=int(r["id"]),
            kb_id=str(r["kb_id"]),
            document_id=str(r["document_id"]),
            grantor_user_id=int(r["grantor_user_id"]),
            grantee_user_id=int(r["grantee_user_id"]),
            can_delegate=bool(r["can_delegate"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


@dataclass(frozen=True)
class KnowledgeAccessNotificationRow:
    """One durable inbox row with a public string ID and internal integer key."""

    id: str
    pk: int
    recipient_user_id: int
    kb_id: str
    resource_id: str
    resource_path: str
    event_kind: str
    access_retained: bool
    payload: dict[str, Any]
    read_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> KnowledgeAccessNotificationRow:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        read_at = r["read_at"]
        return cls(
            id=str(r["notification_id"]),
            pk=int(r["id"]),
            recipient_user_id=int(r["recipient_user_id"]),
            kb_id=str(r["kb_id"]),
            resource_id=str(r["resource_id"]),
            resource_path=str(r["resource_path"] or ""),
            event_kind=str(r["event_kind"]),
            access_retained=bool(r["access_retained"]),
            payload=payload if isinstance(payload, dict) else {},
            read_at=None if read_at is None else int(read_at),
            created_at=int(r["created_at"]),
        )


class KnowledgeDelegationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_for_document(
        self, kb_id: str, document_id: str, *, conn: Any | None = None
    ) -> list[KnowledgeDelegationRow]:
        """Every live edge of one document, ordered oldest first."""
        if conn is not None:
            rows = conn.execute(
                "SELECT * FROM knowledge_delegations "
                "WHERE kb_id = ? AND document_id = ? ORDER BY id",
                (kb_id, document_id),
            ).fetchall()
            return map_rows(rows, KnowledgeDelegationRow)
        with self._db.connect() as owned:
            rows = owned.execute(
                "SELECT * FROM knowledge_delegations "
                "WHERE kb_id = ? AND document_id = ? ORDER BY id",
                (kb_id, document_id),
            ).fetchall()
        return map_rows(rows, KnowledgeDelegationRow)

    def list_for_kb(self, kb_id: str, *, conn: Any | None = None) -> list[KnowledgeDelegationRow]:
        if conn is not None:
            rows = conn.execute(
                "SELECT * FROM knowledge_delegations WHERE kb_id = ? ORDER BY id",
                (kb_id,),
            ).fetchall()
            return map_rows(rows, KnowledgeDelegationRow)
        with self._db.connect() as owned:
            rows = owned.execute(
                "SELECT * FROM knowledge_delegations WHERE kb_id = ? ORDER BY id",
                (kb_id,),
            ).fetchall()
        return map_rows(rows, KnowledgeDelegationRow)

    def get_by_delegation_id(
        self, delegation_id: str, *, conn: Any | None = None
    ) -> KnowledgeDelegationRow | None:
        sql = "SELECT * FROM knowledge_delegations WHERE delegation_id = ?"
        if conn is not None:
            row = conn.execute(sql, (delegation_id,)).fetchone()
        else:
            with self._db.connect() as owned:
                row = owned.execute(sql, (delegation_id,)).fetchone()
        return KnowledgeDelegationRow.from_row(row) if row else None

    # ------------------------------------------------------------------
    # Writes (caller-owned transactions)
    # ------------------------------------------------------------------

    def upsert(
        self,
        *,
        kb_id: str,
        document_id: str,
        grantor_user_id: int,
        grantee_user_id: int,
        can_delegate: bool,
        conn: Any,
    ) -> KnowledgeDelegationRow:
        """Create or update one edge inside the caller's transaction.

        The ``(document_id, grantor, grantee)`` uniqueness is the graph's
        integrity: one issuer holds exactly one edge per recipient and
        document, so the upsert can only ever touch the issuer's own grant —
        another issuer's edge to the same recipient is a separate row and is
        never replaced here.
        """
        ts = now_ts()
        conn.execute(
            "INSERT INTO knowledge_delegations("
            "delegation_id, kb_id, document_id, grantor_user_id, grantee_user_id, "
            "can_delegate, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(document_id, grantor_user_id, grantee_user_id) DO UPDATE SET "
            "can_delegate = excluded.can_delegate, updated_at = excluded.updated_at",
            (
                new_ulid(),
                kb_id,
                document_id,
                grantor_user_id,
                grantee_user_id,
                bool_int(can_delegate),
                ts,
                ts,
            ),
        )
        fresh = conn.execute(
            "SELECT * FROM knowledge_delegations WHERE document_id = ? "
            "AND grantor_user_id = ? AND grantee_user_id = ?",
            (document_id, grantor_user_id, grantee_user_id),
        ).fetchone()
        if fresh is None:  # pragma: no cover - the upsert above guarantees the row
            raise RuntimeError("knowledge delegation upsert failed")
        return KnowledgeDelegationRow.from_row(fresh)

    def delete(self, delegation_id: str, *, conn: Any) -> None:
        conn.execute(
            "DELETE FROM knowledge_delegations WHERE delegation_id = ?",
            (delegation_id,),
        )

    def delete_for_documents(self, document_ids: Collection[str], *, conn: Any) -> None:
        if not document_ids:
            return
        placeholders = ", ".join("?" * len(document_ids))
        conn.execute(
            f"DELETE FROM knowledge_delegations WHERE document_id IN ({placeholders})",
            tuple(document_ids),
        )


class KnowledgeAccessNotificationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def insert(
        self,
        *,
        recipient_user_id: int,
        kb_id: str,
        resource_id: str,
        resource_path: str,
        event_kind: str,
        access_retained: bool,
        payload: dict[str, Any],
        conn: Any,
    ) -> str:
        """Persist one inbox row inside the caller's transaction.

        Called only from mutation paths after their cascade resolution, so the
        row commits exactly when the revocation does: a rejected or rolled-back
        mutation never leaves a notification behind.
        """
        if event_kind not in EVENT_KINDS:
            raise ValueError(f"unknown knowledge notification event_kind {event_kind!r}")
        notification_id = new_ulid()
        conn.execute(
            "INSERT INTO knowledge_access_notifications("
            "notification_id, recipient_user_id, kb_id, resource_id, resource_path, "
            "event_kind, access_retained, payload_json, read_at, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                notification_id,
                recipient_user_id,
                kb_id,
                resource_id,
                resource_path,
                event_kind,
                bool_int(access_retained),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now_ts(),
            ),
        )
        return notification_id

    def list_for_recipient(
        self,
        recipient_user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[KnowledgeAccessNotificationRow]:
        sql = "SELECT * FROM knowledge_access_notifications WHERE recipient_user_id = ?"
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        with self._db.connect() as conn:
            rows = conn.execute(sql, (recipient_user_id, max(1, int(limit)))).fetchall()
        return map_rows(rows, KnowledgeAccessNotificationRow)

    def count_unread(self, recipient_user_id: int) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_access_notifications "
                "WHERE recipient_user_id = ? AND read_at IS NULL",
                (recipient_user_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def get(self, notification_id: str) -> KnowledgeAccessNotificationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_access_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return KnowledgeAccessNotificationRow.from_row(row) if row else None

    def mark_read(self, notification_id: str, *, conn: Any | None = None) -> None:
        """Mark one row read; re-marking an already-read row is idempotent."""
        sql = (
            "UPDATE knowledge_access_notifications SET read_at = ? "
            "WHERE notification_id = ? AND read_at IS NULL"
        )
        if conn is not None:
            conn.execute(sql, (now_ts(), notification_id))
            return
        with self._db.connect() as owned:
            owned.execute(sql, (now_ts(), notification_id))
