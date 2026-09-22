"""Per-knowledge-base SQLite sidecar vector index."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from octop.infra.utils.paths import PathLayout


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    score: float
    metadata: dict[str, object]


class KnowledgeIndex:
    """Store chunk embeddings in one local SQLite database per knowledge base."""

    def __init__(self, kb_id: str) -> None:
        self._path = PathLayout.from_env().knowledge_dir / kb_id / "index.sqlite"
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
                """
            )

    def replace_doc_chunks(
        self,
        doc_id: str,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        *,
        metadata: Sequence[dict[str, object]] | None = None,
    ) -> None:
        """Atomically replace all chunks belonging to one document."""
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings must have the same length")
        if metadata is not None and len(metadata) != len(texts):
            raise ValueError("metadata and texts must have the same length")
        rows: list[tuple[str, str, int, str, bytes, str]] = []
        for ordinal, (text, embedding) in enumerate(zip(texts, embeddings, strict=True)):
            vector = [float(value) for value in embedding]
            if not vector:
                raise ValueError("embedding cannot be empty")
            meta = metadata[ordinal] if metadata is not None else {}
            rows.append(
                (
                    f"{doc_id}:{ordinal}",
                    doc_id,
                    ordinal,
                    text,
                    struct.pack(f"<{len(vector)}f", *vector),
                    json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                )
            )
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO chunks(chunk_id, doc_id, ordinal, text, embedding, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def doc_chunks(self, doc_id: str) -> list[tuple[str, list[float], dict[str, object]]]:
        """One document's chunks in order: text, vector, metadata.

        The reader that matches :meth:`replace_doc_chunks`. It exists because a
        document can move between knowledge bases (design §13 folds a whole
        library into the space), and it has to take its chunks with it — the
        sidecar layout is this class's business, not the caller's.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT text, embedding, meta_json FROM chunks WHERE doc_id = ? ORDER BY ordinal",
                (doc_id,),
            ).fetchall()
        chunks: list[tuple[str, list[float], dict[str, object]]] = []
        for text, blob, meta_json in rows:
            vector = list(struct.unpack(f"<{len(blob) // 4}f", blob))
            chunks.append((str(text), vector, json.loads(meta_json or "{}")))
        return chunks

    def delete_doc(self, doc_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def search_text(self, terms: Sequence[str]) -> list[Hit]:
        """Chunks whose text contains any of *terms*, in no particular order.

        Candidates only. The ranking lives in ``knowledge.search``, which is
        where a score a person can argue with belongs; this method's job is the
        substring test, and running that in SQL is what the engine is for.

        *terms* are expected already normalized (``search.query_terms``
        lower-cases them), because SQLite's ``lower()`` only folds ASCII.
        """
        cleaned = [term for term in terms if term]
        if not cleaned:
            return []
        clause = " OR ".join("instr(lower(text), ?) > 0" for _ in cleaned)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT chunk_id, doc_id, ordinal, text, meta_json FROM chunks WHERE {clause}",
                cleaned,
            ).fetchall()
        hits: list[Hit] = []
        for chunk_id, doc_id, ordinal, text, meta_json in rows:
            decoded = json.loads(meta_json)
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    ordinal=ordinal,
                    text=text,
                    score=0.0,
                    metadata=decoded if isinstance(decoded, dict) else {},
                )
            )
        return hits

    def search(self, query_vec: Sequence[float], k: int) -> list[Hit]:
        """Return the ``k`` best chunk hits using in-process cosine similarity."""
        if k <= 0:
            return []
        query = [float(value) for value in query_vec]
        if not query:
            raise ValueError("query vector cannot be empty")
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            raise ValueError("query vector cannot be zero")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chunk_id, doc_id, ordinal, text, embedding, meta_json FROM chunks"
            ).fetchall()
        hits: list[Hit] = []
        for chunk_id, doc_id, ordinal, text, blob, meta_json in rows:
            embedding = struct.unpack(f"<{len(blob) // 4}f", blob)
            if len(embedding) != len(query):
                continue
            norm = math.sqrt(sum(value * value for value in embedding))
            score = (
                0.0
                if norm == 0
                else sum(a * b for a, b in zip(query, embedding, strict=True)) / (query_norm * norm)
            )
            decoded = json.loads(meta_json)
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    ordinal=ordinal,
                    text=text,
                    score=score,
                    metadata=decoded if isinstance(decoded, dict) else {},
                )
            )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:k]
