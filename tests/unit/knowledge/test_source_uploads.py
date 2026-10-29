"""Source uploads preserve originals, access boundaries and scanner identity."""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.knowledge_sync_runs import KnowledgeSyncRunRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.auto_sync import AutoSyncRunner
from octop.infra.knowledge.data_sources import DataSourceService, FolderSettings
from octop.infra.knowledge.files import document_digest, document_path
from octop.infra.knowledge.jobs import enqueue_index_document, shutdown_index_jobs
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.knowledge.source_access import KnowledgeSourceService
from octop.infra.knowledge.sources.scan import DEBOUNCE_SECONDS
from octop.infra.sharing import AclEntry


@pytest.fixture
def source_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    home = tmp_path / "home"
    monkeypatch.setenv("MAITU_HOME", str(home))
    monkeypatch.setenv("OCTOP_HOME", str(home))
    pool = SqlitePool(home / "test.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        repos=RepoBundle.from_pool(pool),
        config=SimpleNamespace(max_upload_bytes=1_000_000),
        knowledge_repo=KnowledgeRepo(pool),
        knowledge_collections_repo=KnowledgeCollectionsRepo(pool),
        data_sources_repo=DataSourceRepo(pool),
        knowledge_sync_runs_repo=KnowledgeSyncRunRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        secret_repo=SecretRepo(pool),
        audit_repo=AuditRepo(pool),
        provider_repo=None,
    )
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = services.user_repo.create(username="uploader", password_hash="unused", role="admin")
    knowledge = KnowledgeService(services)
    kb_id = knowledge.enterprise_space().id
    root = tmp_path / "share"
    (root / "certificates").mkdir(parents=True)
    sources = DataSourceService(services)
    source = sources.create(
        kb_id,
        actor_user_id=actor,
        name="Equipment files",
        kind="local",
        folder=FolderSettings(root_path=str(root), read_only=False),
    )
    try:
        yield SimpleNamespace(
            services=services,
            knowledge=knowledge,
            sources=sources,
            actor=actor,
            kb_id=kb_id,
            source=source,
            root=root,
        )
    finally:
        pool.close()


async def test_source_original_is_searchable_without_platform_copy_or_duplicate_scan(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    content = b"# WRP-130\nCalibration certificate CERT-2026-014.\n"
    doc = env.sources.upload_document(
        env.kb_id,
        env.source.id,
        actor_user_id=env.actor,
        filename="certificate.md",
        path="certificates/certificate.md",
        content=content,
    )
    assert (env.root / "certificates" / "certificate.md").read_bytes() == content
    assert not document_path(env.kb_id, doc.id, doc.filename).exists()
    await enqueue_index_document(env.services, env.kb_id, doc.id)
    hits = env.knowledge.search(actor_user_id=env.actor, kb_id=env.kb_id, query="CERT-2026-014")
    assert any(hit.document_id == doc.id and hit.match_kind == "content" for hit in hits)
    with KnowledgeSourceService(env.services).open_original(
        env.kb_id,
        doc.id,
        actor_user_id=env.actor,
    ) as original:
        assert original.read_bytes() == content
    env.sources.sync(env.source.id, actor_user_id=env.actor)
    rows = [r for r in env.services.knowledge_repo.list_source_files(env.source.id) if not r.is_dir]
    assert [(r.id, r.source_path, r.status) for r in rows] == [
        (doc.id, "certificates/certificate.md", "ready")
    ]
    assert env.services.knowledge_repo.get_base(env.kb_id).doc_count == 1


@pytest.mark.asyncio
async def test_scheduled_scan_indexes_new_local_file_without_manual_enqueue(
    source_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = source_env
    env.services.data_sources_repo.update_source_fields(env.source.id, scan_interval_seconds=1)
    content = b"# Scheduled calibration\nTimer discovered keyword SYNC-INDEX-742.\n"
    (env.root / "scheduled.txt").write_bytes(content)

    clock = [int((env.root / "scheduled.txt").stat().st_mtime)]
    monkeypatch.setattr(
        "octop.infra.knowledge.auto_sync.time", SimpleNamespace(time=lambda: clock[0])
    )
    monkeypatch.setattr("octop.infra.knowledge.data_sources.now_ts", lambda: clock[0])
    runner = AutoSyncRunner(SimpleNamespace(services=env.services))

    async def searchable_document():
        while True:
            rows = env.services.knowledge_repo.list_source_files(env.source.id)
            document = next((row for row in rows if row.filename == "scheduled.txt"), None)
            if document is not None and document.status == "ready":
                hits = env.knowledge.search(
                    actor_user_id=env.actor,
                    kb_id=env.kb_id,
                    query="SYNC-INDEX-742",
                )
                if any(
                    hit.document_id == document.id and hit.match_kind == "content" for hit in hits
                ):
                    return document
            await asyncio.sleep(0.01)

    try:
        await runner.run()
        observed = next(
            row
            for row in env.services.knowledge_repo.list_source_files(env.source.id)
            if row.filename == "scheduled.txt"
        )
        assert observed.status == "discovered"
        clock[0] += DEBOUNCE_SECONDS + 1
        await runner.run()
        document = await asyncio.wait_for(searchable_document(), timeout=5)
        assert document.source_path == "scheduled.txt"
        assert not document_path(env.kb_id, document.id, document.filename).exists()
    finally:
        await shutdown_index_jobs(env.services)


def test_platform_upload_still_stores_original_in_platform(source_env: SimpleNamespace) -> None:
    env = source_env
    content = b"platform-owned-original"
    doc = env.knowledge.upload_document(
        env.kb_id,
        actor_user_id=env.actor,
        filename="note.txt",
        content_type="text/plain",
        content=content,
    )
    assert doc.data_source_id is None
    assert document_path(env.kb_id, doc.id, doc.filename).read_bytes() == content
    assert not (env.root / "note.txt").exists()


@pytest.mark.asyncio
async def test_unknown_platform_and_source_uploads_are_archive_only(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    payload = b"opaque source bytes, not searchable"
    platform_doc = env.knowledge.upload_document(
        env.kb_id,
        actor_user_id=env.actor,
        filename="model.unknown",
        content_type="text/plain",
        content=payload,
    )
    source_doc = env.sources.upload_document(
        env.kb_id,
        env.source.id,
        actor_user_id=env.actor,
        filename="model.unknown",
        path="certificates/model.unknown",
        content=payload,
    )
    assert platform_doc.content_type == "application/octet-stream"
    assert source_doc.content_type == "application/octet-stream"
    assert source_doc.source_path == "certificates/model.unknown"
    assert source_doc.content_hash == document_digest(payload)
    await enqueue_index_document(env.services, env.kb_id, platform_doc.id)
    await enqueue_index_document(env.services, env.kb_id, source_doc.id)
    for doc in (platform_doc, source_doc):
        refreshed = env.services.knowledge_repo.get_document(doc.id)
        assert refreshed.status == "archive_only"
        assert refreshed.content_hash == document_digest(payload)
        assert refreshed.chunk_count == 0
    assert document_path(env.kb_id, platform_doc.id, platform_doc.filename).read_bytes() == payload
    assert (env.root / "certificates" / "model.unknown").read_bytes() == payload


@pytest.mark.asyncio
async def test_dwg_without_libredwg_is_saved_as_archive_only(
    source_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.knowledge.processors import libredwg

    env = source_env
    payload = b"not a real DWG; converter is unavailable"
    monkeypatch.setattr(libredwg, "find_dwgread", lambda: None)
    document = env.knowledge.upload_document(
        env.kb_id,
        actor_user_id=env.actor,
        filename="plan.dwg",
        content_type="application/octet-stream",
        content=payload,
    )
    await enqueue_index_document(env.services, env.kb_id, document.id)
    refreshed = env.services.knowledge_repo.get_document(document.id)
    assert refreshed.status == "archive_only"
    assert refreshed.error_message == "cad_unavailable"
    assert refreshed.content_hash == document_digest(payload)
    assert document_path(env.kb_id, document.id, document.filename).read_bytes() == payload


@pytest.mark.asyncio
async def test_image_upload_with_ocr_disabled_is_kept_archive_only(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    env.services.settings_repo.set("knowledge_ocr_enabled", "false")
    payload = b"not a valid image, OCR is disabled and must not run"
    document = env.knowledge.upload_document(
        env.kb_id,
        actor_user_id=env.actor,
        filename="scan.png",
        content_type="image/png",
        content=payload,
    )
    await enqueue_index_document(env.services, env.kb_id, document.id)
    refreshed = env.services.knowledge_repo.get_document(document.id)
    assert refreshed.status == "archive_only"
    assert refreshed.error_message == "ocr_disabled"
    assert refreshed.content_hash == document_digest(payload)


@pytest.mark.asyncio
async def test_configured_but_failing_dwg_converter_marks_parse_failed(
    source_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace as Namespace

    from octop.infra.knowledge.processors import libredwg

    env = source_env
    payload = b"invalid DWG test fixture"
    monkeypatch.setattr(libredwg, "find_dwgread", lambda: "dwgread.exe")
    monkeypatch.setattr(
        libredwg.subprocess,
        "run",
        lambda *args, **kwargs: Namespace(returncode=1, stderr="invalid drawing", stdout=""),
    )
    document = env.knowledge.upload_document(
        env.kb_id,
        actor_user_id=env.actor,
        filename="broken.dwg",
        content_type="application/octet-stream",
        content=payload,
    )
    with pytest.raises(libredwg.CadProcessingFailed, match="invalid drawing"):
        await enqueue_index_document(env.services, env.kb_id, document.id)
    refreshed = env.services.knowledge_repo.get_document(document.id)
    assert refreshed.status == "failed"
    assert refreshed.error_message
    assert refreshed.content_hash == document_digest(payload)
    assert document_path(env.kb_id, document.id, document.filename).read_bytes() == payload


def test_source_conflict_never_overwrites_existing_original(source_env: SimpleNamespace) -> None:
    env = source_env
    target = env.root / "certificate.txt"
    target.write_bytes(b"existing-original")
    with pytest.raises(OctopError) as error:
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=env.actor,
            filename=target.name,
            content=b"replacement",
        )
    assert error.value.code == ErrorCode.DATA_SOURCE_UPLOAD_CONFLICT
    assert target.read_bytes() == b"existing-original"
    assert env.services.knowledge_repo.count_documents(env.kb_id) == 0
    assert not list(env.root.glob(".octop-upload-*"))


def test_read_only_source_is_hidden_and_refuses_direct_upload(source_env: SimpleNamespace) -> None:
    env = source_env
    env.sources.update_source(
        env.source.id,
        actor_user_id=env.actor,
        folder=FolderSettings(root_path=str(env.root), read_only=True),
    )
    assert env.sources.upload_targets(env.kb_id, actor_user_id=env.actor) == []
    with pytest.raises(OctopError) as error:
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=env.actor,
            filename="new.txt",
            content=b"not-saved",
        )
    assert error.value.code == ErrorCode.DATA_SOURCE_READ_ONLY
    assert not (env.root / "new.txt").exists()


def test_folder_acl_filters_targets_and_blocks_direct_upload(source_env: SimpleNamespace) -> None:
    env = source_env
    user = env.services.user_repo.create(username="member", password_hash="unused", role="user")
    acl = ResourceAclRepo(env.services.db)
    acl.upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=env.kb_id,
            owner_user_id=None,
            visibility="public",
            unit_key=None,
            version=1,
            permission="write",
        )
    )
    folder = env.services.knowledge_repo.ensure_folder(
        env.kb_id,
        f"{env.source.id}/certificates",
        data_source_id=env.source.id,
    )
    acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=folder.id,
            owner_user_id=env.actor,
            visibility="private",
            unit_key=None,
            version=1,
            permission="write",
        )
    )
    targets = env.sources.upload_targets(env.kb_id, actor_user_id=user)
    assert targets[0].folders == [""]
    with pytest.raises(PermissionError):
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=user,
            filename="private.txt",
            path="certificates/private.txt",
            content=b"denied",
        )
    assert not (env.root / "certificates" / "private.txt").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "file.txt:stream",
        ".octop-upload-hidden.txt",
    ],
)
def test_upload_refuses_unsafe_paths(source_env: SimpleNamespace, path: str) -> None:
    env = source_env
    with pytest.raises(OctopError) as error:
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=env.actor,
            filename="safe.txt",
            path=path,
            content=b"denied",
        )
    assert error.value.code == ErrorCode.DATA_SOURCE_UPLOAD_INVALID
    assert env.services.knowledge_repo.count_documents(env.kb_id) == 0
    assert not (env.root.parent / "outside.txt").exists()


def test_excluded_upload_does_not_save_an_unsearchable_original(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    env.sources.update_source(
        env.source.id,
        actor_user_id=env.actor,
        folder=FolderSettings(
            root_path=str(env.root), read_only=False, exclude_globs="certificates"
        ),
    )
    with pytest.raises(OctopError) as error:
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=env.actor,
            filename="new.txt",
            path="certificates/new.txt",
            content=b"excluded",
        )
    assert error.value.code == ErrorCode.DATA_SOURCE_UPLOAD_INVALID
    assert not (env.root / "certificates" / "new.txt").exists()


def test_document_limit_rolls_back_staging_before_original_is_published(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    env.services.knowledge_repo.update_base(env.kb_id, max_documents=1)
    env.sources.upload_document(
        env.kb_id,
        env.source.id,
        actor_user_id=env.actor,
        filename="first.txt",
        content=b"first",
    )
    with pytest.raises(ValueError):
        env.sources.upload_document(
            env.kb_id,
            env.source.id,
            actor_user_id=env.actor,
            filename="second.txt",
            content=b"second",
        )
    assert (env.root / "first.txt").read_bytes() == b"first"
    assert not (env.root / "second.txt").exists()
    assert not list(env.root.glob(".octop-upload-*"))
    assert env.services.knowledge_repo.count_documents(env.kb_id) == 1


def test_concurrent_uploads_keep_one_original_and_one_document(source_env: SimpleNamespace) -> None:
    env = source_env

    def upload(content: bytes) -> bytes | ErrorCode:
        try:
            env.sources.upload_document(
                env.kb_id,
                env.source.id,
                actor_user_id=env.actor,
                filename="same.txt",
                content=content,
            )
            return content
        except OctopError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(upload, [b"first", b"second"]))
    winners = [item for item in outcomes if isinstance(item, bytes)]
    assert len(winners) == 1
    assert (env.root / "same.txt").read_bytes() == winners[0]
    assert ErrorCode.DATA_SOURCE_UPLOAD_CONFLICT in outcomes
    assert env.services.knowledge_repo.count_documents(env.kb_id) == 1


async def test_parse_failure_keeps_source_original_and_failure_state(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    doc = env.sources.upload_document(
        env.kb_id,
        env.source.id,
        actor_user_id=env.actor,
        filename="empty.txt",
        content=b"",
    )
    with pytest.raises(ValueError, match="no extractable text"):
        await enqueue_index_document(env.services, env.kb_id, doc.id)
    assert (env.root / "empty.txt").read_bytes() == b""
    current = env.services.knowledge_repo.get_document(doc.id)
    assert current.status == "failed"
    assert not document_path(env.kb_id, doc.id, doc.filename).exists()


def test_upload_permission_does_not_allow_retargeting_a_writable_source(
    source_env: SimpleNamespace,
    tmp_path: Path,
) -> None:
    env = source_env
    user = env.services.user_repo.create(username="writer", password_hash="unused", role="user")
    ResourceAclRepo(env.services.db).upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=env.kb_id,
            owner_user_id=None,
            visibility="public",
            unit_key=None,
            version=1,
            permission="write",
        )
    )
    env.sources.upload_document(
        env.kb_id,
        env.source.id,
        actor_user_id=user,
        filename="allowed.txt",
        content=b"allowed",
    )
    assert (env.root / "allowed.txt").read_bytes() == b"allowed"
    other = tmp_path / "other-directory"
    other.mkdir()
    with pytest.raises(PermissionError):
        env.sources.update_source(
            env.source.id,
            actor_user_id=user,
            folder=FolderSettings(root_path=str(other), read_only=False),
        )
    assert env.services.data_sources_repo.get(env.source.id).root_path == str(env.root)
    with pytest.raises(PermissionError):
        env.sources.create(
            env.kb_id,
            actor_user_id=user,
            name="Unapproved write access",
            kind="local",
            folder=FolderSettings(root_path=str(other), read_only=False),
        )
    readonly = env.sources.create(
        env.kb_id,
        actor_user_id=env.actor,
        name="Read-only",
        kind="local",
        folder=FolderSettings(root_path=str(other)),
    )
    with pytest.raises(PermissionError):
        env.sources.update_source(
            readonly.id,
            actor_user_id=user,
            folder=FolderSettings(root_path=str(other), read_only=False),
        )
    assert env.services.data_sources_repo.get(readonly.id).read_only is True
