"""In-process document indexing jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from octop.infra.knowledge.chunk import chunk_text
from octop.infra.knowledge.embed import embed_knowledge_texts
from octop.infra.knowledge.files import document_path, file_digest
from octop.infra.knowledge.gate import assert_embedding_usable
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.ocr import optional_ocr_extractor
from octop.infra.knowledge.params import get_advanced_settings
from octop.infra.knowledge.parse import ParsedBlock, failure_status, parse_document
from octop.infra.knowledge.sources import SourceConnector

logger = logging.getLogger(__name__)

_running_jobs: dict[tuple[str, str], asyncio.Task[None]] = {}


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
        connector.copy_to(source_path, temp)
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


def document_text(services: Any, document: Any) -> str:
    """The document's text, re-parsed (design §12.6).

    Re-parsed rather than rebuilt from the stored chunks: chunks overlap by
    design, so joining them would repeat a slice of the document at every
    boundary. A source file is staged in a temporary copy exactly as indexing
    stages it, and a ``.doc`` is converted again — the cost is why the derived
    *structure* is stored, but the text itself is kept nowhere else.

    The import is local because ``data_sources`` imports this module, and the
    connector it provides is the whole point: extraction must reach a source's
    file the same way a scan did.
    """
    from octop.infra.knowledge.data_sources import DataSourceService

    source = (
        services.data_sources_repo.get(document.data_source_id) if document.data_source_id else None
    )
    if document.data_source_id and source is None:
        raise ValueError("the data source this file came from no longer exists")
    if source is None:
        with _platform_file(document.kb_id, document.id, document.filename) as path:
            return _parse_document_text(services, path, document)
    connector = DataSourceService(services).connector(source)
    with _source_path(connector, document.source_path, document.filename) as path:
        return _parse_document_text(services, path, document)


def _parse_document_text(services: Any, path: Path, document: Any) -> str:
    return parse_document(
        path, ocr=optional_ocr_extractor(services), source_path=document.display_path
    ).text


def _process(services: Any, kb_id: str, doc_id: str, *, parse_path: ParsePath) -> None:
    """Index original-located text; embeddings are an optional ranking layer."""
    repo = services.knowledge_repo
    document = repo.get_document(doc_id)
    base = repo.get_base(kb_id)
    if document is None or document.kb_id != kb_id or base is None:
        raise LookupError("knowledge document or base not found")
    if document.is_dir:
        return
    repo.update_document(doc_id, status="processing", error_message="")
    try:
        with parse_path(document) as path:
            digest = file_digest(path)
            parsed = parse_document(
                path,
                ocr=optional_ocr_extractor(services),
                # The path the document really has: a source's file is parsed
                # from a staged copy, and a stored one lives under its id, so
                # neither on-disk name is the file's own.
                source_path=document.display_path,
            )
        text = parsed.text
        if not text.strip():
            raise ValueError("knowledge document has no extractable text")
        blocks = parsed.blocks or (
            ParsedBlock(text=text, kind="document", locator={"kind": "document"}),
        )
        index = KnowledgeIndex(kb_id)
        count = index.replace_doc_segments(doc_id, blocks, version=digest)
        if not count:
            raise ValueError("knowledge document has no extractable text")
        dimension = 0
        try:
            assert_embedding_usable(
                services.settings_repo.get, getattr(services, "provider_repo", None)
            )
            knobs = get_advanced_settings(services.settings_repo.get)
            chunks = []
            vector_metadata: list[dict[str, object]] = []
            for ordinal, block in enumerate(blocks):
                for piece in chunk_text(
                    block.text, size=knobs["chunk_size"], overlap=knobs["chunk_overlap"]
                ):
                    chunks.append(piece)
                    vector_metadata.append({"segment_id": f"{doc_id}:{ordinal}"})
            embeddings = embed_knowledge_texts(services, chunks)
        except Exception:
            # A missing or unavailable model must not prevent local lexical search.
            logger.info("optional knowledge embeddings unavailable for %s", doc_id)
            index.delete_doc_chunks(doc_id)
        else:
            index.replace_doc_chunks(doc_id, chunks, embeddings, metadata=vector_metadata)
            dimension = len(embeddings[0]) if embeddings else 0
        repo.set_derived(doc_id, parsed.derived())
        repo.update_document(
            doc_id,
            status="ready",
            error_message="",
            chunk_count=count,
            content_hash=digest,
        )
        if dimension and base.embedding_dim != dimension:
            repo.update_base(kb_id, embedding_dim=dimension)
    except Exception as exc:
        repo.update_document(
            doc_id, status=failure_status(exc), error_message=str(exc), chunk_count=0
        )
        raise


def _process_current_document(services: Any, kb_id: str, doc_id: str) -> bool:
    """Reopen the right source kind when a queued job runs or resumes."""
    repo = services.knowledge_repo
    document = repo.get_document(doc_id)
    if document is None or document.kb_id != kb_id:
        return False
    if not document.data_source_id:
        process_document(services, kb_id, doc_id)
        return False
    from octop.infra.knowledge.data_sources import DataSourceService

    source = services.data_sources_repo.get(document.data_source_id)
    if source is None:
        repo.update_document(doc_id, status="failed", error_message="knowledge source unavailable")
        return False
    try:
        connector = DataSourceService(services).connector(source)
        process_source_file(
            services,
            kb_id,
            doc_id,
            connector=connector,
            source_path=document.source_path,
        )
    except Exception as exc:
        repo.update_document(doc_id, status=failure_status(exc), error_message=str(exc))
        raise
    current = repo.get_document(doc_id)
    if current is None:
        KnowledgeIndex(kb_id).delete_doc(doc_id)
    elif (current.observed_size, current.observed_modified_at) != (
        document.observed_size,
        document.observed_modified_at,
    ):
        repo.update_document(doc_id, status="pending", error_message="")
        return True
    else:
        repo.mark_source_processed(
            doc_id,
            size=document.observed_size
            if document.observed_size is not None
            else document.byte_size,
            modified_at=document.observed_modified_at,
        )
    return False


def enqueue_index_document(services: Any, kb_id: str, doc_id: str) -> asyncio.Task[None]:
    """Schedule a durable pending document once, bounded by the shared semaphore."""
    key = (kb_id, doc_id)
    previous = _running_jobs.get(key)
    if previous is not None and not previous.done():
        return previous
    loop = asyncio.get_running_loop()
    sem = _get_index_semaphore()

    async def _run() -> None:
        async with sem:
            while await loop.run_in_executor(
                None, _process_current_document, services, kb_id, doc_id
            ):
                pass

    task = asyncio.create_task(_run())
    _running_jobs[key] = task

    def _finished(done: asyncio.Task[None]) -> None:
        _running_jobs.pop(key, None)
        if not done.cancelled():
            try:
                done.result()
            except Exception:
                logger.exception("knowledge indexing failed for %s", doc_id)

    task.add_done_callback(_finished)
    return task


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
