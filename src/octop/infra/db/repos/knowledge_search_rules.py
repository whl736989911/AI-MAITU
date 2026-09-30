"""Per-user search rules attached to stable knowledge-file or folder identities."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


@dataclass(frozen=True)
class KnowledgeSearchRuleRow:
    document_id: str
    user_id: int
    mode: str
    keywords: tuple[str, ...]
    updated_at: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> KnowledgeSearchRuleRow:
        return cls(
            document_id=str(row["document_id"]),
            user_id=int(row["user_id"]),
            mode=str(row["mode"]),
            keywords=tuple(json.loads(str(row["keywords_json"]))),
            updated_at=int(row["updated_at"]),
        )


class KnowledgeSearchRulesRepo:
    """Read and write one caller's document rules; callers enforce document ACLs."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_for_user(self, *, user_id: int) -> list[KnowledgeSearchRuleRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT document_id, user_id, mode, keywords_json, updated_at "
                "FROM knowledge_search_rules WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [KnowledgeSearchRuleRow.from_row(row) for row in rows]

    def get(self, *, document_id: str, user_id: int) -> KnowledgeSearchRuleRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT document_id, user_id, mode, keywords_json, updated_at "
                "FROM knowledge_search_rules WHERE document_id = ? AND user_id = ?",
                (document_id, user_id),
            ).fetchone()
        return KnowledgeSearchRuleRow.from_row(row) if row is not None else None

    def set(
        self, *, document_id: str, user_id: int, mode: str, keywords: Sequence[str]
    ) -> KnowledgeSearchRuleRow:
        if mode not in {"keyword", "hybrid", "exclude"}:
            raise ValueError("invalid knowledge search mode")
        payload = json.dumps(list(keywords), ensure_ascii=False, separators=(",", ":"))
        ts = now_ts()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO knowledge_search_rules "
                "(document_id, user_id, mode, keywords_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(document_id, user_id) DO UPDATE SET "
                "mode = excluded.mode, keywords_json = excluded.keywords_json, "
                "updated_at = excluded.updated_at",
                (document_id, user_id, mode, payload, ts),
            )
        row = self.get(document_id=document_id, user_id=user_id)
        if row is None:
            raise RuntimeError("knowledge search rule was not stored")
        return row

    def delete(self, *, document_id: str, user_id: int) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "DELETE FROM knowledge_search_rules WHERE document_id = ? AND user_id = ?",
                (document_id, user_id),
            )


__all__ = ["KnowledgeSearchRuleRow", "KnowledgeSearchRulesRepo"]
