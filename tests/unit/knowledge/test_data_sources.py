"""Unit tests for knowledge-base data sources.

The ``upload`` sync runs the real document pipeline (parse → chunk → index);
only the embedding backend call is stubbed, because a unit test cannot download
an ONNX model. Everything else — file bytes, chunk rows in the per-base index,
document status — is produced and asserted for real.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import jobs as jobs_module
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.data_sources import (
    DataSourceService,
    DataSourceSyncFailed,
    DataSourceSyncUnsupported,
)
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.utils.paths import PathLayout

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


def test_url_and_connector_sync_refuse_instead_of_reporting_success(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
    base = env.services.knowledge_repo.create_base(owner_user_id=people.owner, name="Docs")
    url_source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Site",
        kind="url",
        config={"url": "https://example.com/handbook"},
    )
    connector_source = env.sources.create(
        base.id,
        actor_user_id=people.owner,
        name="Feishu",
        kind="connector",
        config={"connector_id": "cn1234"},
    )

    for source, kind in ((url_source, "url"), (connector_source, "connector")):
        with pytest.raises(DataSourceSyncUnsupported) as raised:
            env.sources.sync(source.id, actor_user_id=people.owner)
        assert raised.value.kind == kind
        row = env.services.data_sources_repo.get(source.id)
        assert row.sync_status == "failed"
        assert kind in (row.sync_error or "")
        assert row.last_synced_at is None


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


def test_read_access_follows_resource_acl(
    env: SimpleNamespace, people: SimpleNamespace
) -> None:
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
