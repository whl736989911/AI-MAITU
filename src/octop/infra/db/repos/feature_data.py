"""Per-feature business records, audit, attachments and reminders."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return (
        [dict(item) for item in parsed if isinstance(item, Mapping)]
        if isinstance(parsed, list)
        else []
    )


@dataclass(frozen=True)
class FeatureDataRecordRow:
    id: str
    agent_id: str
    object_key: str
    title: str
    values: dict[str, Any]
    visibility: str
    revision: int
    created_by: int
    created_at: int
    updated_at: int
    deleted_at: int | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureDataRecordRow:
        deleted_at = row["deleted_at"]
        return cls(
            id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            object_key=str(row["object_key"]),
            title=str(row["title"]),
            values=_json_object(row["values_json"]),
            visibility=str(row["visibility"]),
            revision=int(row["revision"]),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            deleted_at=int(deleted_at) if deleted_at else None,
        )


@dataclass(frozen=True)
class FeatureDataChangeRow:
    id: str
    record_id: str
    agent_id: str
    user_id: int
    kind: str
    summary: str
    items: list[dict[str, Any]]
    created_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureDataChangeRow:
        return cls(
            id=str(row["id"]),
            record_id=str(row["record_id"]),
            agent_id=str(row["agent_id"]),
            user_id=int(row["user_id"]),
            kind=str(row["kind"]),
            summary=str(row["summary"]),
            items=_json_list(row["items_json"]),
            created_at=int(row["created_at"]),
        )


@dataclass(frozen=True)
class FeatureDataFileRow:
    id: str
    record_id: str
    agent_id: str
    field_key: str
    version: int
    filename: str
    content_type: str
    size: int
    sha256: str
    storage_path: str
    note: str
    uploaded_by: int
    created_at: int
    current: bool

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureDataFileRow:
        return cls(
            id=str(row["id"]),
            record_id=str(row["record_id"]),
            agent_id=str(row["agent_id"]),
            field_key=str(row["field_key"]),
            version=int(row["version"]),
            filename=str(row["filename"]),
            content_type=str(row["content_type"]),
            size=int(row["size"]),
            sha256=str(row["sha256"]),
            storage_path=str(row["storage_path"]),
            note=str(row["note"]),
            uploaded_by=int(row["uploaded_by"]),
            created_at=int(row["created_at"]),
            current=bool(row["is_current"]),
        )


@dataclass(frozen=True)
class FeatureDataReminderRow:
    id: str
    agent_id: str
    record_id: str
    object_key: str
    field_key: str
    due_at: str
    title: str
    status: str
    next_notify_at: int | None
    last_notified_at: int | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeatureDataReminderRow:
        return cls(
            id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            record_id=str(row["record_id"]),
            object_key=str(row["object_key"]),
            field_key=str(row["field_key"]),
            due_at=str(row["due_at"]),
            title=str(row["title"]),
            status=str(row["status"]),
            next_notify_at=int(row["next_notify_at"]) if row["next_notify_at"] else None,
            last_notified_at=int(row["last_notified_at"]) if row["last_notified_at"] else None,
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FeatureDataNotificationRow:
    id: str
    agent_id: str
    user_id: int
    reminder_id: str
    cycle_key: str
    title: str
    body: str
    channel: str
    read_at: int | None
    created_at: int


class FeatureDataRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # records ---------------------------------------------------------------
    def create_record(
        self,
        *,
        agent_id: str,
        object_key: str,
        title: str,
        values: Mapping[str, Any],
        visibility: str,
        created_by: int,
    ) -> FeatureDataRecordRow:
        record_id = new_ulid()
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_data_records("
                "id, agent_id, object_key, title, values_json, visibility, revision, "
                "created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    agent_id,
                    object_key,
                    title,
                    json.dumps(dict(values), ensure_ascii=False),
                    visibility,
                    1,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        row = self.get_record(record_id)
        assert row is not None
        return row

    def get_record(self, record_id: str) -> FeatureDataRecordRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_data_records WHERE id = ?", (record_id,)
            ).fetchone()
        return FeatureDataRecordRow.from_row(row) if row else None

    def list_records(
        self,
        *,
        agent_id: str,
        object_key: str,
        user_id: int,
        include_private_of: int | None = None,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[FeatureDataRecordRow], int]:
        where = [
            "agent_id = ?",
            "object_key = ?",
            "deleted_at IS NULL",
            "(visibility = 'feature' OR created_by = ?)",
        ]
        params: list[Any] = [agent_id, object_key, user_id]
        if include_private_of is not None:
            where[-1] = "(visibility = 'feature' OR created_by = ? OR created_by = ?)"
            params.append(include_private_of)
        if query.strip():
            where.append("(title LIKE ? OR values_json LIKE ?)")
            needle = f"%{query.strip()}%"
            params.extend([needle, needle])
        clause = " AND ".join(where)
        with self._db.connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM feature_data_records WHERE {clause}", params
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"SELECT * FROM feature_data_records WHERE {clause} "
                "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, max(1, min(limit, 200)), max(0, offset)],
            ).fetchall()
        return [FeatureDataRecordRow.from_row(row) for row in rows], total

    def update_record(
        self,
        *,
        record_id: str,
        values: Mapping[str, Any],
        title: str,
        expected_revision: int,
    ) -> FeatureDataRecordRow | None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_data_records SET values_json = ?, title = ?, "
                "revision = revision + 1, updated_at = ? "
                "WHERE id = ? AND revision = ? AND deleted_at IS NULL",
                (
                    json.dumps(dict(values), ensure_ascii=False),
                    title,
                    now_ts(),
                    record_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_record(record_id)

    def publish_record(self, record_id: str) -> FeatureDataRecordRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_data_records SET visibility = 'feature', revision = revision + 1, "
                "updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now_ts(), record_id),
            )
        return self.get_record(record_id)

    def soft_delete(self, record_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_data_records SET deleted_at = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now_ts(), now_ts(), record_id),
            )
        return bool(cursor.rowcount == 1)

    # history ---------------------------------------------------------------
    def add_change(
        self,
        *,
        record_id: str,
        agent_id: str,
        user_id: int,
        kind: str,
        summary: str,
        items: Sequence[Mapping[str, Any]] = (),
    ) -> FeatureDataChangeRow:
        change_id = new_ulid()
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feature_data_record_changes("
                "id, record_id, agent_id, user_id, kind, summary, items_json, created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    change_id,
                    record_id,
                    agent_id,
                    user_id,
                    kind,
                    summary,
                    json.dumps([dict(item) for item in items], ensure_ascii=False),
                    timestamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM feature_data_record_changes WHERE id = ?", (change_id,)
            ).fetchone()
        return FeatureDataChangeRow.from_row(row)

    def list_changes(self, record_id: str, limit: int = 50) -> list[FeatureDataChangeRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_data_record_changes WHERE record_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (record_id, max(1, min(limit, 200))),
            ).fetchall()
        return [FeatureDataChangeRow.from_row(row) for row in rows]

    # files -----------------------------------------------------------------
    def add_file(
        self,
        *,
        record_id: str,
        agent_id: str,
        field_key: str,
        filename: str,
        content_type: str,
        size: int,
        sha256: str,
        storage_path: str,
        note: str,
        uploaded_by: int,
    ) -> FeatureDataFileRow:
        file_id = new_ulid()
        timestamp = now_ts()
        with self._db.transaction() as conn:
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM feature_data_files "
                    "WHERE record_id = ? AND field_key = ?",
                    (record_id, field_key),
                ).fetchone()[0]
            )
            conn.execute(
                "UPDATE feature_data_files SET is_current = 0 WHERE record_id = ? AND field_key = ?",
                (record_id, field_key),
            )
            conn.execute(
                "INSERT INTO feature_data_files("
                "id, record_id, agent_id, field_key, version, filename, content_type, "
                "size, sha256, storage_path, note, uploaded_by, created_at, is_current"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    file_id,
                    record_id,
                    agent_id,
                    field_key,
                    version,
                    filename,
                    content_type,
                    size,
                    sha256,
                    storage_path,
                    note,
                    uploaded_by,
                    timestamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM feature_data_files WHERE id = ?", (file_id,)
            ).fetchone()
        return FeatureDataFileRow.from_row(row)

    def get_file(self, file_id: str) -> FeatureDataFileRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_data_files WHERE id = ?", (file_id,)
            ).fetchone()
        return FeatureDataFileRow.from_row(row) if row else None

    def list_files(self, record_id: str) -> list[FeatureDataFileRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_data_files WHERE record_id = ? "
                "ORDER BY field_key, version DESC",
                (record_id,),
            ).fetchall()
        return [FeatureDataFileRow.from_row(row) for row in rows]

    # reminders -------------------------------------------------------------
    def upsert_reminder(
        self,
        *,
        agent_id: str,
        record_id: str,
        object_key: str,
        field_key: str,
        due_at: str,
        title: str,
        next_notify_at: int | None,
        created_by: int,
    ) -> FeatureDataReminderRow:
        timestamp = now_ts()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM feature_data_reminders "
                "WHERE record_id = ? AND field_key = ? AND due_at = ?",
                (record_id, field_key, due_at),
            ).fetchone()
            if row:
                reminder_id = str(row["id"])
                conn.execute(
                    "UPDATE feature_data_reminders SET title = ?, next_notify_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (title, next_notify_at, timestamp, reminder_id),
                )
            else:
                reminder_id = new_ulid()
                conn.execute(
                    "INSERT INTO feature_data_reminders("
                    "id, agent_id, record_id, object_key, field_key, due_at, title, status, "
                    "next_notify_at, created_by, created_at, updated_at"
                    ") VALUES(?,?,?,?,?,?,?,'open',?,?,?,?)",
                    (
                        reminder_id,
                        agent_id,
                        record_id,
                        object_key,
                        field_key,
                        due_at,
                        title,
                        next_notify_at,
                        created_by,
                        timestamp,
                        timestamp,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM feature_data_reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return FeatureDataReminderRow.from_row(row)

    def get_reminder(self, reminder_id: str) -> FeatureDataReminderRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_data_reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return FeatureDataReminderRow.from_row(row) if row else None

    def dismiss_reminders_for_field(
        self, record_id: str, field_key: str, *, keep_due_at: str | None = None
    ) -> None:
        """Close old schedules without deleting notification/audit history."""
        with self._db.transaction() as conn:
            if keep_due_at is None:
                conn.execute(
                    "UPDATE feature_data_reminders SET status = 'dismissed', "
                    "next_notify_at = NULL, updated_at = ? "
                    "WHERE record_id = ? AND field_key = ? AND status = 'open'",
                    (now_ts(), record_id, field_key),
                )
            else:
                conn.execute(
                    "UPDATE feature_data_reminders SET status = 'dismissed', "
                    "next_notify_at = NULL, updated_at = ? "
                    "WHERE record_id = ? AND field_key = ? AND due_at <> ? AND status = 'open'",
                    (now_ts(), record_id, field_key, keep_due_at),
                )

    def list_reminders(self, record_id: str) -> list[FeatureDataReminderRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_data_reminders WHERE record_id = ? ORDER BY due_at",
                (record_id,),
            ).fetchall()
        return [FeatureDataReminderRow.from_row(row) for row in rows]

    def due_reminders(self, now: int, limit: int = 200) -> list[FeatureDataReminderRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_data_reminders WHERE status = 'open' "
                "AND next_notify_at IS NOT NULL AND next_notify_at <= ? "
                "ORDER BY next_notify_at LIMIT ?",
                (now, max(1, min(limit, 1000))),
            ).fetchall()
        return [FeatureDataReminderRow.from_row(row) for row in rows]

    def set_reminder_schedule(
        self, reminder_id: str, *, next_notify_at: int | None, last_notified_at: int | None
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feature_data_reminders SET next_notify_at = ?, last_notified_at = ?, "
                "updated_at = ? WHERE id = ?",
                (next_notify_at, last_notified_at, now_ts(), reminder_id),
            )

    def complete_reminder(self, reminder_id: str, *, status: str = "done") -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_data_reminders SET status = ?, next_notify_at = NULL, updated_at = ? "
                "WHERE id = ?",
                (status, now_ts(), reminder_id),
            )
        return bool(cursor.rowcount == 1)

    # notifications ---------------------------------------------------------
    def add_notification(
        self,
        *,
        agent_id: str,
        user_id: int,
        reminder_id: str,
        cycle_key: str,
        title: str,
        body: str,
        channel: str = "in_app",
    ) -> bool:
        notification_id = new_ulid()
        with self._db.transaction() as conn:
            duplicate = conn.execute(
                "SELECT 1 FROM feature_data_notifications "
                "WHERE reminder_id = ? AND user_id = ? AND cycle_key = ?",
                (reminder_id, user_id, cycle_key),
            ).fetchone()
            if duplicate is not None:
                return False
            conn.execute(
                "INSERT INTO feature_data_notifications("
                "id, agent_id, user_id, reminder_id, cycle_key, title, body, channel, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    notification_id,
                    agent_id,
                    user_id,
                    reminder_id,
                    cycle_key,
                    title,
                    body,
                    channel,
                    now_ts(),
                ),
            )
        return True

    def list_notifications(
        self,
        *,
        agent_id: str,
        user_id: int,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[FeatureDataNotificationRow]:
        clause = "WHERE agent_id = ? AND user_id = ?" + (
            " AND read_at IS NULL" if unread_only else ""
        )
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM feature_data_notifications {clause} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (agent_id, user_id, max(1, min(limit, 500))),
            ).fetchall()
        return [
            FeatureDataNotificationRow(
                id=str(row["id"]),
                agent_id=str(row["agent_id"]),
                user_id=int(row["user_id"]),
                reminder_id=str(row["reminder_id"]),
                cycle_key=str(row["cycle_key"]),
                title=str(row["title"]),
                body=str(row["body"]),
                channel=str(row["channel"]),
                read_at=int(row["read_at"]) if row["read_at"] else None,
                created_at=int(row["created_at"]),
            )
            for row in rows
        ]

    def mark_notification_read(self, notification_id: str, user_id: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE feature_data_notifications SET read_at = ? "
                "WHERE id = ? AND user_id = ? AND read_at IS NULL",
                (now_ts(), notification_id, user_id),
            )
        return bool(cursor.rowcount == 1)

    def delete_for_feature(self, agent_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM feature_data_records WHERE agent_id = ?", (agent_id,))
