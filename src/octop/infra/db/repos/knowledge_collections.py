"""Owner-scoped collection metadata and source-located entries."""

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
    keywords: tuple[str, ...]
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> KnowledgeCollectionRow:
        try:
            keywords = json.loads(row["keywords_json"] or "[]")
        except (TypeError, ValueError):
            keywords = []
        return cls(
            id=str(row["collection_id"]),
            kb_id=str(row["knowledge_base_id"]),
            owner_user_id=int(row["owner_user_id"]),
            target_document_id=str(row["target_document_id"]),
            target_path=str(row["target_path"]),
            target_kind=str(row["target_kind"]),
            name=str(row["name"] or ""),
            keywords=tuple(str(v) for v in keywords),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class KnowledgeCollectionsRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_for_owner(self, kb_id: str, owner_user_id: int) -> list[KnowledgeCollectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_collections WHERE knowledge_base_id = ? AND owner_user_id = ? ORDER BY target_path, collection_id",
                (kb_id, owner_user_id),
            ).fetchall()
        return [KnowledgeCollectionRow.from_row(row) for row in rows]

    def get(self, collection_id: str, owner_user_id: int) -> KnowledgeCollectionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ? AND owner_user_id = ?",
                (collection_id, owner_user_id),
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
        name: str = "",
    ) -> KnowledgeCollectionRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_collections(collection_id, knowledge_base_id, owner_user_id, target_document_id, target_path, target_kind, name, keywords_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?) ON CONFLICT(knowledge_base_id, owner_user_id, target_document_id) DO NOTHING",
                (
                    new_short_id(),
                    kb_id,
                    owner_user_id,
                    target_document_id,
                    target_path,
                    target_kind,
                    name,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE knowledge_base_id = ? AND owner_user_id = ? AND target_document_id = ?",
                (kb_id, owner_user_id, target_document_id),
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row)

    def update(
        self,
        collection_id: str,
        owner_user_id: int,
        *,
        keywords: Sequence[str] | None = None,
        name: str | None = None,
    ) -> KnowledgeCollectionRow | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ? AND owner_user_id = ?",
                (collection_id, owner_user_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE knowledge_collections SET keywords_json = ?, name = ?, updated_at = ? WHERE collection_id = ? AND owner_user_id = ?",
                (
                    json.dumps(
                        list(keywords)
                        if keywords is not None
                        else json.loads(row["keywords_json"] or "[]"),
                        ensure_ascii=False,
                    ),
                    name if name is not None else row["name"],
                    now_ts(),
                    collection_id,
                    owner_user_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row)

    def entries(self, collection_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_collection_entries WHERE collection_id = ? ORDER BY document_id, ordinal, segment_id",
                (collection_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key, default in (
                ("locator_json", {}),
                ("keyword_matches_json", []),
                ("metadata_matches_json", []),
            ):
                try:
                    item[key.removesuffix("_json")] = json.loads(item[key] or json.dumps(default))
                except (TypeError, ValueError):
                    item[key.removesuffix("_json")] = default
            result.append(item)
        return result

    def put_entry(
        self,
        collection_id: str,
        *,
        document_id: str,
        source_version: str,
        segment_id: str,
        ordinal: int,
        locator: Mapping[str, object],
        text: str,
        summary: str,
        read: bool,
        keyword_matches: Sequence[str],
        metadata_matches: Sequence[str] = (),
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_collection_entries(collection_id, document_id, source_version, segment_id, ordinal, locator_json, text, extractive_summary, read, keyword_matches_json, metadata_matches_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(collection_id, document_id, source_version, segment_id) DO UPDATE SET ordinal=excluded.ordinal, locator_json=excluded.locator_json, text=excluded.text, extractive_summary=excluded.extractive_summary, read=excluded.read, keyword_matches_json=excluded.keyword_matches_json, metadata_matches_json=excluded.metadata_matches_json, updated_at=excluded.updated_at",
                (
                    collection_id,
                    document_id,
                    source_version,
                    segment_id,
                    ordinal,
                    json.dumps(dict(locator), ensure_ascii=False),
                    text,
                    summary,
                    int(read),
                    json.dumps(list(keyword_matches), ensure_ascii=False),
                    json.dumps(list(metadata_matches), ensure_ascii=False),
                    now_ts(),
                ),
            )

    def delete_entry(
        self, collection_id: str, document_id: str, source_version: str, segment_id: str
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collection_entries WHERE collection_id = ? AND document_id = ? AND source_version = ? AND segment_id = ?",
                (collection_id, document_id, source_version, segment_id),
            )

    def delete_content_entries(self, collection_id: str, document_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collection_entries WHERE collection_id = ? AND document_id = ? AND segment_id <> ''",
                (collection_id, document_id),
            )

    def add_members(self, collection_id: str, document_ids: Sequence[str]) -> None:
        if not document_ids:
            return
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO knowledge_collection_members(collection_id, document_id) VALUES (?, ?) ON CONFLICT(collection_id, document_id) DO NOTHING",
                [(collection_id, document_id) for document_id in document_ids],
            )

    def remove_member(self, collection_id: str, document_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collection_members WHERE collection_id = ? AND document_id = ?",
                (collection_id, document_id),
            )

    def update_target_path(
        self, collection_id: str, owner_user_id: int, target_path: str
    ) -> KnowledgeCollectionRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_collections SET target_path = ?, updated_at = ? WHERE collection_id = ? AND owner_user_id = ?",
                (target_path, now_ts(), collection_id, owner_user_id),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_collections WHERE collection_id = ? AND owner_user_id = ?",
                (collection_id, owner_user_id),
            ).fetchone()
        return KnowledgeCollectionRow.from_row(row) if row else None

    def remove_doc_entries(
        self, collection_id: str, document_id: str, *, keep_version: str | None = None
    ) -> None:
        with self._db.transaction() as conn:
            if keep_version is None:
                conn.execute(
                    "DELETE FROM knowledge_collection_entries WHERE collection_id = ? AND document_id = ?",
                    (collection_id, document_id),
                )
            else:
                conn.execute(
                    "DELETE FROM knowledge_collection_entries WHERE collection_id = ? AND document_id = ? AND source_version <> ?",
                    (collection_id, document_id, keep_version),
                )

    def delete_entries(self, collection_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collection_entries WHERE collection_id = ?",
                (collection_id,),
            )

    def delete(self, collection_id: str, owner_user_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM knowledge_collections WHERE collection_id = ? AND owner_user_id = ?",
                (collection_id, owner_user_id),
            )

    def list_for_base(self, kb_id: str) -> list[KnowledgeCollectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_collections WHERE knowledge_base_id = ? ORDER BY owner_user_id, target_path",
                (kb_id,),
            ).fetchall()
        return [KnowledgeCollectionRow.from_row(row) for row in rows]

    def collections_for_document(self, kb_id: str, doc_id: str) -> list[KnowledgeCollectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT c.* FROM knowledge_collections c "
                "LEFT JOIN knowledge_collection_entries e ON e.collection_id = c.collection_id "
                "LEFT JOIN knowledge_collection_members m ON m.collection_id = c.collection_id "
                "LEFT JOIN knowledge_documents folder_target ON folder_target.document_id = c.target_document_id "
                "WHERE c.knowledge_base_id = ? AND (c.target_document_id = ? OR e.document_id = ? OR m.document_id = ? "
                "OR (c.target_kind = 'folder' AND folder_target.kb_id = c.knowledge_base_id AND ("
                "folder_target.path = '' OR folder_target.path = (SELECT path FROM knowledge_documents WHERE document_id = ?) "
                "OR substr((SELECT path FROM knowledge_documents WHERE document_id = ?), 1, length(folder_target.path) + 1) = folder_target.path || '/')))"
                " ORDER BY c.collection_id",
                (kb_id, doc_id, doc_id, doc_id, doc_id, doc_id),
            ).fetchall()
        return [KnowledgeCollectionRow.from_row(row) for row in rows]


__all__ = ["KnowledgeCollectionRow", "KnowledgeCollectionsRepo"]
