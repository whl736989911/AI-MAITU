"""Per-knowledge-base SQLite index for source-located text and optional vectors."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from octop.infra.knowledge.parse import ParsedBlock
from octop.infra.utils.paths import PathLayout

_CJK_RUN = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_SEARCH_WORDS = re.compile(rf"(?P<cjk>[{_CJK_RUN}]+)|[^\W{_CJK_RUN}]+")


def search_tokens(text: str) -> Iterator[tuple[str, bool]]:
    """Yield normalized Unicode words and flag CJK runs."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for match in _SEARCH_WORDS.finditer(normalized):
        yield match.group(), match.lastgroup == "cjk"


def _index_terms(text: str) -> set[str]:
    """Bounded substrings make short CJK queries indexable without scanning text."""
    terms: set[str] = set()
    for word, cjk in search_tokens(text):
        if not cjk:
            terms.add(word)
        else:
            for offset in range(len(word)):
                terms.update(
                    word[offset : offset + length]
                    for length in range(1, 5)
                    if offset + length <= len(word)
                )
    return terms


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    score: float
    metadata: dict[str, object]


class KnowledgeIndex:
    """Store located segments and optional embeddings in one local sidecar per base."""

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
                CREATE TABLE IF NOT EXISTS segments (
                    segment_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    meta_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_segments_doc ON segments(doc_id);
                CREATE TABLE IF NOT EXISTS segment_terms (
                    term TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    PRIMARY KEY (term, segment_id)
                );
                CREATE INDEX IF NOT EXISTS idx_segment_terms_doc ON segment_terms(doc_id);
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

    def replace_doc_segments(
        self, doc_id: str, blocks: Sequence[ParsedBlock], *, version: str
    ) -> int:
        """Publish one version's located text and postings in a single transaction."""
        segments: list[tuple[str, str, int, str, str]] = []
        postings: list[tuple[str, str, str]] = []
        for ordinal, block in enumerate(blocks):
            text = block.text.strip()
            if not text:
                continue
            segment_id = f"{doc_id}:{ordinal}"
            meta = {
                "kind": block.kind,
                "heading": block.heading,
                "locator": block.locator,
                "version": version,
            }
            segments.append(
                (segment_id, doc_id, ordinal, text, json.dumps(meta, ensure_ascii=False))
            )
            postings.extend((term, doc_id, segment_id) for term in _index_terms(text))
        with self._connect() as conn:
            conn.execute("DELETE FROM segment_terms WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM segments WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO segments(segment_id, doc_id, ordinal, text, meta_json) VALUES (?, ?, ?, ?, ?)",
                segments,
            )
            conn.executemany(
                "INSERT INTO segment_terms(term, doc_id, segment_id) VALUES (?, ?, ?)",
                postings,
            )
        return len(segments)

    def get_segment(self, doc_id: str, segment_id: str) -> Hit | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT segment_id, doc_id, ordinal, text, meta_json FROM segments "
                "WHERE doc_id = ? AND segment_id = ?",
                (doc_id, segment_id),
            ).fetchone()
        if row is None:
            return None
        return Hit(row[0], row[1], row[2], row[3], 0.0, json.loads(row[4]))

    def search_segments(
        self,
        terms: Sequence[str],
        *,
        allowed_doc_ids: Sequence[str],
        phrase: str = "",
        limit: int = 1000,
    ) -> list[Hit]:
        """Join the caller's readable document set before bounding candidates."""
        cleaned = tuple(
            dict.fromkeys(unicodedata.normalize("NFKC", term).casefold() for term in terms if term)
        )
        if not cleaned or not allowed_doc_ids or limit <= 0:
            return []
        placeholders = ", ".join("?" for _ in cleaned)
        with self._connect() as conn:
            conn.execute("CREATE TEMP TABLE permitted(doc_id TEXT PRIMARY KEY)")
            conn.executemany(
                "INSERT OR IGNORE INTO permitted(doc_id) VALUES (?)",
                ((doc_id,) for doc_id in allowed_doc_ids),
            )
            rows = conn.execute(
                "SELECT s.segment_id, s.doc_id, s.ordinal, s.text, s.meta_json "
                "FROM segments AS s JOIN ("
                "SELECT p.segment_id, COUNT(*) AS matches FROM segment_terms AS p "
                "JOIN permitted AS a ON a.doc_id = p.doc_id "
                f"WHERE p.term IN ({placeholders}) GROUP BY p.segment_id "
                ") AS candidates ON candidates.segment_id = s.segment_id "
                "ORDER BY candidates.matches DESC, instr(lower(s.text), ?) DESC, "
                "candidates.segment_id LIMIT ?",
                (*cleaned, phrase, limit),
            ).fetchall()
        return [Hit(row[0], row[1], row[2], row[3], 0.0, json.loads(row[4])) for row in rows]

    def delete_doc_chunks(self, doc_id: str) -> None:
        """Remove obsolete vector text when a document is indexed lexically only."""
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def delete_doc(self, doc_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM segment_terms WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM segments WHERE doc_id = ?", (doc_id,))

    def search_text(
        self,
        terms: Sequence[str],
        *,
        allowed_doc_ids: Sequence[str] | None = None,
        phrase: str = "",
    ) -> list[Hit]:
        """Search located postings, with a legacy reader until old files are reindexed.

        Permission filtering happens inside the candidate query, before its
        bounded result set. Legacy chunks are read only for documents not yet
        converted to located segments.
        """
        cleaned = tuple(term for term in terms if term)
        if not cleaned:
            return []
        if allowed_doc_ids is None:
            with self._connect() as conn:
                allowed_doc_ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT doc_id FROM segments UNION SELECT doc_id FROM chunks"
                    )
                ]
        if not allowed_doc_ids:
            return []
        hits = self.search_segments(cleaned, allowed_doc_ids=allowed_doc_ids, phrase=phrase)
        clause = " OR ".join("instr(lower(c.text), ?) > 0" for _ in cleaned)
        with self._connect() as conn:
            conn.execute("CREATE TEMP TABLE permitted(doc_id TEXT PRIMARY KEY)")
            conn.executemany(
                "INSERT OR IGNORE INTO permitted(doc_id) VALUES (?)",
                ((doc_id,) for doc_id in allowed_doc_ids),
            )
            rows = conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.ordinal, c.text, c.meta_json "
                "FROM chunks AS c JOIN permitted AS a ON a.doc_id = c.doc_id "
                "WHERE NOT EXISTS (SELECT 1 FROM segments AS s WHERE s.doc_id = c.doc_id) "
                f"AND ({clause})",
                cleaned,
            ).fetchall()
        hits.extend(
            Hit(chunk_id, doc_id, ordinal, text, 0.0, json.loads(meta_json or "{}"))
            for chunk_id, doc_id, ordinal, text, meta_json in rows
        )
        return hits

    def search(
        self,
        query_vec: Sequence[float],
        k: int,
        *,
        allowed_doc_ids: Sequence[str] | None = None,
    ) -> list[Hit]:
        """Return the best optional vector hits only within readable documents."""
        if k <= 0:
            return []
        query = [float(value) for value in query_vec]
        if not query:
            raise ValueError("query vector cannot be empty")
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            raise ValueError("query vector cannot be zero")
        with self._connect() as conn:
            if allowed_doc_ids is not None:
                if not allowed_doc_ids:
                    return []
                conn.execute("CREATE TEMP TABLE permitted(doc_id TEXT PRIMARY KEY)")
                conn.executemany(
                    "INSERT OR IGNORE INTO permitted(doc_id) VALUES (?)",
                    ((doc_id,) for doc_id in allowed_doc_ids),
                )
                rows = conn.execute(
                    "SELECT c.chunk_id, c.doc_id, c.ordinal, c.text, c.embedding, c.meta_json "
                    "FROM chunks AS c JOIN permitted AS a ON a.doc_id = c.doc_id"
                ).fetchall()
            else:
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
