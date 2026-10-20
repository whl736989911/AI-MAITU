"""Feature business data schema, records, history, attachments and reminders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_data as fd
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_data import FeatureDataRepo
from octop.infra.errors import OctopError


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'author', 'x', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at, kind) "
            "VALUES ('feat-supplier', 1, 'supplier', 1, 1, 'feature')"
        )
    return pool


@pytest.fixture
def workspace(tmp_path: Path) -> BackendWorkspace:
    root = tmp_path / "workspace"
    root.mkdir()
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), str(root))


def _schema() -> dict[str, Any]:
    return {
        "version": 1,
        "objects": [
            {
                "key": "supplier",
                "name": {"zh": "供应商", "en": "Supplier"},
                "kind": "ledger",
                "title_field": "name",
                "fields": [
                    {"key": "name", "type": "text", "required": True},
                    {"key": "expires_at", "type": "date", "reminder_days": [30, 1]},
                    {"key": "license", "type": "file"},
                    {"key": "status", "type": "enum", "options": ["approved", "suspended"]},
                ],
            },
            {
                "key": "controlled_doc",
                "name": "受控文件",
                "kind": "document",
                "controlled": True,
                "title_field": "title",
                "fields": [
                    {"key": "title", "type": "text", "required": True},
                    {"key": "version", "type": "text", "required": True},
                    {
                        "key": "status",
                        "type": "enum",
                        "options": ["draft", "effective", "obsolete"],
                    },
                    {"key": "file", "type": "file"},
                ],
            },
        ],
    }


async def test_schema_validation_and_controlled_document_rule() -> None:
    schema = fd.validate_schema(_schema())
    assert {obj["key"] for obj in schema["objects"]} == {"supplier", "controlled_doc"}
    with pytest.raises(ValueError, match="controlled"):
        fd.validate_schema(
            {
                "version": 1,
                "objects": [
                    {
                        "key": "bad",
                        "kind": "ledger",
                        "controlled": True,
                        "fields": [{"key": "name", "type": "text"}],
                    }
                ],
            }
        )
    with pytest.raises(ValueError, match="file field"):
        fd.validate_schema(
            {
                "version": 1,
                "objects": [
                    {
                        "key": "doc",
                        "kind": "document",
                        "controlled": True,
                        "fields": [{"key": "name", "type": "text"}],
                    }
                ],
            }
        )


async def test_record_lifecycle_history_visibility_and_reminders(
    db: SqlitePool, workspace: BackendWorkspace
) -> None:
    repo = FeatureDataRepo(db)
    schema = await fd.save_schema(workspace, _schema())
    assert schema["version"] == 1
    record = await fd.create_record(
        repo,
        workspace,
        agent_id="feat-supplier",
        object_key="supplier",
        values={"name": "Acme", "expires_at": "2030-01-01", "status": "approved"},
        user_id=1,
        feature_active=False,
    )
    assert record.visibility == "private"
    assert record.title == "Acme"
    assert (
        repo.list_records(agent_id="feat-supplier", object_key="supplier", user_id=1)[0][0].id
        == record.id
    )
    assert repo.list_records(agent_id="feat-supplier", object_key="supplier", user_id=2)[0] == []
    assert repo.list_reminders(record.id)
    updated = await fd.update_record(
        repo,
        workspace,
        agent_id="feat-supplier",
        record_id=record.id,
        values={"name": "Acme Ltd", "status": "suspended"},
        expected_revision=1,
        user_id=1,
    )
    assert updated.revision == 2
    assert updated.title == "Acme Ltd"
    with pytest.raises(OctopError):
        await fd.update_record(
            repo,
            workspace,
            agent_id="feat-supplier",
            record_id=record.id,
            values={"name": "stale"},
            expected_revision=1,
            user_id=1,
        )
    history = repo.list_changes(record.id)
    assert [change.kind for change in history] == ["update", "create"]
    assert history[0].items[0]["field"] == "name"

    published = fd.publish_record(repo, agent_id="feat-supplier", record_id=record.id, user_id=1)
    assert published.visibility == "feature"


async def test_attachment_versions_are_stored_in_workspace(
    db: SqlitePool, workspace: BackendWorkspace
) -> None:
    repo = FeatureDataRepo(db)
    await fd.save_schema(workspace, _schema())
    record = await fd.create_record(
        repo,
        workspace,
        agent_id="feat-supplier",
        object_key="supplier",
        values={"name": "Acme"},
        user_id=1,
        feature_active=True,
    )
    first = await fd.add_file(
        repo,
        workspace,
        agent_id="feat-supplier",
        record_id=record.id,
        field_key="license",
        filename="license.pdf",
        content_type="application/pdf",
        data=b"first",
        note="initial",
        user_id=1,
    )
    second = await fd.add_file(
        repo,
        workspace,
        agent_id="feat-supplier",
        record_id=record.id,
        field_key="license",
        filename="license-2.pdf",
        content_type="application/pdf",
        data=b"second",
        note="renewed",
        user_id=1,
    )
    assert (first.version, second.version) == (1, 2)
    files = repo.list_files(record.id)
    assert [file.current for file in files] == [True, False]
    assert (await workspace.adownload_bytes(second.storage_path)) == b"second"
    assert (await workspace.adownload_bytes(first.storage_path)) == b"first"


async def test_invalid_values_and_unconfigured_schema_are_refused(
    db: SqlitePool, workspace: BackendWorkspace
) -> None:
    repo = FeatureDataRepo(db)
    with pytest.raises(OctopError):
        await fd.create_record(
            repo,
            workspace,
            agent_id="feat-supplier",
            object_key="supplier",
            values={"name": "Acme"},
            user_id=1,
            feature_active=False,
        )
    await fd.save_schema(workspace, _schema())
    with pytest.raises(ValueError, match="required"):
        fd.validate_values(
            fd.object_definition(fd.validate_schema(_schema()), "supplier"), {"status": "approved"}
        )
    with pytest.raises(ValueError, match="must be one of"):
        fd.validate_values(
            fd.object_definition(fd.validate_schema(_schema()), "supplier"),
            {"name": "Acme", "status": "nope"},
        )


async def test_schema_roundtrip_uses_workspace_document(
    workspace: BackendWorkspace,
) -> None:
    loaded = await fd.load_schema(workspace)
    assert loaded.schema is None and loaded.error is None
    await fd.save_schema(workspace, _schema())
    loaded = await fd.load_schema(workspace)
    assert loaded.schema is not None
    assert loaded.schema["objects"][0]["key"] == "supplier"
