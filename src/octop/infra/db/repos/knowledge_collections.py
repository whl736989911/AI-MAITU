"""Collection registrations, prompt-directed topic results and source-read evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_short_id


@dataclass(frozen=True)
class KnowledgeCollectionRow:
    id: str
    kb_id: str
    owner_user_id: int
    target_document_id: str
    target_path: str
    target_kind: str
    name: str
    prompt: str
    prompt_updated_at: int | None
    generation_state: str
    generation_error: str
    generated_model: str
    generated_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> KnowledgeCollectionRow:
        return cls(
            id=str(row["collection_id"]),
            kb_id=str(row["knowledge_base_id"]),
            owner_user_id=int(row["owner_user_id"]),
            target_document_id=str(row["target_document_id"]),
            target_path=str(row["target_path"]),
            target_kind=str(row["target_kind"]),
            name=str(row["name"] or ""),
            prompt=str(row["prompt"] or ""),
            prompt_updated_at=(
                int(row["prompt_updated_at"]) if row["prompt_updated_at"] is not None else None
            ),
            generation_state=str(row["generation_state"] or "idle"),
            generation_error=str(row["generation_error"] or ""),
            generated_model=str(row["generated_model"] or ""),
            generated_at=int(row["generated_at"]) if row["generated_at"] is not None else None,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


def _load(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class KnowledgeCollectionsRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def list_for_base(self, kb_id: str) -> list[KnowledgeCollectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_collections WHERE knowledge_base_id = ? "
                "ORDER BY target_path, collection_id",
                (kb_id,),
            ).fetchall()
        return [KnowledgeCollectionRow.from_row(row) for row in rows]

    def collections_for_document(self, kb_id: str, doc_id: str) -> list[KnowledgeCollectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT c.* FROM knowledge_collections c "
                "LEFT JOIN knowledge_collection_topics t "
                "ON t.collection_id = c.collection_id AND t.document_id = ? "
                "WHERE c.knowledge_base_id = ? AND (c.target_document_id = ? "
                "OR t.collection_id IS NOT NULL) ORDER BY c.collection_id",
                (doc_id, kb_id, doc_id),
            ).fetchall()
        return [KnowledgeCollectionRow.from_row(row) for row in rows]

    def get(self, collection_id: str) -> KnowledgeCollectionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row) if row else None

    def create(
        self,
        *,
        kb_id: str,
        owner_user_id: int,
        target_document_id: str,
        target_path: str,
        target_kind: str,
        name: str,
        prompt: str,
    ) -> KnowledgeCollectionRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_collections(collection_id, knowledge_base_id, owner_user_id, "
                "target_document_id, target_path, target_kind, name, prompt, prompt_updated_at, "
                "generation_state, generation_error, generated_model, generated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', '', '', NULL, ?, ?) "
                "ON CONFLICT(knowledge_base_id, owner_user_id, target_document_id) DO NOTHING",
                (
                    new_short_id(),
                    kb_id,
                    owner_user_id,
                    target_document_id,
                    target_path,
                    target_kind,
                    name,
                    prompt,
                    ts,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE knowledge_base_id = ? "
                "AND owner_user_id = ? AND target_document_id = ?",
                (kb_id, owner_user_id, target_document_id),
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row)

    def update(
        self,
        collection_id: str,
        *,
        prompt: str | None = None,
        name: str | None = None,
    ) -> KnowledgeCollectionRow | None:
        ts = now_ts()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
            if row is None:
                return None
            prompt_changed = prompt is not None and prompt != str(row["prompt"] or "")
            if prompt_changed:
                # Derived results no longer answer the current prompt.
                conn.execute(
                    "UPDATE knowledge_collections SET prompt = ?, name = ?, "
                    "prompt_updated_at = ?, generation_state = 'idle', "
                    "generation_error = '', updated_at = ? WHERE collection_id = ?",
                    (
                        prompt,
                        name if name is not None else str(row["name"] or ""),
                        ts,
                        ts,
                        collection_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_collections SET name = ?, updated_at = ? "
                    "WHERE collection_id = ?",
                    (name if name is not None else str(row["name"] or ""), ts, collection_id),
                )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row)

    def claim_generation(self, collection_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_collections SET generation_state = 'running', "
                "generation_error = '', updated_at = ? "
                "WHERE collection_id = ? AND generation_state <> 'running'",
                (now_ts(), collection_id),
            )
            return bool(cursor.rowcount)

    def finish_generation(
        self,
        collection_id: str,
        *,
        state: str,
        error: str = "",
        model_ref: str = "",
        generated_at: int | None = None,
    ) -> None:
        ts = generated_at if generated_at is not None else now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_collections SET generation_state = ?, generation_error = ?, "
                "generated_model = ?, generated_at = ?, updated_at = ? WHERE collection_id = ?",
                (state, error, model_ref, ts, now_ts(), collection_id),
            )

    def update_target_path(
        self, collection_id: str, target_path: str
    ) -> KnowledgeCollectionRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_collections SET target_path = ?, updated_at = ? "
                "WHERE collection_id = ?",
                (target_path, now_ts(), collection_id),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row) if row else None

    def delete(self, collection_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collections WHERE collection_id = ?", (collection_id,)
            )

    # ------------------------------------------------------------------
    # Prompt-directed topic entries
    # ------------------------------------------------------------------

    def topics(self, collection_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_collection_topics WHERE collection_id = ? "
                "ORDER BY document_id, source_version",
                (collection_id,),
            ).fetchall()
        return [self._topic_row(row) for row in rows]

    def topics_for_document(self, kb_id: str, document_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT t.* FROM knowledge_collection_topics t "
                "JOIN knowledge_collections c ON c.collection_id = t.collection_id "
                "WHERE c.knowledge_base_id = ? AND t.document_id = ? "
                "ORDER BY t.collection_id",
                (kb_id, document_id),
            ).fetchall()
        return [self._topic_row(row) for row in rows]

    @staticmethod
    def _topic_row(row: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["citations"] = _load(item.pop("citations_json"), [])
        item["evidence_ids"] = _load(item.pop("evidence_ids_json"), [])
        item["corrections"] = _load(item.pop("corrections_json"), [])
        item["conflicts"] = _load(item.pop("conflicts_json"), [])
        item["omissions"] = _load(item.pop("omissions_json"), [])
        item["generated_at"] = (
            int(item["generated_at"]) if item["generated_at"] is not None else None
        )
        return item

    def put_topic(
        self,
        collection_id: str,
        document_id: str,
        source_version: str,
        *,
        status: str,
        content: str,
        summary: str,
        citations: Sequence[Mapping[str, Any]],
        evidence_ids: Sequence[str],
        corrections: Sequence[Mapping[str, Any]] | None = None,
        conflicts: Sequence[str] = (),
        omissions: Sequence[str] = (),
        model_ref: str = "",
        error: str = "",
        generated_at: int | None = None,
    ) -> None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_collection_topics(collection_id, document_id, source_version, "
                "status, content, summary, citations_json, evidence_ids_json, corrections_json, "
                "conflicts_json, omissions_json, model_ref, error, generated_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(collection_id, document_id, source_version) DO UPDATE SET "
                "status=excluded.status, content=excluded.content, summary=excluded.summary, "
                "citations_json=excluded.citations_json, evidence_ids_json=excluded.evidence_ids_json, "
                "corrections_json=excluded.corrections_json, conflicts_json=excluded.conflicts_json, "
                "omissions_json=excluded.omissions_json, model_ref=excluded.model_ref, "
                "error=excluded.error, generated_at=excluded.generated_at, updated_at=excluded.updated_at "
                "WHERE excluded.generated_at IS NOT NULL AND (knowledge_collection_topics.generated_at "
                "IS NULL OR excluded.generated_at >= knowledge_collection_topics.generated_at)",
                (
                    collection_id,
                    document_id,
                    source_version,
                    status,
                    content,
                    summary,
                    _dump(list(citations)),
                    _dump(list(evidence_ids)),
                    _dump(list(corrections or ())),
                    _dump(list(conflicts)),
                    _dump(list(omissions)),
                    model_ref,
                    error,
                    ts if generated_at is None else generated_at,
                    ts,
                ),
            )

    def append_correction(
        self,
        collection_id: str,
        document_id: str,
        source_version: str,
        correction: Mapping[str, Any],
        *,
        limit: int = 50,
    ) -> bool:
        """Merge one evidence-derived correction; concurrent-safe via row transaction."""
        ts = now_ts()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT corrections_json, status FROM knowledge_collection_topics "
                "WHERE collection_id = ? AND document_id = ? AND source_version = ?",
                (collection_id, document_id, source_version),
            ).fetchone()
            if row is None:
                return False
            existing = _load(row["corrections_json"], [])
            key = str(correction.get("evidence_id") or "")
            if any(str(item.get("evidence_id") or "") == key for item in existing):
                return False
            merged = [*existing, dict(correction)][-limit:]
            conn.execute(
                "UPDATE knowledge_collection_topics SET corrections_json = ?, "
                "status = CASE WHEN status IN ('ready', 'partial') THEN 'stale' ELSE status END, "
                "updated_at = ? WHERE collection_id = ? AND document_id = ? AND source_version = ?",
                (_dump(merged), ts, collection_id, document_id, source_version),
            )
            return True

    def mark_topics_stale(self, collection_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_collection_topics SET status = 'stale', updated_at = ? "
                "WHERE collection_id = ? AND status IN ('ready', 'partial')",
                (now_ts(), collection_id),
            )

    def remove_doc_topics(
        self, collection_id: str, document_id: str, *, keep_version: str | None = None
    ) -> None:
        with self._db.transaction() as conn:
            if keep_version is None:
                conn.execute(
                    "DELETE FROM knowledge_collection_topics WHERE collection_id = ? "
                    "AND document_id = ?",
                    (collection_id, document_id),
                )
            else:
                conn.execute(
                    "DELETE FROM knowledge_collection_topics WHERE collection_id = ? "
                    "AND document_id = ? AND source_version <> ?",
                    (collection_id, document_id, keep_version),
                )

    def delete_topics(self, collection_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collection_topics WHERE collection_id = ?", (collection_id,)
            )

    # ------------------------------------------------------------------
    # Source-read evidence accumulation
    # ------------------------------------------------------------------

    def put_read(
        self,
        *,
        evidence_id: str,
        kb_id: str,
        document_id: str,
        source_version: str,
        kind: str,
        status: str,
        locator: Mapping[str, Any],
        text: str,
        summary: str,
        images: Sequence[Mapping[str, Any]],
        read_by: int | None,
    ) -> bool:
        """Insert one evidence row; return False when the identical read already exists."""
        ts = now_ts()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_source_reads(evidence_id, knowledge_base_id, document_id, "
                "source_version, kind, status, locator_json, text, summary, images_json, read_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(document_id, source_version, evidence_id) DO NOTHING",
                (
                    evidence_id,
                    kb_id,
                    document_id,
                    source_version,
                    kind,
                    status,
                    _dump(dict(locator)),
                    text,
                    summary,
                    _dump(list(images)),
                    read_by,
                    ts,
                    ts,
                ),
            )
            return bool(cursor.rowcount)

    def get_read(
        self, document_id: str, source_version: str, evidence_id: str
    ) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_source_reads WHERE document_id = ? "
                "AND source_version = ? AND evidence_id = ?",
                (document_id, source_version, evidence_id),
            ).fetchone()
        return self._read_row(row) if row else None

    def reads_for_documents(self, kb_id: str, document_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_source_reads WHERE knowledge_base_id = ? "
                f"AND document_id IN ({placeholders}) "
                "ORDER BY document_id, created_at, id",
                (kb_id, *document_ids),
            ).fetchall()
        return [self._read_row(row) for row in rows]

    @staticmethod
    def _read_row(row: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["locator"] = _load(item.pop("locator_json"), {})
        item["images"] = _load(item.pop("images_json"), [])
        return item

    def prune_doc_reads(
        self, kb_id: str, document_id: str, *, keep_version: str | None = None
    ) -> None:
        with self._db.transaction() as conn:
            if keep_version is None:
                conn.execute(
                    "DELETE FROM knowledge_source_reads WHERE knowledge_base_id = ? "
                    "AND document_id = ?",
                    (kb_id, document_id),
                )
            else:
                conn.execute(
                    "DELETE FROM knowledge_source_reads WHERE knowledge_base_id = ? "
                    "AND document_id = ? AND source_version <> ?",
                    (kb_id, document_id, keep_version),
                )


__all__ = ["KnowledgeCollectionRow", "KnowledgeCollectionsRepo"]
