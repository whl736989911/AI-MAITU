"""Lexical search over a knowledge base's chunks (design §9, §12.9).

The design asks for keyword and full-text search *independently of* the vector
index, and says why: an identifier, a name, or an exact phrase is what a user
types when they already know what they are looking for, and embeddings are bad
at exactly that. :meth:`KnowledgeIndex.search` answers the other half — similar
meaning, different words — and this module answers this one.

Two halves, deliberately apart:

* **Terms, score, and snippet are pure functions.** What a query is searched by,
  what makes one chunk a better answer, and the excerpt shown with a hit are all
  decided without a database, so the ranking can be tested on its own and
  argued with when it looks wrong.
* **The narrowing is the store's job.** ``KnowledgeIndex.search_text`` decides
  which chunks are candidates in SQL — a substring test is what the engine is
  for — and the scorer runs over what comes back.

CJK has no spaces, so a query is not split on whitespace alone: a run of Han,
Kana, or Hangul becomes one term when it is short (a field name, a model number)
and its two-character windows when it is long (a sentence — and its windows are
what find the part of it a document actually contains).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.knowledge.index import Hit, KnowledgeIndex

MAX_TERMS = 12
"""Bound on the terms one query contributes; more stops being a search."""

SNIPPET_CHARS = 200
DEFAULT_SEARCH_K = 20

_CJK_RUN = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_TERM_RE = re.compile(rf"[0-9A-Za-z_]+|[{_CJK_RUN}]+")
_MAX_WHOLE_CJK = 4
"""A run this short is one term; longer runs are cut into windows instead."""


@dataclass(frozen=True)
class SearchHit:
    """One search result: where it came from, and how it reads (design §9, §14).

    ``ordinal`` is the chunk's position inside its document and ``path`` is the
    document's path in the knowledge base — together they are the "原文件路径及
    位置" a result is supposed to be able to cite, and they are what the
    dashboard's existing citation deep link needs to open the file.
    """

    kb_id: str
    base_name: str
    document_id: str
    filename: str
    path: str
    source_path: str
    title: str
    ordinal: int
    snippet: str
    score: float


def query_terms(query: str) -> tuple[str, ...]:
    """The terms a query is searched by, lower-cased and de-duplicated."""
    terms: list[str] = []
    for match in _TERM_RE.finditer(query or ""):
        token = match.group(0)
        if token.isascii():
            terms.append(token.lower())
        elif len(token) <= _MAX_WHOLE_CJK:
            terms.append(token)
        else:
            terms.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tuple(dict.fromkeys(terms))[:MAX_TERMS]


def normalize_query(query: str) -> str:
    """The query as one compared string, for the whole-query test."""
    return " ".join((query or "").split()).lower()


def score_chunk(text: str, terms: Sequence[str], phrase: str = "") -> float:
    """How well a chunk answers a query; larger is better.

    Three signals, in the order they matter:

    * **The whole query appears literally.** The strongest evidence there is, and
      what makes typing a phrase behave the way it reads.
    * **How many of the terms appear at all.** A chunk holding every term beats
      one that repeats a single rare one.
    * **How often they appear, damped.** A word mentioned fifty times is not
      fifty times more relevant, so the frequency term saturates.

    Nothing here rewards a shorter chunk on its own: a chunk is a fixed window of
    a document, so its length says more about where it fell than about how well
    it answers.
    """
    if not terms and not phrase:
        return 0.0
    haystack = " ".join(text.lower().split())
    found = sum(1 for term in terms if term in haystack)
    coverage = found / len(terms) if terms else 0.0
    occurrences = sum(haystack.count(term) for term in terms)
    score = 2.0 * coverage + min(occurrences, 5) / 5.0
    if phrase and phrase in haystack:
        score += 3.0
    return score


def snippet(text: str, terms: Sequence[str], *, width: int = SNIPPET_CHARS) -> str:
    """The excerpt shown with a hit, with the first match inside it.

    Centred on the match rather than taken from the start of the chunk: a chunk
    can be eight hundred characters, and the sentence that answers the query is
    usually not its first one.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= width:
        return collapsed
    at = _first_match(collapsed.lower(), terms)
    start = max(0, min(at - width // 3, len(collapsed) - width))
    end = start + width
    head = "…" if start > 0 else ""
    tail = "…" if end < len(collapsed) else ""
    return f"{head}{collapsed[start:end]}{tail}"


def _first_match(haystack: str, terms: Sequence[str]) -> int:
    best = -1
    for term in terms:
        at = haystack.find(term)
        if at >= 0 and (best < 0 or at < best):
            best = at
    return max(best, 0)


def rank_hits(hits: Sequence[Hit], terms: Sequence[str], phrase: str = "") -> list[Hit]:
    """The hits re-scored for a lexical query, best first.

    Ties break by document and then by position, so the same query over the same
    index answers with the same order every time — a result list that shuffles
    between identical searches is unusable for the person comparing two of them.
    """
    scored = [
        Hit(
            chunk_id=hit.chunk_id,
            doc_id=hit.doc_id,
            ordinal=hit.ordinal,
            text=hit.text,
            score=score_chunk(hit.text, terms, phrase),
            metadata=hit.metadata,
        )
        for hit in hits
    ]
    scored.sort(key=lambda hit: (-hit.score, hit.doc_id, hit.ordinal))
    return scored


def search_base(
    kb_id: str,
    *,
    query: str,
    ready_documents: dict[str, Any],
    limit: int,
) -> list[tuple[Hit, Any]]:
    """Rank one base's chunks and keep only hits on documents that may be shown.

    ``ready_documents`` is the caller's answer to "which of this base's documents
    may be shown" — ready, and not folders — keyed by document id. It is built
    from the same rule the chat retrieval uses, so the two paths cannot disagree
    about what a search may return, and a file that is still indexing, failed, or
    someone else's cannot surface here.
    """
    terms = query_terms(query)
    if not terms or limit <= 0:
        return []
    phrase = normalize_query(query)
    ranked = rank_hits(
        KnowledgeIndex(kb_id).search_text(terms),
        terms,
        phrase,
    )
    out: list[tuple[Hit, Any]] = []
    for hit in ranked:
        document = ready_documents.get(hit.doc_id)
        if document is None:
            continue
        out.append((hit, document))
        if len(out) >= limit:
            break
    return out
