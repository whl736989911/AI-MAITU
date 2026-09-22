"""Unit tests for knowledge-base data sources.

The ``upload`` sync runs the real document pipeline (parse → chunk → index);
only the embedding backend call is stubbed, because a unit test cannot download
an ONNX model. Everything else — file bytes, chunk rows in the per-base index,
document status — is produced and asserted for real.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import data_sources as data_sources_module
from octop.infra.knowledge import jobs as jobs_module
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.data_sources import (
    DataSourceService,
    DataSourceSyncFailed,
    DataSourceSyncUnsupported,
    FolderSettings,
)
from octop.infra.knowledge.files import document_path
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.knowledge.sources import SourceError, SourceUnsupported
from octop.infra.knowledge.url_fetch import FetchedDocument
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ssrf_guard import OutboundFetchError, UnsafeOutboundUrl

_DOC_TEXT = "# Handbook\n\nRefunds are processed within five business days.\n"


def _fake_embeddings(_services: object, texts: list[str]) -> list[list[float]]:
    return [[float(len(text)), 1.0, 0.0, 0.0] for text in texts]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        config=SimpleNamespace(max_upload_bytes=1_000_000, max_upload_mb=1),
        paths=PathLayout.from_env(),
        knowledge_repo=KnowledgeRepo(pool),
        data_sources_repo=DataSourceRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        secret_repo=SecretRepo(pool),
        audit_repo=AuditRepo(pool),
        provider_repo=None,
    )
    monkeypatch.setattr(service_module, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_module, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_module, "embed_knowledge_texts", _fake_embeddings)
    return SimpleNamespace(
        services=services,
        pool=pool,
        knowledge=KnowledgeService(services),
        sources=DataSourceService(services),
    )


@pytest.fixture
def people(env: SimpleNamespace) -> SimpleNamespace:
    users = env.services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    viewer = users.create(username="viewer", password_hash="h", role="user")
    return SimpleNamespace(owner=owner, viewer=viewer)


def _uploaded_document(env: SimpleNamespace, owner_id: int, *, shared: bool = True):
    """A knowledge base with one real uploaded markdown document."""
    base = env.services.knowledge_repo.create_base(
        owner_user_id=owner_id, name="Docs", shared=shared
    )
    document = env.knowledge.upload_document(
        base.id,
        actor_user_id=owner_id,
        filename="handbook.md",
        content_type="text/markdown",
        content=_DOC_TEXT.encode("utf-8"),
    )
    return base, document


def _chunk_count(base_id: str) -> int:
    path = KnowledgeIndex(base_id).path
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])


def _indexed_text(base_id: str) -> str:
    """Every chunk text in the base's index — what retrieval can actually see."""
    path = KnowledgeIndex(base_id).path
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT text FROM chunks").fetchall()
    return "\n".join(str(row[0]) for row in rows)


def test_upload_source_sync_ingests_the_document(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, document = _uploaded_document(env, people.owner)
    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Handbook",
        kind="upload",
        config={"path": "handbook.md"},
    )

    synced = env.sources.sync(source.id, actor_user_id=people.owner)

    assert synced.sync_status == "ok"
    assert synced.sync_error is None
    assert synced.last_synced_at is not None
    refreshed = env.services.knowledge_repo.get_document(document.id)
    assert refreshed.status == "ready"
    assert refreshed.chunk_count > 0
    assert _chunk_count(base.id) == refreshed.chunk_count


def test_connector_sync_refuses_instead_of_reporting_success(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """A connector source has nothing to pull from: it must say so, not pretend."""
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")
    connector_source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Feishu",
        kind="connector",
        config={"connector_id": "cn1234"},
    )

    with pytest.raises(DataSourceSyncUnsupported) as raised:
        env.sources.sync(connector_source.id, actor_user_id=people.owner)

    assert raised.value.kind == "connector"
    row = env.services.data_sources_repo.get(connector_source.id)
    assert row.sync_status == "failed"
    assert "connector" in (row.sync_error or "")
    assert row.last_synced_at is None


def _url_source(env: SimpleNamespace, owner_id: int, *, url: str = "https://example.com/handbook"):
    base = env.services.knowledge_repo.create_base(owner_user_id=owner_id, name="Docs")
    source = env.sources.create(
        base.id,
        actor_user_id=owner_id,
        name="Handbook",
        kind="url",
        config={"url": url},
    )
    return base, source


def _fetched(body: str, *, filename: str = "Handbook.html", content_type: str = "text/html"):
    return FetchedDocument(
        filename=filename,
        content_type=content_type,
        content=body.encode("utf-8"),
        final_url="https://example.com/handbook",
    )


def test_url_source_sync_ingests_the_fetched_page(
    env: SimpleNamespace, people: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fetched page must end up parsed, chunked, and indexed for real."""
    base, source = _url_source(env, people.owner)
    page = "<h1>Refund policy</h1><p>Refunds are processed within five business days.</p>"
    monkeypatch.setattr(data_sources_module, "fetch_document", lambda *_a, **_k: _fetched(page))

    synced = env.sources.sync(source.id, actor_user_id=people.owner)

    assert synced.sync_status == "ok"
    assert synced.sync_error is None
    assert synced.last_synced_at is not None
    document = env.services.knowledge_repo.get_document(synced.config["document_id"])
    assert document is not None
    assert document.path == "Handbook.html"
    assert document.content_type == "text/html"
    assert document.status == "ready"
    assert document.chunk_count > 0
    assert document_path(base.id, document.id, document.filename).exists()
    assert _chunk_count(base.id) == document.chunk_count
    stored = _indexed_text(base.id)
    assert "Refund policy" in stored
    assert "five business days" in stored


def test_url_sync_refreshes_the_same_document_instead_of_duplicating_it(
    env: SimpleNamespace, people: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, source = _url_source(env, people.owner)
    monkeypatch.setattr(
        data_sources_module, "fetch_document", lambda *_a, **_k: _fetched("<p>old text</p>")
    )
    env.sources.sync(source.id, actor_user_id=people.owner)

    monkeypatch.setattr(
        data_sources_module, "fetch_document", lambda *_a, **_k: _fetched("<p>new rules</p>")
    )
    refreshed = env.sources.sync(source.id, actor_user_id=people.owner)

    documents = [
        row for row in env.services.knowledge_repo.list_documents(base.id) if not row.is_dir
    ]
    assert [row.id for row in documents] == [refreshed.config["document_id"]]
    stored = _indexed_text(base.id)
    assert "new rules" in stored
    assert "old text" not in stored
    assert env.services.knowledge_repo.get_document(documents[0].id).status == "ready"


def test_url_sync_after_the_document_is_deleted_ingests_a_fresh_one(
    env: SimpleNamespace, people: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, source = _url_source(env, people.owner)
    monkeypatch.setattr(
        data_sources_module, "fetch_document", lambda *_a, **_k: _fetched("<p>text</p>")
    )
    first = env.sources.sync(source.id, actor_user_id=people.owner)
    env.knowledge.delete_document(base.id, first.config["document_id"], actor_user_id=people.owner)

    second = env.sources.sync(source.id, actor_user_id=people.owner)

    assert second.sync_status == "ok"
    assert second.config["document_id"] != first.config["document_id"]
    document = env.services.knowledge_repo.get_document(second.config["document_id"])
    assert document is not None
    assert document.status == "ready"
    assert _chunk_count(base.id) == document.chunk_count


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/handbook", "https"),
        ("https://localhost/handbook", "localhost"),
        ("https://127.0.0.1/handbook", "private"),
        ("https://192.168.1.10/handbook", "private"),
        ("https://169.254.169.254/latest/meta-data", "private"),
    ],
)
def test_url_source_creation_refuses_targets_the_guard_rejects(
    env: SimpleNamespace, people: SimpleNamespace, url: str, reason: str
) -> None:
    """SSRF: an internal or plain-http target never becomes a stored source."""
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")

    with pytest.raises(UnsafeOutboundUrl, match=reason):
        env.sources.create(
            base.id, actor_user_id=people.owner, name="SSRF", kind="url", config={"url": url}
        )

    assert env.services.data_sources_repo.list_for_base(base.id) == []


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OutboundFetchError("HTTP 404 Not Found for https://example.com/handbook"), "404"),
        (UnsafeOutboundUrl("private or reserved IP addresses are not allowed"), "private"),
    ],
)
def test_url_sync_failures_are_recorded_and_leave_no_document_behind(
    env: SimpleNamespace,
    people: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    """A failed fetch is a failed sync: no empty document, no 'ok'."""
    base, source = _url_source(env, people.owner)

    def _boom(*_args: object, **_kwargs: object) -> FetchedDocument:
        raise error

    monkeypatch.setattr(data_sources_module, "fetch_document", _boom)

    with pytest.raises(DataSourceSyncFailed) as raised:
        env.sources.sync(source.id, actor_user_id=people.owner)

    assert raised.value.cause is error
    row = env.services.data_sources_repo.get(source.id)
    assert row.sync_status == "failed"
    assert reason in (row.sync_error or "")
    assert row.last_synced_at is None
    assert env.services.knowledge_repo.list_documents(base.id) == []


def test_url_and_upload_configs_reject_fields_their_kind_cannot_use(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """A field this kind cannot honour is an error, not a silently dropped key."""
    base, document = _uploaded_document(env, people.owner)

    with pytest.raises(ValueError, match="config.path"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Site",
            kind="url",
            config={"url": "https://example.com/", "path": "nope.md"},
        )
    with pytest.raises(ValueError, match="config.url"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Handbook",
            kind="upload",
            config={"document_id": document.id, "url": "https://example.com/"},
        )
    with pytest.raises(ValueError, match="config.url"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Feishu",
            kind="connector",
            config={"connector_id": "cn1", "url": "https://example.com/"},
        )


def test_upload_sync_reports_a_failed_ingest(env: SimpleNamespace, people: SimpleNamespace) -> None:
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")
    # A row whose bytes never reached disk: parsing cannot succeed.
    document = env.services.knowledge_repo.create_document(
        kb_id=base.id, filename="missing.md", content_type="text/markdown", byte_size=10
    )
    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Broken",
        kind="upload",
        config={"document_id": document.id},
    )

    with pytest.raises(DataSourceSyncFailed):
        env.sources.sync(source.id, actor_user_id=people.owner)

    row = env.services.data_sources_repo.get(source.id)
    assert row.sync_status == "failed"
    assert row.sync_error
    assert row.last_synced_at is None
    assert env.services.knowledge_repo.get_document(document.id).status == "failed"


def test_reader_can_list_but_may_not_create_delete_or_sync(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, _document = _uploaded_document(env, people.owner)
    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Handbook",
        kind="url",
        config={"url": "https://example.com/handbook"},
    )

    assert [row.id for row in env.sources.list_for_base(base.id, actor_user_id=people.viewer)] == [
        source.id
    ]
    assert env.sources.get(source.id, actor_user_id=people.viewer).id == source.id

    with pytest.raises(PermissionError, match="write"):
        env.sources.create(
            base.id,
            actor_user_id=people.viewer,
            name="Sneaky",
            kind="url",
            config={"url": "https://example.com/other"},
        )
    with pytest.raises(PermissionError, match="write"):
        env.sources.delete(source.id, actor_user_id=people.viewer)
    with pytest.raises(PermissionError, match="write"):
        env.sources.sync(source.id, actor_user_id=people.viewer)
    assert env.services.data_sources_repo.get(source.id) is not None


def test_private_base_is_not_readable_by_another_user(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, _document = _uploaded_document(env, people.owner, shared=False)

    with pytest.raises(PermissionError, match="read"):
        env.sources.list_for_base(base.id, actor_user_id=people.viewer)


def test_read_access_follows_resource_acl(env: SimpleNamespace, people: SimpleNamespace) -> None:
    """The reader's access is the ACL's, not any row-level share flag."""
    base, _document = _uploaded_document(env, people.owner, shared=False)
    acl = ResourceAclRepo(env.pool)
    entry = acl.get("knowledge_base", base.id)
    assert entry is not None
    acl.upsert(replace(entry, visibility="public", version=entry.version + 1))

    assert env.sources.list_for_base(base.id, actor_user_id=people.viewer) == []

    acl.upsert(replace(entry, visibility="private", version=entry.version + 1))
    with pytest.raises(PermissionError, match="read"):
        env.sources.list_for_base(base.id, actor_user_id=people.viewer)


def test_upload_source_needs_a_document_that_exists(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, document = _uploaded_document(env, people.owner)
    other = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Other")
    foreign = env.services.knowledge_repo.create_document(
        kb_id=other.id, filename="other.md", content_type="text/markdown", byte_size=1
    )

    with pytest.raises(ValueError, match="document_id or config.path"):
        env.sources.create(base.id, actor_user_id=people.owner, name="Empty", kind="upload")
    with pytest.raises(ValueError, match="not in this knowledge base"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Elsewhere",
            kind="upload",
            config={"document_id": foreign.id},
        )
    with pytest.raises(ValueError, match="not in this knowledge base"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Missing",
            kind="upload",
            config={"path": "nope.md"},
        )

    stored = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Ok",
        kind="upload",
        config={"path": "handbook.md"},
    )
    # The config is normalised to the resolved document, so a later sync cannot
    # drift onto a different file with the same name.
    assert stored.config["document_id"] == document.id
    assert stored.config["path"] == "handbook.md"


def test_unknown_kind_and_empty_config_are_rejected(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")

    with pytest.raises(ValueError, match="unknown data source kind"):
        env.sources.create(base.id, actor_user_id=people.owner, name="X", kind="ftp")
    with pytest.raises(ValueError, match="config.url"):
        env.sources.create(base.id, actor_user_id=people.owner, name="X", kind="url")
    with pytest.raises(ValueError, match="config.connector_id"):
        env.sources.create(base.id, actor_user_id=people.owner, name="X", kind="connector")
    with pytest.raises(ValueError, match="needs a name"):
        env.sources.create(base.id, actor_user_id=people.owner, name="  ", kind="url")


def test_sync_after_the_document_is_deleted_reports_failure(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base, document = _uploaded_document(env, people.owner)
    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Handbook",
        kind="upload",
        config={"document_id": document.id},
    )
    env.knowledge.delete_document(base.id, document.id, actor_user_id=people.owner)

    with pytest.raises(ValueError, match="not in this knowledge base"):
        env.sources.sync(source.id, actor_user_id=people.owner)

    row = env.services.data_sources_repo.get(source.id)
    assert row.sync_status == "failed"
    assert row.last_synced_at is None


# ---------------------------------------------------------------------------
# folder sources — local / smb / nfs (design §4)
# ---------------------------------------------------------------------------

_SHARE_PASSWORD = "s3cret-share-password"


def _local_root(tmp_path: Path) -> Path:
    root = tmp_path / "folder"
    root.mkdir(parents=True)
    (root / "policy.md").write_text("refunds take five days", encoding="utf-8")
    return root


def _base(env: SimpleNamespace, owner_id: int):
    return env.services.knowledge_repo.create_base(owner_user_id=owner_id, name="Docs")


def _local_source(env: SimpleNamespace, owner_id: int, root: Path, *, name: str = "Policies"):
    base = _base(env, owner_id)
    source = env.sources.create(
        base.id,
        actor_user_id=owner_id,
        name=name,
        kind="local",
        folder=FolderSettings(root_path=str(root)),
    )
    return base, source


def test_local_source_records_its_connection_and_carries_no_secret(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    root = _local_root(tmp_path)

    _base_row, source = _local_source(env, people.owner, root)

    assert source.kind == "local"
    assert source.root_path == str(root)
    # design §4: read-only unless an administrator says otherwise.
    assert source.read_only is True
    assert source.has_credentials is False
    assert source.connection_status == "unknown"
    # The row has no field a secret could sit in, so no payload can leak one.
    assert not hasattr(source, "credentials_enc")
    assert "password" not in asdict(source)


def test_a_source_that_could_not_work_is_refused_at_create(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connector is the check, so the form fails rather than the first scan."""
    home = tmp_path / "octop-home"
    home.mkdir()
    monkeypatch.setenv("OCTOP_HOME", str(home))
    base = _base(env, people.owner)

    with pytest.raises(SourceError, match="platform's own data directory"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="Bad",
            kind="local",
            folder=FolderSettings(root_path=str(home)),
        )

    assert env.services.data_sources_repo.list_for_base(base.id) == []


def test_nfs_source_is_refused_with_a_reason(env: SimpleNamespace, people: SimpleNamespace) -> None:
    """NFS has no connector in this build, so a source that names one is refused."""
    base = _base(env, people.owner)

    with pytest.raises(SourceUnsupported, match="libnfs"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="NAS",
            kind="nfs",
            folder=FolderSettings(server="nas", share="export"),
        )

    assert env.services.data_sources_repo.list_for_base(base.id) == []


def test_folder_source_rejects_config_keys(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    """A folder source keeps its settings in columns; a config key reads nothing."""
    base = _base(env, people.owner)

    with pytest.raises(ValueError, match="does not accept config"):
        env.sources.create(
            base.id,
            actor_user_id=people.owner,
            name="X",
            kind="local",
            config={"server": "somewhere"},
            folder=FolderSettings(root_path=str(_local_root(tmp_path))),
        )


def test_test_connection_reports_the_source_and_audits_it(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    _base_row, source = _local_source(env, people.owner, _local_root(tmp_path))

    detail = env.sources.test_connection(source.id, actor_user_id=people.owner)

    assert "1 item(s)" in detail
    row = env.services.data_sources_repo.get(source.id)
    assert row.connection_status == "ok"
    assert row.connection_error is None
    audits = env.services.audit_repo.query(action="knowledge.source.test")
    assert [entry.target for entry in audits] == [source.id]
    assert audits[0].actor == "owner"


def test_test_connection_records_a_failure(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    """A share that went away is recorded, so the failure outlives the request."""
    root = _local_root(tmp_path)
    _base_row, source = _local_source(env, people.owner, root)
    shutil.rmtree(root)

    with pytest.raises(SourceError):
        env.sources.test_connection(source.id, actor_user_id=people.owner)

    row = env.services.data_sources_repo.get(source.id)
    assert row.connection_status == "failed"
    assert "does not exist" in (row.connection_error or "")
    assert env.services.audit_repo.query(action="knowledge.source.test.failed")


def test_a_viewer_cannot_test_a_source(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    """A test spends the stored credential, so seeing the base is not enough."""
    base, _source = _local_source(env, people.owner, _local_root(tmp_path))
    env.services.knowledge_repo.update_base(base.id, shared=True)

    with pytest.raises(PermissionError):
        env.sources.test_connection(_source.id, actor_user_id=people.viewer)


def test_smb_source_keeps_its_secret_encrypted(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """The blob is stored, the plaintext is not, and the row only says one exists."""
    base = _base(env, people.owner)

    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Share",
        kind="smb",
        folder=FolderSettings(
            server="fileserver",
            share="company",
            root_path="policies",
            username="DOMAIN\\svc",
            password=_SHARE_PASSWORD,
        ),
    )

    assert source.server == "fileserver"
    assert source.share == "company"
    assert source.root_path == "policies"
    assert source.username == "DOMAIN\\svc"
    assert source.has_credentials is True
    blob = env.services.data_sources_repo.get_credentials(source.id)
    assert blob is not None
    assert _SHARE_PASSWORD.encode("utf-8") not in blob


def test_update_replaces_clears_or_keeps_the_secret(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    """``password=None`` keeps, ``\"\"`` clears, anything else replaces."""
    base = _base(env, people.owner)
    source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Share",
        kind="smb",
        folder=FolderSettings(server="fileserver", share="company", password="first"),
    )
    first = env.services.data_sources_repo.get_credentials(source.id)

    kept = env.sources.update_source(
        source.id,
        actor_user_id=people.owner,
        name="Renamed",
        folder=FolderSettings(server="fileserver", share="company"),
    )
    assert kept.name == "Renamed"
    assert env.services.data_sources_repo.get_credentials(source.id) == first

    replaced = env.sources.update_source(
        source.id,
        actor_user_id=people.owner,
        folder=FolderSettings(server="fileserver", share="company", password="second"),
    )
    assert replaced.has_credentials is True
    assert env.services.data_sources_repo.get_credentials(source.id) != first
    # A new credential makes the previous verdict stale.
    assert replaced.connection_status == "unknown"

    cleared = env.sources.update_source(
        source.id,
        actor_user_id=people.owner,
        folder=FolderSettings(server="fileserver", share="company", password=""),
    )
    assert cleared.has_credentials is False
    assert env.services.data_sources_repo.get_credentials(source.id) is None


def test_update_validates_before_it_writes(
    env: SimpleNamespace, people: SimpleNamespace, tmp_path: Path
) -> None:
    _base_row, source = _local_source(env, people.owner, _local_root(tmp_path))

    with pytest.raises(SourceError):
        env.sources.update_source(
            source.id,
            actor_user_id=people.owner,
            folder=FolderSettings(root_path="relative/path"),
        )

    assert env.services.data_sources_repo.get(source.id).root_path == source.root_path
