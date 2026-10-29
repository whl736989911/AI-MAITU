"""Central feature access policy and the atomic responsible-user handover."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.agents.feature_access import (
    can_read_record,
    can_use_feature,
    can_write_record,
    effective_writer,
    is_feature_manager,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_data import FeatureDataRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import VISIBILITY_UNIT, AclEntry


def _feature_row(agent_id: str = "feat-ac", *, user_id: int | None = 1, unit: str | None = None):
    return SimpleNamespace(
        agent_id=agent_id, kind="feature", user_id=user_id, enterprise_unit_key=unit
    )


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        # 1 creator, 2 colleague, 3 enterprise admin (owning enterprise),
        # 4 enterprise admin (another enterprise), 5 new responsible user.
        for user_id, unit in (
            (1, "ent-a/sales"),
            (2, "ent-a/sales"),
            (3, "ent-a"),
            (4, "ent-b"),
            (5, "ent-a/sales"),
        ):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, org_unit, created_at) "
                "VALUES (?, ?, 'x', 'user', ?, 1)",
                (user_id, f"u{user_id}", unit),
            )
        conn.execute("UPDATE users SET role = 'enterprise_admin' WHERE id IN (3, 4)")
        for key, parent in (("ent-a", None), ("ent-a/sales", "ent-a"), ("ent-b", None)):
            conn.execute(
                "INSERT INTO org_units(key, label_zh, label_en, parent_key, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, 0, 1)",
                (key, key, key, parent),
            )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, kind, enterprise_unit_key, created_at, updated_at) "
            "VALUES ('feat-ac', 1, 'ac', 'feature', 'ent-a', 1, 1)"
        )
    return pool


def _repos(db: SqlitePool) -> tuple[UserRepo, OrgUnitRepo]:
    return UserRepo(db), OrgUnitRepo(db)


def test_published_feature_manager_spans_enterprise_but_not_private_drafts(db: SqlitePool) -> None:
    user_repo, org_unit_repo = _repos(db)
    row = _feature_row(unit="ent-a")

    assert is_feature_manager(row, user_id=3, user_repo=user_repo, org_unit_repo=org_unit_repo)
    assert not is_feature_manager(row, user_id=4, user_repo=user_repo, org_unit_repo=org_unit_repo)
    assert not is_feature_manager(row, user_id=2, user_repo=user_repo, org_unit_repo=org_unit_repo)
    assert not is_feature_manager(row, user_id=1, user_repo=user_repo, org_unit_repo=org_unit_repo)

    # A private draft is creator-only — the enterprise manager has no read,
    # no matter the manager reach the published feature grants.
    draft = SimpleNamespace(id="r1", visibility="private", created_by=2, responsible_user_id=None)
    assert can_read_record(draft, user_id=2)
    assert not can_read_record(draft, user_id=3)
    assert not can_write_record(
        draft, row, user_id=3, user_repo=user_repo, org_unit_repo=org_unit_repo
    )


def test_unpublished_draft_stays_with_its_author(db: SqlitePool) -> None:
    user_repo, org_unit_repo = _repos(db)
    row = _feature_row(unit=None)
    assert is_feature_manager(row, user_id=1, user_repo=user_repo, org_unit_repo=org_unit_repo)
    assert not is_feature_manager(row, user_id=3, user_repo=user_repo, org_unit_repo=org_unit_repo)


def _record(record_id: str, *, visibility: str, created_by: int, responsible: int | None) -> Any:
    return SimpleNamespace(
        id=record_id,
        visibility=visibility,
        created_by=created_by,
        responsible_user_id=responsible,
    )


def test_effective_writer_and_shared_record_writes(db: SqlitePool) -> None:
    user_repo, org_unit_repo = _repos(db)
    row = _feature_row(unit="ent-a")
    legacy = _record("r1", visibility="feature", created_by=2, responsible=None)
    handed = _record("r2", visibility="feature", created_by=2, responsible=5)

    assert effective_writer(legacy) == 2
    assert effective_writer(handed) == 5
    # The new responsible user may write; the original creator of a handed-over
    # record is audit history, not a standing writer; a colleague may not.
    assert can_write_record(
        handed, row, user_id=5, user_repo=user_repo, org_unit_repo=org_unit_repo
    )
    assert not can_write_record(
        handed, row, user_id=2, user_repo=user_repo, org_unit_repo=org_unit_repo
    )
    assert not can_write_record(
        legacy, row, user_id=5, user_repo=user_repo, org_unit_repo=org_unit_repo
    )
    # The creator fallback keeps legacy creators in charge of their records.
    assert can_write_record(
        legacy, row, user_id=2, user_repo=user_repo, org_unit_repo=org_unit_repo
    )


def test_handover_moves_only_shared_records_and_keeps_audit(db: SqlitePool) -> None:
    repo = FeatureDataRepo(db)
    shared = repo.create_record(
        agent_id="feat-ac",
        object_key="ledger",
        title="s1",
        values={"a": 1},
        visibility="feature",
        created_by=1,
    )
    private = repo.create_record(
        agent_id="feat-ac",
        object_key="ledger",
        title="p1",
        values={"a": 2},
        visibility="private",
        created_by=1,
    )
    other_object = repo.create_record(
        agent_id="feat-ac",
        object_key="docs",
        title="d1",
        values={"a": 3},
        visibility="feature",
        created_by=1,
    )
    repo.add_change(
        record_id=shared.id,
        agent_id="feat-ac",
        user_id=1,
        kind="create",
        summary="created",
        items=[{"field": "a", "before": None, "after": 1}],
    )

    moved = repo.handover_records(
        agent_id="feat-ac",
        from_user_id=1,
        to_user_id=5,
        actor_user_id=3,
        object_key="ledger",
    )

    assert moved == [shared.id]
    after_shared = repo.get_record(shared.id)
    after_private = repo.get_record(private.id)
    untouched = repo.get_record(other_object.id)
    assert after_shared is not None and after_private is not None and untouched is not None
    assert after_shared.responsible_user_id == 5
    assert after_shared.created_by == 1, "creator audit must be preserved"
    assert after_shared.revision == shared.revision + 1
    assert after_private.responsible_user_id == 1, "private drafts are never taken over"
    assert untouched.responsible_user_id == 1
    changes = repo.list_changes(shared.id)
    assert {change.kind for change in changes} == {"handover", "create"}
    handover = next(change for change in changes if change.kind == "handover")
    assert handover.user_id == 3, "the acting manager owns the handover audit row"

    # A second handover of the same scope moves nothing — the source no
    # longer holds the records.
    assert (
        repo.handover_records(
            agent_id="feat-ac", from_user_id=1, to_user_id=2, actor_user_id=3, object_key="ledger"
        )
        == []
    )
    # And the new responsible user can be moved again by matching them.
    assert repo.handover_records(
        agent_id="feat-ac", from_user_id=5, to_user_id=2, actor_user_id=3
    ) == [shared.id]


def test_can_use_feature_union_and_owner_shortcut(db: SqlitePool) -> None:
    user_repo, org_unit_repo = _repos(db)
    scope = {
        "acl_repo": ResourceAclRepo(db),
        "user_repo": user_repo,
        "org_unit_repo": org_unit_repo,
    }
    acl = scope["acl_repo"]
    row = _feature_row()
    # The feature's owner passes without any ACL row.
    assert can_use_feature(row, user_id=1, **scope)
    assert not can_use_feature(row, user_id=2, **scope)
    acl.upsert(
        AclEntry(
            resource_type="feature",
            resource_id="ac",
            owner_user_id=1,
            visibility=VISIBILITY_UNIT,
            unit_key="ent-a/sales",
            version=0,
        )
    )
    assert can_use_feature(row, user_id=2, **scope)
    assert not can_use_feature(row, user_id=4, **scope)


async def test_enterprise_manager_tools_use_real_turn_identity_and_keep_drafts_private(
    db: SqlitePool, tmp_path: Path
) -> None:
    import json

    from deepagents.backends.local_shell import LocalShellBackend
    from harness_agent.backends.workspace import BackendWorkspace

    from octop.infra.agents import feature_data as fd
    from octop.infra.agents.feature_data_tools import build_feature_data_tools
    from octop.infra.db.services import RepoBundle

    root = tmp_path / "workspace"
    root.mkdir()
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(root), virtual_mode=False), str(root)
    )
    repos = RepoBundle.from_pool(db)
    row = _feature_row(user_id=None, unit="ent-a")
    registry = SimpleNamespace(
        get_row=lambda agent_id: row if agent_id == row.agent_id else None,
        workspace_for_agent=lambda agent_id: workspace if agent_id == row.agent_id else None,
        octop_config=SimpleNamespace(default_timezone="America/New_York"),
    )
    repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="agent",
            resource_id=row.agent_id,
            owner_user_id=1,
            visibility="private",
            unit_key="ent-a",
            version=0,
        )
    )
    await fd.save_schema(
        workspace,
        {
            "version": 1,
            "objects": [
                {
                    "key": "ledger",
                    "kind": "ledger",
                    "title_field": "name",
                    "fields": [{"key": "name", "type": "text", "required": True}],
                }
            ],
        },
    )
    shared = repos.feature_data_repo.create_record(
        agent_id=row.agent_id,
        object_key="ledger",
        title="Original",
        values={"name": "Original"},
        visibility="feature",
        created_by=1,
    )
    private = repos.feature_data_repo.create_record(
        agent_id=row.agent_id,
        object_key="ledger",
        title="Private",
        values={"name": "Private"},
        visibility="private",
        created_by=1,
    )
    update = next(
        tool
        for tool in build_feature_data_tools(registry=registry, repos=repos)
        if tool.name == "feature_data_update"
    )

    async def rename(record, user_id, name):
        return await update.ainvoke(
            {
                "record_id": record.id,
                "values_json": json.dumps({"name": name}),
                "expected_revision": record.revision,
            },
            config={"configurable": {"agent_id": row.agent_id, "user": user_id}},
        )

    result = await rename(shared, 3, "Managed")
    assert repos.feature_data_repo.get_record(shared.id).values["name"] == "Managed", result
    await rename(private, 3, "Must remain private")
    assert repos.feature_data_repo.get_record(private.id).values["name"] == "Private"
    await rename(repos.feature_data_repo.get_record(shared.id), 4, "Foreign manager")
    assert repos.feature_data_repo.get_record(shared.id).values["name"] == "Managed"
