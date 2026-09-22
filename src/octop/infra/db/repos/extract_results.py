"""What a template produced for a document (design §7.3).

One row per document and template, replaced on a re-run: a template applied
twice to one file is not two answers. The row keeps the provenance the design
asks for — template version, model, parser version, file hash — because those
are what let a later reader tell whether a stored answer still describes
anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_SUCCEEDED, STATUS_FAILED)


@dataclass(frozen=True)
class ExtractResultRow:
    id: str
    document_id: str
    template_id: str
    template_version: int
    status: str
    fields_json: str
    error: str | None
    model: str
    parser_version: str
    content_hash: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ExtractResultRow:
        return cls(
            id=str(r["id"]),
            document_id=str(r["document_id"]),
            template_id=str(r["template_id"]),
            template_version=int(r["template_version"]),
            status=str(r["status"]),
            fields_json=str(r["fields_json"] or "{}"),
            error=str(r["error"]) if r["error"] is not None else None,
            model=str(r["model"] or ""),
            parser_version=str(r["parser_version"] or ""),
            content_hash=str(r["content_hash"] or ""),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )

    @property
    def fields(self) -> dict[str, Any]:
        """The extracted fields, keyed by the template's field names.

        An unreadable payload degrades to ``{}`` like every other JSON column in
        the repos: the status already says whether the extraction worked, and
        refusing to read the row would hide that too.
        """
        try:
            decoded = json.loads(self.fields_json or "{}")
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}


class ExtractResultRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def upsert(
        self,
        *,
        document_id: str,
        template_id: str,
        template_version: int,
        status: str,
        fields_json: str = "{}",
        error: str | None = None,
        model: str = "",
        parser_version: str = "",
        content_hash: str = "",
    ) -> ExtractResultRow:
        """Write this run's outcome, replacing whatever the last run left.

        The row keeps its id across runs, so anything that referred to a result
        (a task, a citation) still refers to the same one after a re-run.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_extract_results("
                "id, document_id, template_id, template_version, status, fields_json, "
                "error, model, parser_version, content_hash, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (document_id, template_id) DO UPDATE SET "
                "template_version = excluded.template_version, status = excluded.status, "
                "fields_json = excluded.fields_json, error = excluded.error, "
                "model = excluded.model, parser_version = excluded.parser_version, "
                "content_hash = excluded.content_hash, updated_at = excluded.updated_at",
                (
                    new_ulid(),
                    document_id,
                    template_id,
                    int(template_version),
                    status,
                    fields_json,
                    error,
                    model,
                    parser_version,
                    content_hash,
                    ts,
                    ts,
                ),
            )
        row = self.get(document_id, template_id)
        if row is None:
            raise RuntimeError(f"extraction result insert failed: {document_id}/{template_id}")
        return row

    def get(self, document_id: str, template_id: str) -> ExtractResultRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM knowledge_extract_results WHERE document_id = ? AND template_id = ?",
                (document_id, template_id),
            ).fetchone()
        return ExtractResultRow.from_row(r) if r else None

    def list_for_document(self, document_id: str) -> list[ExtractResultRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_extract_results WHERE document_id = ? "
                "ORDER BY updated_at DESC, template_id",
                (document_id,),
            ).fetchall()
        return map_rows(rows, ExtractResultRow)

    def list_for_template(
        self, template_id: str, *, status: str | None = None
    ) -> list[ExtractResultRow]:
        if status is None:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM knowledge_extract_results WHERE template_id = ? "
                    "ORDER BY updated_at DESC, document_id",
                    (template_id,),
                ).fetchall()
        else:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM knowledge_extract_results WHERE template_id = ? "
                    "AND status = ? ORDER BY updated_at DESC, document_id",
                    (template_id, status),
                ).fetchall()
        return map_rows(rows, ExtractResultRow)

    def counts_for_template(self, template_id: str) -> dict[str, int]:
        """How many results of each status a template has, in one query."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM knowledge_extract_results "
                "WHERE template_id = ? GROUP BY status",
                (template_id,),
            ).fetchall()
        return {str(r["status"]): int(r["n"]) for r in rows}
