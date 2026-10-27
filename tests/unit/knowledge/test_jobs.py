"""Unit tests for in-process document indexing jobs."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import jobs
from octop.infra.knowledge.files import write_document
from octop.infra.knowledge.index import KnowledgeIndex


def test_enqueue_index_document_limits_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs.reset_index_semaphore_for_tests()
    monkeypatch.setattr(jobs, "INDEX_CONCURRENCY", 1)

    lock = threading.Lock()
    active = 0
    peak = 0
    entered = threading.Event()
    hold = threading.Event()

    def blocking(_services: object, _kb: str, _doc: str) -> bool:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        assert hold.wait(timeout=2)
        with lock:
            active -= 1
        return False

    monkeypatch.setattr(jobs, "_process_current_document", blocking)

    async def run() -> None:
        first = jobs.enqueue_index_document(object(), "kb", "d1")
        second = jobs.enqueue_index_document(object(), "kb", "d2")
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert peak == 1
        hold.set()
        await asyncio.gather(first, second)

    asyncio.run(run())
    assert peak == 1


@pytest.mark.parametrize("cancel_running", [False, True])
def test_shutdown_joins_sqlite_worker_and_leaves_queued_work_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_running: bool
) -> None:
    pool = SqlitePool(tmp_path / "shutdown.db")
    run_migrations(pool)
    settings = SettingsRepo(pool)
    settings.set("shutdown_probe", "read-before-close")
    services = SimpleNamespace(db=pool)
    started = threading.Event()
    release = threading.Event()
    reads: list[str | None] = []

    def process(_services: object, _kb: str, doc_id: str) -> bool:
        assert doc_id == "active"
        started.set()
        assert release.wait(timeout=10)
        reads.append(settings.get("shutdown_probe"))
        return False

    monkeypatch.setattr(jobs, "_process_current_document", process)

    async def run() -> None:
        active = jobs.enqueue_index_document(services, "kb", "active")
        assert await asyncio.to_thread(started.wait, 10)
        queued = jobs.enqueue_index_document(services, "kb", "queued")
        if cancel_running:
            active.cancel()
        shutdown = asyncio.create_task(jobs.shutdown_index_jobs(services))
        try:
            await asyncio.sleep(0)
            assert not shutdown.done()
            if cancel_running:
                active.cancel()
                await asyncio.sleep(0)
                assert not active.done()
        finally:
            release.set()
            await shutdown
        assert queued.cancelled()
        assert active.cancelled() is cancel_running

    try:
        asyncio.run(run())
        assert reads == ["read-before-close"]
    finally:
        release.set()
        pool.close()


def test_process_document_marks_empty_extract_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(
        owner_user_id=owner_id, name="Docs", embedding_model="BAAI/bge-small-zh-v1.5"
    )
    doc = repo.create_document(
        kb_id=kb.id, filename="blank.md", content_type="text/markdown", byte_size=0
    )
    write_document(kb.id, doc.id, doc.filename, b"   \n")
    services = SimpleNamespace(knowledge_repo=repo, settings_repo=SettingsRepo(pool))
    monkeypatch.setattr(jobs, "assert_embedding_usable", lambda *_args: None)
    monkeypatch.setattr(
        jobs, "embed_knowledge_texts", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError())
    )

    with pytest.raises(ValueError, match="no extractable text"):
        jobs.process_document(services, kb.id, doc.id)

    updated = repo.get_document(doc.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.chunk_count == 0


def test_process_document_indexes_chunks_and_marks_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(
        owner_user_id=owner_id, name="Docs", embedding_model="BAAI/bge-small-zh-v1.5"
    )
    doc = repo.create_document(
        kb_id=kb.id, filename="notes.md", content_type="text/markdown", byte_size=5
    )
    write_document(kb.id, doc.id, doc.filename, b"hello")
    services = SimpleNamespace(knowledge_repo=repo, settings_repo=SettingsRepo(pool))
    monkeypatch.setattr(jobs, "assert_embedding_usable", lambda *_args: None)
    monkeypatch.setattr(
        jobs, "embed_knowledge_texts", lambda _services, texts: [[1.0, 0.0] for _ in texts]
    )

    jobs.process_document(services, kb.id, doc.id)

    updated = repo.get_document(doc.id)
    assert updated is not None
    assert updated.status == "ready"
    assert updated.chunk_count == 1
    assert KnowledgeIndex(kb.id).search([1.0, 0.0], 1)[0].doc_id == doc.id


def test_process_document_indexes_ocr_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(owner_user_id=owner_id, name="Scans", embedding_model="model")
    doc = repo.create_document(
        kb_id=kb.id, filename="scan.png", content_type="image/png", byte_size=5
    )
    write_document(kb.id, doc.id, doc.filename, b"image")
    services = SimpleNamespace(knowledge_repo=repo, settings_repo=SettingsRepo(pool))
    monkeypatch.setattr(jobs, "assert_embedding_usable", lambda *_args: None)
    monkeypatch.setattr(jobs, "optional_ocr_extractor", lambda _services: lambda _path: "发票")
    monkeypatch.setattr(jobs, "embed_knowledge_texts", lambda _services, _texts: [[1.0, 0.0]])

    jobs.process_document(services, kb.id, doc.id)

    updated = repo.get_document(doc.id)
    assert updated is not None
    assert updated.status == "ready"
    assert KnowledgeIndex(kb.id).search([1.0, 0.0], 1)[0].text == "发票"


def test_reindex_all_updates_base_model_marks_documents_pending_and_enqueues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(owner_user_id=owner_id, name="Docs", embedding_model="old-model")
    doc = repo.create_document(
        kb_id=kb.id, filename="notes.md", content_type="text/markdown", byte_size=5
    )
    repo.update_document(doc.id, status="ready", chunk_count=1)
    services = SimpleNamespace(knowledge_repo=repo)
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        jobs,
        "enqueue_index_document",
        lambda _services, kb_id, doc_id: enqueued.append((kb_id, doc_id)),
    )

    jobs.reindex_all_documents(services, "new-model")

    assert repo.get_base(kb.id).embedding_model == "new-model"
    assert repo.get_base(kb.id).embedding_dim == 0
    assert repo.get_document(doc.id).status == "pending"
    assert enqueued == [(kb.id, doc.id)]


def test_resume_pending_index_jobs_resets_processing_and_enqueues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(owner_user_id=owner_id, name="Docs")
    doc = repo.create_document(
        kb_id=kb.id, filename="notes.md", content_type="text/markdown", byte_size=5
    )
    repo.update_document(doc.id, status="processing")
    services = SimpleNamespace(knowledge_repo=repo)
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        jobs,
        "enqueue_index_document",
        lambda _services, kb_id, doc_id: enqueued.append((kb_id, doc_id)),
    )

    jobs.resume_pending_index_jobs(services)

    assert repo.get_document(doc.id).status == "pending"
    assert enqueued == [(kb.id, doc.id)]


def test_direct_ingestion_shares_the_background_resource_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    active = 0
    peak = 0
    observed = threading.Lock()

    def ingest(*_args: object, **_kwargs: object) -> None:
        nonlocal active, peak
        with observed:
            active += 1
            peak = max(peak, active)
        entered.set()
        if _args[2] == "second":
            second_entered.set()
        try:
            assert release.wait(timeout=5)
        finally:
            with observed:
                active -= 1

    monkeypatch.setattr(jobs, "_process_locked", ingest)

    def second() -> None:
        second_started.set()
        jobs.process_document(object(), "kb", "second")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(jobs.process_document, object(), "kb", "first")
        try:
            assert entered.wait(timeout=5)
            other = executor.submit(second)
            assert second_started.wait(timeout=5)
            assert not second_entered.wait(timeout=0.1)
        finally:
            release.set()
        first.result(timeout=5)
        other.result(timeout=5)
    assert peak == 1


def test_large_document_keeps_all_vectors_with_a_bounded_embedding_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = KnowledgeRepo(pool)
    owner_id = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    kb = repo.create_base(owner_user_id=owner_id, name="Large document")
    lines = [f"Section {number}: document resource policy." for number in range(45)]
    content = "\n".join(lines).encode()
    doc = repo.create_document(
        kb_id=kb.id, filename="policy.txt", content_type="text/plain", byte_size=len(content)
    )
    write_document(kb.id, doc.id, doc.filename, content)
    services = SimpleNamespace(knowledge_repo=repo, settings_repo=SettingsRepo(pool))
    monkeypatch.setattr(jobs, "assert_embedding_usable", lambda *_args: None)

    def bounded_backend(_services: object, texts: list[str]) -> list[list[float]]:
        if len(texts) > 20:
            raise ValueError("backend input capacity exceeded")
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(jobs, "embed_knowledge_texts", bounded_backend)
    jobs.process_document(services, kb.id, doc.id)
    vectors = KnowledgeIndex(kb.id).doc_chunks(doc.id)
    assert [text for text, _vector, _metadata in vectors] == lines
    assert [metadata["segment_id"] for _text, _vector, metadata in vectors] == [
        f"{doc.id}:{number}" for number in range(45)
    ]
    assert repo.get_document(doc.id).status == "ready"
