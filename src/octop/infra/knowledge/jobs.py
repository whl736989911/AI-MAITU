"""In-process document indexing jobs."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from octop.infra.knowledge.chunk import chunk_text
from octop.infra.knowledge.embed import embed_knowledge_texts
from octop.infra.knowledge.files import document_path
from octop.infra.knowledge.gate import assert_knowledge_usable
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.ocr import optional_ocr_extractor
from octop.infra.knowledge.params import get_advanced_settings
from octop.infra.knowledge.parse import parse_document
from octop.infra.knowledge.sources import SourceConnector

INDEX_CONCURRENCY = 2
_index_semaphore: asyncio.Semaphore | None = None

ParsePath = Callable[[Any], AbstractContextManager[Path]]
"""How the document being indexed becomes something to parse.

A context manager rather than a path because one of the two callers has to
create a temporary file and clean it up afterwards, and the cleanup has to
happen whether parsing succeeded or not.
"""


def reset_index_semaphore_for_tests() -> None:
    """Drop the cached semaphore so tests can change INDEX_CONCURRENCY."""
    global _index_semaphore
    _index_semaphore = None


def _get_index_semaphore() -> asyncio.Semaphore:
    global _index_semaphore
    if _index_semaphore is None:
        _index_semaphore = asyncio.Semaphore(INDEX_CONCURRENCY)
    return _index_semaphore


@contextmanager
def _platform_file(kb_id: str, doc_id: str, filename: str) -> Iterator[Path]:
    """The copy the platform holds, for an uploaded or fetched document.

    A context manager like its source-side twin so both satisfy one contract;
    this one has nothing to clean up.
    """
    yield document_path(kb_id, doc_id, filename)


@contextmanager
def _source_path(
    connector: SourceConnector | None, source_path: str, filename: str
) -> Iterator[Path]:
    """A source file as a local path, for the parsers that need one.

    Parsed through a temporary file that is deleted immediately, never into
    platform storage: design §3.3 makes the external folder the source of truth,
    so the platform keeps the index and not a second copy of the document.

    A temporary *file* rather than in-memory parsing because the parsers open
    from paths — ``xlrd``, ``python-docx`` and ``openpyxl`` all do — and
    re-plumbing ten of them onto streams would rewrite the parse layer without
    the design asking for it. Design §6.1's "转换使用临时目录 / 不修改原文件"
    is the same rule applied to the conversion path.
    """
    if connector is None:
        raise ValueError("a source file needs its connector")
    handle, temp_name = tempfile.mkstemp(suffix=Path(filename).suffix, prefix="octop-kbsrc-")
    os.close(handle)
    temp = Path(temp_name)
    try:
        temp.write_bytes(connector.read_bytes(source_path))
        yield temp
    finally:
        temp.unlink(missing_ok=True)


def process_document(services: Any, kb_id: str, doc_id: str) -> None:
    """Synchronously parse, embed, and atomically replace one platform document."""
    _process(
        services,
        kb_id,
        doc_id,
        parse_path=lambda row: _platform_file(kb_id, doc_id, row.filename),
    )


def process_source_file(
    services: Any,
    kb_id: str,
    doc_id: str,
    *,
    connector: SourceConnector | None,
    source_path: str,
) -> None:
    """The same chain for a file that lives in a source rather than in storage."""
    _process(
        services,
        kb_id,
        doc_id,
        parse_path=lambda row: _source_path(connector, source_path, row.filename),
    )


def _process(services: Any, kb_id: str, doc_id: str, *, parse_path: ParsePath) -> None:
    """Parse, chunk, embed, and atomically replace one document's chunks."""
    repo = services.knowledge_repo
    assert_knowledge_usable(services.settings_repo.get, getattr(services, "provider_repo", None))
    document = repo.get_document(doc_id)
    base = repo.get_base(kb_id)
    if document is None or document.kb_id != kb_id or base is None:
        raise LookupError("knowledge document or base not found")
    if document.is_dir:
        return
    repo.update_document(doc_id, status="processing", error_message="")
    try:
        with parse_path(document) as path:
            text = parse_document(path, ocr=optional_ocr_extractor(services))
        knobs = get_advanced_settings(services.settings_repo.get)
        chunks = chunk_text(text, size=knobs["chunk_size"], overlap=knobs["chunk_overlap"])
        if not (text or "").strip() or not chunks:
            raise ValueError("knowledge document has no extractable text")
        embeddings = embed_knowledge_texts(services, chunks)
        KnowledgeIndex(kb_id).replace_doc_chunks(doc_id, chunks, embeddings)
        dimension = len(embeddings[0]) if embeddings else 0
        repo.update_document(doc_id, status="ready", error_message="", chunk_count=len(chunks))
        if dimension and base.embedding_dim != dimension:
            repo.update_base(kb_id, embedding_dim=dimension)
    except Exception as exc:
        repo.update_document(doc_id, status="failed", error_message=str(exc), chunk_count=0)
        raise


def enqueue_index_document(services: Any, kb_id: str, doc_id: str) -> asyncio.Task[None]:
    """Schedule CPU- and I/O-bound indexing outside the event loop."""
    loop = asyncio.get_running_loop()
    sem = _get_index_semaphore()

    async def _run() -> None:
        async with sem:
            await loop.run_in_executor(None, process_document, services, kb_id, doc_id)

    return asyncio.create_task(_run())


def reindex_all_documents(services: Any, embedding_model: str) -> None:
    """Reset every index to use ``embedding_model`` and schedule fresh work."""
    documents = services.knowledge_repo.reindex_all_documents(embedding_model)
    for document in documents:
        enqueue_index_document(services, document.kb_id, document.id)


def resume_pending_index_jobs(services: Any) -> None:
    """Resume pending work and jobs interrupted by a prior process shutdown."""
    documents = services.knowledge_repo.resume_pending_documents()
    for document in documents:
        enqueue_index_document(services, document.kb_id, document.id)
