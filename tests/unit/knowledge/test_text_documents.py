"""KnowledgeService text create / update helpers."""

from __future__ import annotations

import asyncio
import base64
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

import octop.infra.knowledge.service as knowledge_service
from octop.api.routers import knowledge_bases as knowledge_api
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.knowledge.files import document_digest, document_path
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.jobs import process_document
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / ".octop"))
    paths = PathLayout.from_env()
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    owner_id = UserRepo(db).create(username="owner", password_hash="h", role="user")
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: {
        "knowledge_feature_enabled": "1",
        "knowledge_embedding_backend": "onnx",
        "knowledge_embedding_model": "tiny",
    }.get(key, default)
    return SimpleNamespace(
        db=db,
        repos=RepoBundle.from_pool(db),
        knowledge_repo=KnowledgeRepo(db),
        settings_repo=settings,
        user_repo=UserRepo(db),
        provider_repo=None,
        owner_id=owner_id,
    )


def test_create_and_update_text_document(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Hello\n",
    )
    assert created.filename == "notes.md"
    assert created.content_type == "text/markdown"
    assert created.status == "pending"
    path = document_path(base.id, created.id, created.filename)
    assert path.read_text(encoding="utf-8") == "# Hello\n"

    services.knowledge_repo.replace_document_facts(
        created.id,
        base.id,
        [
            {
                "fact_type": "clause",
                "value": "old",
                "normalized": "old",
                "locator": {"page": 1},
                "extractor": "test",
            }
        ],
    )
    updated = svc.update_text_document(
        base.id,
        created.id,
        actor_user_id=services.owner_id,
        content="# Updated\n",
    )
    assert services.knowledge_repo.list_document_facts(created.id) == []
    assert updated.status == "pending"
    assert updated.content_hash == document_digest(b"# Updated\n")
    assert path.read_text(encoding="utf-8") == "# Updated\n"
    raw = svc.read_text_document(base.id, created.id, actor_user_id=services.owner_id)
    assert raw["text"] == "# Updated\n"


def test_upload_spreadsheet_documents(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Sheets")
    xlsx = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="sales.xlsx",
        content_type="application/octet-stream",
        content=b"fake-xlsx",
    )
    csv_doc = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="sales.csv",
        content_type="application/vnd.ms-excel",
        content=b"a,b\n1,2\n",
    )
    xls = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="legacy.xls",
        content_type="application/vnd.ms-excel",
        content=b"fake-xls",
    )
    html = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="page.html",
        content_type="text/html",
        content=b"<p>hi</p>",
    )
    json_doc = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="data.json",
        content_type="application/json",
        content=b"{}",
    )
    xlsm = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="macro.xlsm",
        content_type="application/octet-stream",
        content=b"fake-xlsm",
    )
    assert xlsx.content_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert csv_doc.content_type == "text/csv"
    assert xls.content_type == "application/vnd.ms-excel"
    assert html.content_type == "text/html"
    assert json_doc.content_type == "application/json"
    assert xlsm.content_type == "application/vnd.ms-excel.sheet.macroEnabled.12"


def test_disabled_ocr_preserves_image_original_without_indexed_text(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Images")
    original = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
        "x8AAwMCAO+/mWQAAAAASUVORK5CYII="
    )
    created = svc.upload_document(
        base.id,
        actor_user_id=services.owner_id,
        filename="scan.png",
        content_type="application/octet-stream",
        content=original,
    )

    process_document(services, base.id, created.id)

    archived = services.knowledge_repo.get_document(created.id)
    assert archived is not None
    assert archived.status == "archive_only"
    assert archived.error_message == "ocr_disabled"
    assert archived.chunk_count == 0
    assert archived.content_type == "image/png"
    assert document_path(base.id, created.id, created.filename).read_bytes() == original


def test_rename_rejects_dot_names_that_collapse_to_the_parent(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Hello\n",
    )

    with pytest.raises(ValueError):
        svc.rename_document(
            base.id,
            created.id,
            new_name=".",
            actor_user_id=services.owner_id,
        )
    assert services.knowledge_repo.get_document(created.id).filename == "notes.md"


def test_rename_preserves_original_bytes_across_extension_change(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Original\n",
    )
    old_path = document_path(base.id, created.id, created.filename)

    renamed = svc.rename_document(
        base.id,
        created.id,
        new_name="notes.txt",
        actor_user_id=services.owner_id,
    )

    new_path = document_path(base.id, created.id, renamed.filename)
    assert renamed.filename == "notes.txt"
    assert renamed.content_type == "text/plain"
    assert new_path.read_bytes() == b"# Original\n"
    assert not old_path.exists()


@pytest.mark.parametrize(
    ("replacement_name", "content_type"),
    [("renamed.md", "text/markdown"), ("renamed.txt", "text/plain")],
)
def test_replace_content_keeps_new_original_and_discards_old_structure(
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    replacement_name: str,
    content_type: str,
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Original\n",
    )
    services.knowledge_repo.set_derived(created.id, {"title": "Old title"})
    old_path = document_path(base.id, created.id, created.filename)
    replacement = b"# Replacement\n"

    replaced = svc.replace_document_content(
        base.id,
        created.id,
        actor_user_id=services.owner_id,
        path=replacement_name,
        content_type=content_type,
        content=replacement,
    )

    new_path = document_path(base.id, created.id, replaced.filename)
    assert new_path.read_bytes() == replacement
    assert replaced.content_hash == document_digest(replacement)
    if new_path != old_path:
        assert not old_path.exists()
    raw = svc.read_text_document(base.id, created.id, actor_user_id=services.owner_id)
    assert raw["text"] == replacement.decode()
    assert raw["title"] != "Old title"


def test_replace_content_rejects_missing_or_unwritable_destination_folder(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Original\n",
    )
    original_file = document_path(base.id, created.id, created.filename)
    folder = services.knowledge_repo.ensure_folder(base.id, "restricted")
    ResourceAclRepo(services.db).upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=folder.id,
            owner_user_id=None,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(),
        )
    )

    with pytest.raises(ValueError):
        svc.replace_document_content(
            base.id,
            created.id,
            actor_user_id=services.owner_id,
            path="missing/notes.md",
            content_type="text/markdown",
            content=b"# Replacement\n",
        )
    with pytest.raises(PermissionError):
        svc.replace_document_content(
            base.id,
            created.id,
            actor_user_id=services.owner_id,
            path="restricted/notes.md",
            content_type="text/markdown",
            content=b"# Replacement\n",
        )
    assert services.knowledge_repo.get_document(created.id) == created
    assert original_file.read_bytes() == b"# Original\n"


def test_replace_content_cannot_take_another_documents_path(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="first",
        format="md",
        content="first\n",
    )
    second = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="second",
        format="md",
        content="second\n",
    )

    with pytest.raises(ValueError):
        svc.replace_document_content(
            base.id,
            second.id,
            actor_user_id=services.owner_id,
            path="first.md",
            content_type="text/markdown",
            content=b"replacement\n",
        )
    assert services.knowledge_repo.get_document(second.id).path == "second.md"


def test_failed_original_commit_preserves_previous_text(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "octop.infra.knowledge.service.assert_knowledge_usable",
        lambda *_a, **_k: None,
    )
    svc = KnowledgeService(services)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Docs")
    created = svc.create_text_document(
        base.id,
        actor_user_id=services.owner_id,
        name="notes",
        format="md",
        content="# Original\n",
    )

    def reject_commit(_source: object, _target: object) -> None:
        raise OSError("original commit failed")

    monkeypatch.setattr("os.replace", reject_commit)
    with pytest.raises(OSError, match="original commit failed"):
        svc.update_text_document(
            base.id, created.id, actor_user_id=services.owner_id, content="# Replacement\n"
        )

    raw = svc.read_text_document(base.id, created.id, actor_user_id=services.owner_id)
    assert raw["text"] == "# Original\n"
    assert services.knowledge_repo.get_document(created.id).content_hash == created.content_hash


def test_concurrent_rename_and_edit_keep_original_bytes_and_metadata_consistent(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge_service, "assert_knowledge_usable", lambda *_a, **_k: None)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Concurrent")
    document = KnowledgeService(services).create_text_document(
        base.id, actor_user_id=services.owner_id, name="notes.md", format="md", content="old"
    )
    copied = threading.Event()
    release_rename = threading.Event()
    coordination: queue.Queue[str] = queue.Queue()
    writer = threading.local()
    real_lock = threading.RLock()

    class CoordinatedLock:
        def __enter__(self) -> None:
            if getattr(writer, "editing", False):
                coordination.put("writer_entering")
            real_lock.acquire()

        def __exit__(self, *_args: object) -> None:
            real_lock.release()

    lock = CoordinatedLock()
    # Observe attempted acquisition without assuming which operation wins.
    monkeypatch.setattr(
        knowledge_service, "_knowledge_mutation_lock", lambda _kb: lock, raising=False
    )
    original_copy = knowledge_service.shutil.copyfile

    def pause_after_copy(source: Path, target: Path) -> str:
        result = original_copy(source, target)
        copied.set()
        if not release_rename.wait(5):
            raise TimeoutError("rename was not released")
        return str(result)

    monkeypatch.setattr(knowledge_service.shutil, "copyfile", pause_after_copy)

    def edit() -> None:
        writer.editing = True
        KnowledgeService(services).update_text_document(
            base.id, document.id, actor_user_id=services.owner_id, content="new bytes"
        )
        coordination.put("writer_completed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        renamed = executor.submit(
            KnowledgeService(services).rename_document,
            base.id,
            document.id,
            new_name="notes.txt",
            actor_user_id=services.owner_id,
        )
        edited = None
        try:
            assert copied.wait(5)
            edited = executor.submit(edit)
            coordination.get(timeout=5)
        finally:
            release_rename.set()
        renamed.result(timeout=5)
        assert edited is not None
        edited.result(timeout=5)

    current = services.knowledge_repo.get_document(document.id)
    assert current is not None
    actual = document_path(base.id, document.id, current.filename).read_bytes()
    assert actual == b"new bytes"
    assert current.content_hash == document_digest(actual)
    assert (
        KnowledgeService(services).read_text_document(
            base.id, document.id, actor_user_id=services.owner_id
        )["text"]
        == "new bytes"
    )


async def test_cancelled_content_update_waits_for_commit_and_indexes_new_text(
    services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge_service, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(knowledge_api, "_require_usable", lambda *_a: None)
    base = services.knowledge_repo.create_base(owner_user_id=services.owner_id, name="Cancelled")
    document = KnowledgeService(services).create_text_document(
        base.id, actor_user_id=services.owner_id, name="notes.md", format="md", content="old"
    )
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    indexed = asyncio.Event()
    original_update = KnowledgeService.update_text_document
    original_enqueue = knowledge_api.enqueue_index_document

    def blocked_update(self: KnowledgeService, *args: object, **kwargs: object) -> object:
        entered.set()
        try:
            if not release.wait(5):
                raise TimeoutError("content update was not released")
            return original_update(self, *args, **kwargs)
        finally:
            exited.set()

    def enqueue(*args: object) -> asyncio.Task[None]:
        job = original_enqueue(*args)
        job.add_done_callback(lambda _job: indexed.set())
        return job

    def no_embedding(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no embedding model configured")

    monkeypatch.setattr(KnowledgeService, "update_text_document", blocked_update)
    monkeypatch.setattr(knowledge_api, "enqueue_index_document", enqueue)
    monkeypatch.setattr("octop.infra.knowledge.jobs.assert_embedding_usable", no_embedding)
    request = asyncio.create_task(
        knowledge_api.update_text_document(
            base.id,
            document.id,
            knowledge_api.UpdateTextDocumentBody(content="cancel_safe_marker"),
            Request({"type": "http", "headers": []}),
            server=SimpleNamespace(services=services),
            user=SimpleNamespace(id=services.owner_id, is_admin=False),
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(2):
            request.cancel()
            await asyncio.sleep(0)
        finished_before_commit = request.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert await asyncio.to_thread(exited.wait, 5)
    assert not finished_before_commit
    await asyncio.wait_for(indexed.wait(), timeout=10)
    current = services.knowledge_repo.get_document(document.id)
    assert current is not None and current.status == "ready"
    assert current.content_hash == document_digest(b"cancel_safe_marker")
    preview = KnowledgeIndex(base.id).document_preview_blocks(document.id, max_chars=1000)
    assert preview is not None
    assert "cancel_safe_marker" in preview[0]
