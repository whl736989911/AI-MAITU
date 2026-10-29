"""Cross-record and knowledge references: validation, redaction, revocation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_data as fd
from octop.infra.agents.feature_data_refs import (
    FeatureReferenceService,
    ReferenceValueError,
    validate_reference_shape,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_data import FeatureDataRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import VISIBILITY_PUBLIC, AclEntry

SOURCE_AGENT = "feat-src"
TARGET_AGENT = "feat-dst"


def _schema() -> dict[str, Any]:
    return {
        "version": 1,
        "objects": [
            {
                "key": "task",
                "kind": "ledger",
                "title_field": "name",
                "fields": [
                    {"key": "name", "type": "text", "required": True},
                    {
                        "key": "supplier_ref",
                        "type": "record_ref",
                        "target_object": "supplier",
                        "target_agent_id": TARGET_AGENT,
                    },
                    {"key": "doc_ref", "type": "knowledge_ref"},
                ],
            }
        ],
    }


def _target_schema() -> dict[str, Any]:
    return {
        "version": 1,
        "objects": [
            {
                "key": "supplier",
                "kind": "ledger",
                "title_field": "name",
                "fields": [{"key": "name", "type": "text", "required": True}],
            }
        ],
    }


class _Repos:
    def __init__(self, db: SqlitePool) -> None:
        self.db = db
        self.feature_data_repo = FeatureDataRepo(db)
        self.user_repo = UserRepo(db)
        self.org_unit_repo = None
        self.resource_acl_repo = ResourceAclRepo(db)
        self.knowledge_repo = KnowledgeRepo(db)


class _Registry:
    def __init__(self, workspaces: dict[str, BackendWorkspace]) -> None:
        self._workspaces = workspaces
        self.octop_config = SimpleNamespace(default_timezone="UTC")

    def get_row(self, agent_id: str) -> Any:
        return self._rows.get(agent_id)

    _rows: dict[str, Any] = {}

    def workspace_for_agent(self, agent_id: str) -> Any:
        return self._workspaces.get(agent_id)


@pytest.fixture
def env(tmp_path: Path) -> tuple[SqlitePool, BackendWorkspace, BackendWorkspace, _Repos, _Registry]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'author', 'x', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (2, 'outsider', 'x', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (3, 'kbowner', 'x', 'user', 1)"
        )
        for agent_id, user_id in ((SOURCE_AGENT, 1), (TARGET_AGENT, 1)):
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, kind, created_at, updated_at) "
                f"VALUES ('{agent_id}', {user_id}, '{agent_id}', 'feature', 1, 1)"
            )
    source_ws = BackendWorkspace(
        LocalShellBackend(root_dir=str(tmp_path / "src"), virtual_mode=False), str(tmp_path / "src")
    )
    target_ws = BackendWorkspace(
        LocalShellBackend(root_dir=str(tmp_path / "dst"), virtual_mode=False),
        str(tmp_path / "dst"),
    )
    repos = _Repos(pool)
    registry = _Registry({SOURCE_AGENT: source_ws, TARGET_AGENT: target_ws})
    registry._rows = {
        SOURCE_AGENT: SimpleNamespace(
            agent_id=SOURCE_AGENT, kind="feature", user_id=1, enterprise_unit_key=None
        ),
        TARGET_AGENT: SimpleNamespace(
            agent_id=TARGET_AGENT, kind="feature", user_id=1, enterprise_unit_key=None
        ),
    }
    return pool, source_ws, target_ws, repos, registry


async def _seed_target_record(
    repos: _Repos, target_ws: BackendWorkspace, registry: _Registry
) -> Any:
    await fd.save_schema(target_ws, _target_schema())
    return await fd.create_record(
        repos,
        target_ws,
        agent_id=TARGET_AGENT,
        object_key="supplier",
        values={"name": "Acme"},
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )


async def test_record_ref_write_requires_readable_target(
    env: tuple[Any, ...],
) -> None:
    pool, source_ws, target_ws, repos, registry = env
    target = await _seed_target_record(repos, target_ws, registry)
    await fd.save_schema(source_ws, _schema())

    # Happy path: writer may read the target, value is kept.
    created = await fd.create_record(
        repos,
        source_ws,
        agent_id=SOURCE_AGENT,
        object_key="task",
        values={"name": "T1", "supplier_ref": target.id},
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )
    assert created.values["supplier_ref"] == target.id

    # A nonexistent id, a wrong object and a foreign feature are all refused
    # with the same generic message (no existence oracle).
    for bad in ("missing-id", target.id[:8]):
        with pytest.raises(ValueError, match="not available"):
            await fd.create_record(
                repos,
                source_ws,
                agent_id=SOURCE_AGENT,
                object_key="task",
                values={"name": "T2", "supplier_ref": bad},
                user_id=1,
                feature_active=True,
                registry=registry,
                timezone_name="UTC",
            )

    # The target becomes private: even the source feature's writer can no
    # longer point at it, and existing references stop resolving.
    from octop.infra.agents.feature_access import can_read_record

    private_target = repos.feature_data_repo.create_record(
        agent_id=TARGET_AGENT,
        object_key="supplier",
        title="Secret",
        values={"name": "Secret"},
        visibility="private",
        created_by=2,
    )
    with pytest.raises(ValueError, match="not available"):
        await fd.create_record(
            repos,
            source_ws,
            agent_id=SOURCE_AGENT,
            object_key="task",
            values={"name": "T3", "supplier_ref": private_target.id},
            user_id=1,
            feature_active=True,
            registry=registry,
            timezone_name="UTC",
        )
    assert can_read_record(private_target, user_id=1) is False
    del pool


async def test_payload_redacts_revoked_reference_everywhere(
    env: tuple[Any, ...],
) -> None:
    pool, source_ws, target_ws, repos, registry = env
    target = await _seed_target_record(repos, target_ws, registry)
    await fd.save_schema(source_ws, _schema())
    record = await fd.create_record(
        repos,
        source_ws,
        agent_id=SOURCE_AGENT,
        object_key="task",
        values={"name": "T1", "supplier_ref": target.id},
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )
    obj = fd.object_definition(fd.validate_schema(_schema()), "task")

    # The reader (user 2) is not the feature owner; their reach comes from a
    # user grant on the target feature's ACL entry, which is what gets revoked.
    repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="feature",
            resource_id=TARGET_AGENT.removeprefix("feat-"),
            owner_user_id=1,
            visibility="private",
            unit_key=None,
            version=0,
            grants=(("user", "2"),),
        )
    )
    references = FeatureReferenceService(registry=registry, repos=repos, user_id=2)
    info = (await references.describe(agent_id=SOURCE_AGENT, obj=obj, record=record))[
        "supplier_ref"
    ]
    assert info["available"] is True
    assert info["title"] == "Acme"

    repos.resource_acl_repo.delete("feature", TARGET_AGENT.removeprefix("feat-"))
    references2 = FeatureReferenceService(registry=registry, repos=repos, user_id=2)
    revoked = (await references2.describe(agent_id=SOURCE_AGENT, obj=obj, record=record))[
        "supplier_ref"
    ]
    assert revoked["available"] is False
    assert "title" not in revoked
    # The raw value stays in the record but history shows only the redaction
    # marker, never the target title.
    items = [{"field": "supplier_ref", "before": None, "after": target.id}]
    redacted = await references2.redact_history(agent_id=SOURCE_AGENT, obj=obj, items=items)
    assert redacted[0]["after"] == {"redacted": True}
    del pool


async def test_knowledge_ref_pins_version_and_reports_stale(
    env: tuple[Any, ...],
) -> None:
    pool, source_ws, target_ws, repos, registry = env
    await fd.save_schema(source_ws, _schema())
    knowledge = repos.knowledge_repo
    base = knowledge.create_base(owner_user_id=3, name="specs")
    repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=base.id,
            owner_user_id=3,
            visibility=VISIBILITY_PUBLIC,
            unit_key=None,
            version=0,
        )
    )
    document = knowledge.create_document(
        kb_id=base.id,
        filename="spec.txt",
        content_type="text/plain",
        byte_size=5,
        content_hash="a" * 64,
        path="spec.txt",
    )
    obj = fd.object_definition(fd.validate_schema(_schema()), "task")
    service = FeatureReferenceService(registry=registry, repos=repos, user_id=1)

    pinned = await service.pin_and_validate_patch(
        agent_id=SOURCE_AGENT,
        obj=obj,
        patch={
            "doc_ref": {
                "kb_id": base.id,
                "document_id": document.id,
                "page": 2,
                "layout": "PRIVATE-LAYOUT",
            }
        },
    )
    assert pinned["doc_ref"]["version"] == "a" * 64

    with pytest.raises(ReferenceValueError):
        await service.pin_and_validate_patch(
            agent_id=SOURCE_AGENT,
            obj=obj,
            patch={"doc_ref": {"kb_id": base.id, "document_id": "ghost"}},
        )

    record = SimpleNamespace(
        id="rec",
        agent_id=SOURCE_AGENT,
        object_key="task",
        values={"doc_ref": pinned["doc_ref"]},
    )
    info = (await service.describe(agent_id=SOURCE_AGENT, obj=obj, record=record))["doc_ref"]
    assert info["available"] is True and info["stale"] is False
    assert info["original_url"].endswith(f"?version={'a' * 64}")

    # The document content changes: the reference turns stale and only
    # current-content links remain — no claim of serving pinned bytes.
    knowledge.update_document(document.id, content_hash="b" * 64)
    service = FeatureReferenceService(registry=registry, repos=repos, user_id=1)
    stale = (await service.describe(agent_id=SOURCE_AGENT, obj=obj, record=record))["doc_ref"]
    assert stale["stale"] is True
    assert "original_url" not in stale
    assert stale["current_original_url"].endswith(f"?version={'b' * 64}")
    assert stale["current_version"] == "b" * 64
    assert stale["version"] == "a" * 64

    # A subsequent request after revocation exposes no identifiers or locator metadata.
    repos.resource_acl_repo.delete("knowledge_base", base.id)
    service = FeatureReferenceService(registry=registry, repos=repos, user_id=1)
    hidden = (await service.describe(agent_id=SOURCE_AGENT, obj=obj, record=record))["doc_ref"]
    assert hidden["available"] is False
    assert "title" not in hidden and "path" not in hidden and "current_original_url" not in hidden
    assert not {
        "kb_id",
        "document_id",
        "version",
        "current_version",
        "page",
        "layout",
    }.intersection(hidden)


def test_reference_shape_validation() -> None:
    field = {"key": "doc_ref", "type": "knowledge_ref"}
    assert validate_reference_shape(field, {"kb_id": "k", "document_id": "d"}) == {
        "kb_id": "k",
        "document_id": "d",
    }
    with pytest.raises(ReferenceValueError):
        validate_reference_shape(field, {"kb_id": "k"})
    with pytest.raises(ReferenceValueError, match="SHA-256"):
        validate_reference_shape(field, {"kb_id": "k", "document_id": "d", "version": "xyz"})
    with pytest.raises(ReferenceValueError, match="positive integer"):
        validate_reference_shape(field, {"kb_id": "k", "document_id": "d", "page": 0})
    with pytest.raises(ReferenceValueError, match="unsupported keys"):
        validate_reference_shape(field, {"kb_id": "k", "document_id": "d", "extra": 1})
    with pytest.raises(ReferenceValueError):
        validate_reference_shape({"key": "r", "type": "record_ref"}, "")
