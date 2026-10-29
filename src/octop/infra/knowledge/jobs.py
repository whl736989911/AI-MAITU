"""In-process document indexing jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
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

_running_jobs: dict[tuple[int, str, str], asyncio.Task[None]] = {}
_active_jobs: set[tuple[int, str, str]] = set()
_stopping_owners: set[int] = set()


INDEX_CONCURRENCY = 1
_index_semaphore: asyncio.Semaphore | None = None
_index_loop: asyncio.AbstractEventLoop | None = None
# Also covers synchronous data-source ingestion outside the async queue.
_processing_lock = threading.Lock()
_EMBEDDING_BATCH_SIZE = 20

ParsePath = Callable[[Any], AbstractContextManager[Path]]
"""How the document being indexed becomes something to parse.

A context manager rather than a path because one of the two callers has to
create a temporary file and clean it up afterwards, and the cleanup has to
happen whether parsing succeeded or not.
"""


def reset_index_semaphore_for_tests() -> None:
    """Drop the cached semaphore so tests can change INDEX_CONCURRENCY."""
    global _index_semaphore, _index_loop
    _index_semaphore = None
    _index_loop = None


def _get_index_semaphore() -> asyncio.Semaphore:
    global _index_semaphore, _index_loop
    loop = asyncio.get_running_loop()
    if _index_semaphore is None or _index_loop is not loop:
        _index_semaphore = asyncio.Semaphore(INDEX_CONCURRENCY)
        _index_loop = loop
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
    from octop.infra.licensing import current_license

    current_license().require_business_access()
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
    from octop.infra.licensing import current_license

    current_license().require_business_access()
    _process(
        services,
        kb_id,
        doc_id,
        parse_path=lambda row: _source_path(connector, source_path, row.filename),
    )


def _process(services: Any, kb_id: str, doc_id: str, *, parse_path: ParsePath) -> None:
    """Index original-located text; embeddings are an optional ranking layer."""
    try:
        with _processing_lock:
            _process_locked(services, kb_id, doc_id, parse_path=parse_path)
    finally:
        if getattr(services, "knowledge_collections_repo", None) is not None:
            from octop.infra.knowledge.collections import KnowledgeCollectionService

            KnowledgeCollectionService(services).refresh_for_document(kb_id, doc_id)


def _embedding_batches(
    services: Any, doc_id: str, blocks: tuple[ParsedBlock, ...], knobs: dict[str, int]
) -> Iterator[tuple[list[str], list[list[float]], list[dict[str, object]]]]:
    texts: list[str] = []
    metadata: list[dict[str, object]] = []
    for ordinal, block in enumerate(blocks):
        for piece in chunk_text(
            block.text, size=knobs["chunk_size"], overlap=knobs["chunk_overlap"]
        ):
            texts.append(piece)
            metadata.append({"segment_id": f"{doc_id}:{ordinal}"})
            if len(texts) == _EMBEDDING_BATCH_SIZE:
                yield texts, embed_knowledge_texts(services, texts), metadata
                texts, metadata = [], []
    if texts:
        yield texts, embed_knowledge_texts(services, texts), metadata


def _process_locked(services: Any, kb_id: str, doc_id: str, *, parse_path: ParsePath) -> None:
    """Run one ingestion at a time, including its native model allocations."""
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
            parsed = replace(parsed, filename=document.filename)
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
            dimension = index.replace_doc_chunk_batches(
                doc_id, _embedding_batches(services, doc_id, blocks, knobs)
            )
        except Exception:
            # A missing or unavailable model must not prevent local lexical search.
            logger.info("optional knowledge embeddings unavailable for %s", doc_id)
            index.delete_doc_chunks(doc_id)
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
        from octop.infra.knowledge.legacy_office import LegacyConversionUnavailable
        from octop.infra.knowledge.processors import (
            ARCHIVE_CAD_UNAVAILABLE,
            ARCHIVE_LEGACY_OFFICE_UNAVAILABLE,
            ARCHIVE_NO_PROCESSOR,
            ARCHIVE_OCR_DISABLED,
        )
        from octop.infra.knowledge.processors.libredwg import CadProcessorUnavailable

        archive_reason: str | None = None
        if isinstance(exc, ValueError) and str(exc).startswith(
            "unsupported knowledge document extension:"
        ):
            archive_reason = ARCHIVE_NO_PROCESSOR
        elif isinstance(exc, CadProcessorUnavailable):
            archive_reason = ARCHIVE_CAD_UNAVAILABLE
        elif isinstance(exc, LegacyConversionUnavailable):
            archive_reason = ARCHIVE_LEGACY_OFFICE_UNAVAILABLE
        elif isinstance(exc, RuntimeError) and str(exc) == "knowledge OCR is not enabled":
            archive_reason = ARCHIVE_OCR_DISABLED
        if archive_reason is not None:
            KnowledgeIndex(kb_id).delete_doc(doc_id)
            repo.update_document(
                doc_id,
                status="archive_only",
                error_message=archive_reason,
                chunk_count=0,
                content_hash=digest,
            )
            return
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
    # Direct-entry license gate: index processing is business execution.
    from octop.infra.licensing import current_license

    current_license().require_business_access()
    owner = id(services)
    if owner in _stopping_owners:
        raise RuntimeError("knowledge indexing is shutting down")
    key = (owner, kb_id, doc_id)
    previous = _running_jobs.get(key)
    if previous is not None and not previous.done():
        return previous
    loop = asyncio.get_running_loop()
    sem = _get_index_semaphore()

    async def _run() -> None:
        async with sem:
            _active_jobs.add(key)
            try:
                while owner not in _stopping_owners:
                    work = loop.run_in_executor(
                        None, _process_current_document, services, kb_id, doc_id
                    )
                    try:
                        again = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        # Cancelling the asyncio task cannot stop its SQLite worker.
                        # Keep the task alive until that worker releases its resources.
                        while not work.done():
                            try:
                                await asyncio.shield(work)
                            except asyncio.CancelledError:
                                continue
                        work.result()
                        raise
                    if not again:
                        break
            finally:
                _active_jobs.discard(key)

    task = asyncio.create_task(_run())
    _running_jobs[key] = task

    def _finished(done: asyncio.Task[None]) -> None:
        if _running_jobs.get(key) is done:
            _running_jobs.pop(key, None)
        if not done.cancelled():
            try:
                done.result()
            except Exception:
                logger.exception("knowledge indexing failed for %s", doc_id)

    task.add_done_callback(_finished)
    return task


async def shutdown_index_jobs(services: Any) -> None:
    """Leave queued work pending and join active workers before closing their DB."""
    owner = id(services)
    owned = [(key, task) for key, task in _running_jobs.items() if key[0] == owner]
    if not owned:
        return
    _stopping_owners.add(owner)
    try:
        for key, task in owned:
            if key not in _active_jobs:
                task.cancel()
        await asyncio.gather(*(task for _key, task in owned), return_exceptions=True)
    finally:
        _stopping_owners.discard(owner)


def reindex_all_documents(services: Any, embedding_model: str) -> None:
    """Reset every index to use ``embedding_model`` and schedule fresh work."""
    # Direct-entry license gate: index processing is business execution.
    from octop.infra.licensing import current_license

    current_license().require_business_access()
    documents = services.knowledge_repo.reindex_all_documents(embedding_model)
    for document in documents:
        enqueue_index_document(services, document.kb_id, document.id)


def resume_pending_index_jobs(services: Any) -> None:
    """Resume pending work and jobs interrupted by a prior process shutdown."""
    # Direct-entry license gate: index processing is business execution.
    from octop.infra.licensing import current_license

    current_license().require_business_access()
    documents = services.knowledge_repo.resume_pending_documents()
    for document in documents:
        enqueue_index_document(services, document.kb_id, document.id)
