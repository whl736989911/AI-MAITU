"""Unit tests for the sharing change pipeline — apply, approve/reject, rollback."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import (
    CHANGE_APPLIED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    CHANGE_ROLLED_BACK,
    ResourceAclRepo,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.sharing import AclEntry, can_access
from octop.infra.sharing.service import SharingService


@dataclass(frozen=True)
class World:
    service: SharingService
    acl: ResourceAclRepo
    admin: int
    owner: int
    other: int
    member: int


def entry(
    world: World,
    *,
    visibility: str = "private",
    unit_key: str | None = None,
    grants: tuple[tuple[str, str], ...] = (),
) -> AclEntry:
    return AclEntry(
        resource_type="agent",
        resource_id="a1",
        owner_user_id=world.owner,
        visibility=visibility,
        unit_key=unit_key,
        version=0,
        grants=grants,
    )


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def world(db: SqlitePool) -> World:
    units = OrgUnitRepo(db)
    units.create(key="sales", label_zh="销售", label_en="Sales")
    units.create(key="eng", label_zh="研发", label_en="Engineering")
    users = UserRepo(db)
    return World(
        service=SharingService(db),
        acl=ResourceAclRepo(db),
        admin=users.create(username="admin", password_hash="h", role="admin"),
        owner=users.create(username="owner", password_hash="h", role="user", org_unit="sales"),
        other=users.create(username="other", password_hash="h", role="user", org_unit="eng"),
        member=users.create(username="member", password_hash="h", role="user", org_unit="sales"),
    )


def visible_to(world: World, user_id: int) -> set[str]:
    return world.acl.list_visible_resource_ids_for_user("agent", user_id)


def seed_private(world: World) -> None:
    """Create the ACL row the way the repos do: private, version 1."""
    world.acl.set_visibility("agent", "a1", "private", owner_user_id=world.owner)


# ----------------------------------------------------------------------
# apply_change
# ----------------------------------------------------------------------


def test_grant_change_applies_immediately_and_reaches_the_grantee(world: World) -> None:
    seed_private(world)

    result = world.service.apply_change(
        world.owner,
        "agent",
        "a1",
        entry(world, grants=(("user", str(world.other)),)),
        reason="pairing",
    )

    assert result.status == CHANGE_APPLIED
    assert result.impact_scope == "self"
    assert result.applied is True
    assert result.entry.grants == (("user", str(world.other)),)
    assert "a1" in visible_to(world, world.other)
    assert "a1" not in visible_to(world, world.member)

    change = world.acl.get_change(result.change_id)
    assert change is not None
    assert change.status == CHANGE_APPLIED
    assert change.from_version == 1
    assert change.to_version == 2
    assert world.acl.get("agent", "a1").version == 2
    assert json.loads(change.after_json)["grants"] == [["user", str(world.other)]]


def test_unit_change_applies_immediately_to_that_unit_only(world: World) -> None:
    seed_private(world)

    result = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="unit", unit_key="sales")
    )

    assert result.status == CHANGE_APPLIED
    assert result.impact_scope == "unit"
    assert "a1" in visible_to(world, world.member)
    assert "a1" not in visible_to(world, world.other)


def test_org_wide_change_waits_for_approval_and_stays_unapplied(world: World) -> None:
    seed_private(world)

    result = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public"), reason="publish"
    )

    assert result.status == CHANGE_PENDING
    assert result.impact_scope == "org"
    assert result.applied is False
    assert result.entry.visibility == "private"
    stored = world.acl.get("agent", "a1")
    assert stored is not None
    assert stored.visibility == "private"
    assert stored.version == 1
    assert "a1" not in visible_to(world, world.other)

    change = world.acl.get_change(result.change_id)
    assert change is not None
    assert change.status == CHANGE_PENDING
    assert change.reason == "publish"
    assert json.loads(change.after_json)["visibility"] == "public"


def test_first_change_treats_a_rowless_resource_as_private(world: World) -> None:
    result = world.service.apply_change(
        world.owner, "agent", "ghost", entry(world, visibility="public")
    )

    assert result.impact_scope == "org"
    assert result.status == CHANGE_PENDING
    assert world.acl.get("agent", "ghost") is None


def test_apply_change_refuses_actors_without_ownership(world: World) -> None:
    seed_private(world)

    with pytest.raises(OctopError):
        world.service.apply_change(world.other, "agent", "a1", entry(world, visibility="public"))


def test_apply_change_refuses_unknown_resource_type(world: World) -> None:
    with pytest.raises(ValueError):
        world.service.apply_change(world.owner, "spaceship", "s1", entry(world))


# ----------------------------------------------------------------------
# approve / reject
# ----------------------------------------------------------------------


def test_approve_applies_the_parked_change(world: World) -> None:
    seed_private(world)
    pending = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public")
    )

    world.service.approve(pending.change_id, world.admin)

    stored = world.acl.get("agent", "a1")
    assert stored is not None
    assert stored.visibility == "public"
    assert stored.version == 2
    assert "a1" in visible_to(world, world.other)
    change = world.acl.get_change(pending.change_id)
    assert change is not None
    assert change.status == CHANGE_APPLIED
    assert change.to_version == 2


def test_only_admins_may_approve(world: World) -> None:
    seed_private(world)
    pending = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public")
    )

    with pytest.raises(OctopError):
        world.service.approve(pending.change_id, world.owner)

    stored = world.acl.get("agent", "a1")
    assert stored is not None
    assert stored.visibility == "private"


def test_reject_keeps_the_state_and_records_the_reason(world: World) -> None:
    seed_private(world)
    pending = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public")
    )

    world.service.reject(pending.change_id, world.admin, reason="too early")

    stored = world.acl.get("agent", "a1")
    assert stored is not None
    assert stored.visibility == "private"
    assert "a1" not in visible_to(world, world.other)
    change = world.acl.get_change(pending.change_id)
    assert change is not None
    assert change.status == CHANGE_REJECTED
    assert change.reason == "too early"

    with pytest.raises(ValueError):
        world.service.reject(pending.change_id, world.admin, reason="again")


# ----------------------------------------------------------------------
# rollback
# ----------------------------------------------------------------------


def test_rollback_restores_the_previous_state_at_a_fresh_version(world: World) -> None:
    seed_private(world)
    pending = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public")
    )
    world.service.approve(pending.change_id, world.admin)
    approved = world.acl.get("agent", "a1")
    assert approved is not None and approved.version == 2

    world.service.rollback(pending.change_id, world.admin)

    restored = world.acl.get("agent", "a1")
    assert restored is not None
    assert restored.visibility == "private"
    assert restored.version == approved.version + 1
    assert "a1" not in visible_to(world, world.other)
    assert world.acl.get_change(pending.change_id).status == CHANGE_ROLLED_BACK

    undo = world.acl.list_changes("agent", "a1")[0]
    assert undo.id != pending.change_id
    assert undo.status == CHANGE_APPLIED
    assert undo.reason == f"rollback of {pending.change_id}"
    assert undo.from_version == approved.version
    assert undo.to_version == restored.version
    assert json.loads(undo.after_json)["visibility"] == "private"


def test_rollback_needs_admin_or_the_original_actor(world: World) -> None:
    seed_private(world)
    applied = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, unit_key="sales", visibility="unit")
    )

    with pytest.raises(OctopError):
        world.service.rollback(applied.change_id, world.other)

    world.service.rollback(applied.change_id, world.owner)
    restored = world.acl.get("agent", "a1")
    assert restored is not None
    assert restored.visibility == "private"


def test_rollback_refuses_a_change_that_never_applied(world: World) -> None:
    seed_private(world)
    pending = world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="public")
    )

    with pytest.raises(ValueError):
        world.service.rollback(pending.change_id, world.admin)


def test_rollback_of_unknown_change_is_not_found(world: World) -> None:
    with pytest.raises(OctopError):
        world.service.rollback("nope", world.admin)


# ----------------------------------------------------------------------
# the legacy column and the ACL row commit together
# ----------------------------------------------------------------------


def test_failed_acl_write_leaves_the_agent_private(world: World, db: SqlitePool) -> None:
    """A share is one write to one row: it either lands or nothing moved."""
    agents = AgentRepo(db)
    agents.create(agent_id="a1", user_id=world.owner, name="mine")
    with db.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER acl_boom BEFORE UPDATE ON resource_acl "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )

    with pytest.raises(sqlite3.Error):
        agents.set_shared("a1", True)

    stored = world.acl.get("agent", "a1")
    assert stored is not None
    assert stored.visibility == "private"
    assert agents.public_agent_ids(["a1"]) == set()


# ----------------------------------------------------------------------
# visibility SQL agrees with the pure rules
# ----------------------------------------------------------------------


def test_unit_snapshot_survives_the_owner_changing_departments(
    world: World, db: SqlitePool
) -> None:
    seed_private(world)
    world.service.apply_change(
        world.owner, "agent", "a1", entry(world, visibility="unit", unit_key="sales")
    )

    UserRepo(db).set_org_unit(world.owner, "eng")

    assert "a1" in visible_to(world, world.member)
    assert "a1" not in visible_to(world, world.other)
    shared = world.acl.get("agent", "a1")
    assert shared is not None and shared.unit_key == "sales"
    assert can_access(shared, user_id=world.other, role="user", unit_keys=("eng",)) is False


@pytest.mark.parametrize(
    ("viewer", "role", "unit_keys"),
    [
        ("owner", "user", ("sales",)),
        ("owner", "user", ("eng",)),
        ("member", "user", ("sales",)),
        ("other", "user", ("eng",)),
        ("other", "user", ()),
        ("other", "auditor", ("eng",)),
        ("admin", "admin", ()),
        ("ghost", "user", ("sales",)),
    ],
)
def test_visible_ids_match_can_access(
    world: World, viewer: str, role: str, unit_keys: tuple[str, ...]
) -> None:
    def acl_row(
        resource_id: str,
        owner: int | None,
        visibility: str,
        row_unit_key: str | None = None,
        grants: tuple[tuple[str, str], ...] = (),
    ) -> AclEntry:
        return AclEntry("agent", resource_id, owner, visibility, row_unit_key, 1, grants)

    ids = {"owner": world.owner, "member": world.member, "other": world.other, "admin": world.admin}
    world.acl.upsert(acl_row("priv", world.owner, "private"))
    world.acl.upsert(acl_row("unit-sales", world.owner, "unit", "sales"))
    world.acl.upsert(acl_row("pub", world.other, "public"))
    world.acl.upsert(
        acl_row(
            "granted",
            world.owner,
            "private",
            grants=(("user", str(world.member)), ("role", "auditor"), ("unit", "eng")),
        )
    )
    world.acl.upsert(acl_row("system", None, "private"))
    viewer_id = ids.get(viewer, 9999)

    stored = [
        world.acl.get("agent", resource_id)
        for resource_id in ("priv", "unit-sales", "pub", "granted", "system")
    ]
    expected = {
        e.resource_id
        for e in stored
        if e is not None and can_access(e, user_id=viewer_id, role=role, unit_keys=unit_keys)
    }

    got = world.acl.list_visible_resource_ids(
        "agent", user_id=viewer_id, role=role, unit_keys=unit_keys
    )

    assert got == expected
