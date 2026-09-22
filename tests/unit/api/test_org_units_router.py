"""Unit tests for the org-unit write endpoints.

The router functions are called directly (FastAPI's DI is not exercised), so the
server double carries only the org-unit repo the routes touch; the repos
themselves are the real ones over a migrated SQLite control plane, because the
guards are exactly about what those tables hold.

Covered refusals: a duplicate key, an unknown parent, a reparent that would make
the unit its own ancestor (including the degenerate self-parent case), deleting a
unit that still has children, and deleting a unit that is still assigned to users.
Department grant writes are covered too: replace-not-merge semantics, unknown
units and unknown module keys, and the ``unit_admin`` scope (own unit only).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.api.routers.org_units import (
    OrgUnitCreateBody,
    OrgUnitPatchBody,
    OrgUnitPermissionsBody,
    create_org_unit,
    delete_org_unit,
    get_org_unit_permissions,
    patch_org_unit,
    set_org_unit_permissions,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    units = OrgUnitRepo(db)
    server = SimpleNamespace(services=SimpleNamespace(repos=SimpleNamespace(org_unit_repo=units)))
    return SimpleNamespace(
        db=db,
        units=units,
        users=UserRepo(db),
        server=server,
        admin=SimpleNamespace(id=1, is_admin=True),
    )


async def _create(
    world: SimpleNamespace,
    key: str,
    *,
    parent: str | None = None,
    label_zh: str = "甲",
    label_en: str = "Alpha",
    sort_order: int = 0,
) -> dict[str, object]:
    return await create_org_unit(
        OrgUnitCreateBody(
            key=key,
            label_zh=label_zh,
            label_en=label_en,
            parent_key=parent,
            sort_order=sort_order,
        ),
        actor=world.admin,
        server=world.server,
    )


async def test_create_patch_delete_roundtrip(world: SimpleNamespace) -> None:
    await _create(world, "hq")
    created = await _create(world, "ops", sort_order=7)
    assert created == {
        "key": "ops",
        "label": {"zh": "甲", "en": "Alpha"},
        "parent_key": None,
        "sort_order": 7,
    }

    moved = await patch_org_unit(
        "ops",
        OrgUnitPatchBody(label_zh="运维", label_en="Operations", parent_key="hq", sort_order=2),
        actor=world.admin,
        server=world.server,
    )
    assert moved["label"] == {"zh": "运维", "en": "Operations"}
    assert moved["parent_key"] == "hq"
    assert moved["sort_order"] == 2

    # Omitted parent_key keeps the parent; an explicit null moves the unit to the root.
    kept = await patch_org_unit(
        "ops", OrgUnitPatchBody(label_en="Ops"), actor=world.admin, server=world.server
    )
    assert kept["parent_key"] == "hq"
    assert kept["label"] == {"zh": "运维", "en": "Ops"}
    detached = await patch_org_unit(
        "ops", OrgUnitPatchBody(parent_key=None), actor=world.admin, server=world.server
    )
    assert detached["parent_key"] is None

    assert await delete_org_unit("ops", actor=world.admin, server=world.server) is None
    assert world.units.get("ops") is None
    assert world.units.get("hq") is not None


async def test_create_rejects_duplicate_key(world: SimpleNamespace) -> None:
    await _create(world, "ops")

    with pytest.raises(OctopError) as exc:
        await _create(world, "ops", label_zh="重复", label_en="Duplicate")

    assert exc.value.code is ErrorCode.AGENT_ID_TAKEN
    assert exc.value.status == 409
    assert world.units.get("ops").label_en == "Alpha"


async def test_create_rejects_unknown_parent(world: SimpleNamespace) -> None:
    with pytest.raises(OctopError) as exc:
        await _create(world, "ops", parent="ghost")

    assert exc.value.code is ErrorCode.NOT_FOUND
    assert exc.value.status == 404
    assert exc.value.details["parent_key"] == "ghost"
    assert world.units.get("ops") is None


@pytest.mark.parametrize("new_parent", ["ops", "ops-cn"], ids=["itself", "its own descendant"])
async def test_patch_rejects_parent_that_would_cycle(
    world: SimpleNamespace, new_parent: str
) -> None:
    await _create(world, "hq")
    await _create(world, "ops", parent="hq")
    await _create(world, "ops-cn", parent="ops")

    with pytest.raises(OctopError) as exc:
        await patch_org_unit(
            "ops",
            OrgUnitPatchBody(parent_key=new_parent),
            actor=world.admin,
            server=world.server,
        )

    assert exc.value.code is ErrorCode.SLASH_BAD_ARGS
    assert exc.value.status == 400
    assert world.units.get("ops").parent_key == "hq"


async def test_patch_terminates_on_hierarchy_that_already_cycles(world: SimpleNamespace) -> None:
    """A tree corrupted by an older release must not make the guard walk forever."""
    await _create(world, "a")
    await _create(world, "b", parent="a")
    await _create(world, "c")
    with world.db.transaction() as conn:
        conn.execute("UPDATE org_units SET parent_key = 'b' WHERE key = 'a'")
        conn.execute("UPDATE org_units SET parent_key = 'a' WHERE key = 'b'")

    reparented = await patch_org_unit(
        "c", OrgUnitPatchBody(parent_key="a"), actor=world.admin, server=world.server
    )

    assert reparented["parent_key"] == "a"


async def test_delete_rejects_unit_with_children(world: SimpleNamespace) -> None:
    await _create(world, "ops")
    await _create(world, "ops-cn", parent="ops")

    with pytest.raises(OctopError) as exc:
        await delete_org_unit("ops", actor=world.admin, server=world.server)

    assert exc.value.code is ErrorCode.ORG_UNIT_HAS_CHILDREN
    assert exc.value.status == 409
    assert exc.value.details["children"] == ["ops-cn"]
    # Neither the unit nor its child was rewritten by the refused delete.
    assert world.units.get("ops") is not None
    assert world.units.get("ops-cn").parent_key == "ops"


async def test_delete_rejects_unit_assigned_to_users(world: SimpleNamespace) -> None:
    await _create(world, "ops")
    alice = world.users.create(username="alice", password_hash="h", role="user", org_unit="ops")

    with pytest.raises(OctopError) as exc:
        await delete_org_unit("ops", actor=world.admin, server=world.server)

    assert exc.value.code is ErrorCode.ORG_UNIT_IN_USE
    assert exc.value.status == 409
    assert exc.value.details["users"] == ["alice"]
    assert world.units.get("ops") is not None

    # Disabled accounts hold the scope too: ``users.org_unit`` has no foreign key,
    # so skipping them would leave a reference no later read can resolve.
    world.users.set_disabled(alice, True)
    with pytest.raises(OctopError) as exc:
        await delete_org_unit("ops", actor=world.admin, server=world.server)
    assert exc.value.code is ErrorCode.ORG_UNIT_IN_USE
    assert world.units.get("ops") is not None


async def test_patch_and_delete_reject_unknown_unit(world: SimpleNamespace) -> None:
    with pytest.raises(OctopError) as exc:
        await patch_org_unit(
            "ghost", OrgUnitPatchBody(label_en="Ghost"), actor=world.admin, server=world.server
        )
    assert exc.value.code is ErrorCode.NOT_FOUND
    assert exc.value.status == 404

    with pytest.raises(OctopError) as exc:
        await delete_org_unit("ghost", actor=world.admin, server=world.server)
    assert exc.value.code is ErrorCode.NOT_FOUND


async def test_unit_permissions_roundtrip(world: SimpleNamespace) -> None:
    """A grant lands in ``org_unit_permissions`` and a later write replaces it."""
    await _create(world, "ops")

    stored = await set_org_unit_permissions(
        "ops",
        OrgUnitPermissionsBody(permissions=["browser", "knowledge_bases"]),
        actor=world.admin,
        server=world.server,
    )
    assert stored == {"unit_key": "ops", "permissions": ["browser", "knowledge_bases"]}
    assert await get_org_unit_permissions("ops", actor=world.admin, server=world.server) == {
        "unit_key": "ops",
        "permissions": ["browser", "knowledge_bases"],
    }

    # Replace, not merge: dropping a key is the only way to revoke it.
    replaced = await set_org_unit_permissions(
        "ops",
        OrgUnitPermissionsBody(permissions=["knowledge_bases"]),
        actor=world.admin,
        server=world.server,
    )
    assert replaced["permissions"] == ["knowledge_bases"]
    with world.db.connect() as conn:
        rows = conn.execute(
            "SELECT permission_key FROM org_unit_permissions WHERE unit_key = 'ops'"
        ).fetchall()
    assert [r["permission_key"] for r in rows] == ["knowledge_bases"]


async def test_unit_permissions_reject_unknown_unit_and_key(world: SimpleNamespace) -> None:
    await _create(world, "ops")

    with pytest.raises(OctopError) as exc:
        await get_org_unit_permissions("ghost", actor=world.admin, server=world.server)
    assert exc.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(OctopError) as exc:
        await set_org_unit_permissions(
            "ops",
            OrgUnitPermissionsBody(permissions=["not_a_module"]),
            actor=world.admin,
            server=world.server,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.status == 400
    assert await get_org_unit_permissions("ops", actor=world.admin, server=world.server) == {
        "unit_key": "ops",
        "permissions": [],
    }


@pytest.mark.parametrize(
    ("role", "org_unit", "allowed"),
    [
        (Role.UNIT_ADMIN, "ops", True),
        (Role.UNIT_ADMIN, "sales", False),
        (Role.UNIT_ADMIN, None, False),
        (Role.USER, "ops", False),
    ],
    ids=["own unit", "other unit", "no unit", "plain member"],
)
async def test_unit_permission_writes_are_scoped_to_own_unit(
    world: SimpleNamespace, role: Role, org_unit: str | None, allowed: bool
) -> None:
    """``unit_admin`` administers its own department; nobody else's."""
    await _create(world, "ops")
    await _create(world, "sales")
    actor = SimpleNamespace(
        id=9,
        is_admin=False,
        role=role,
        org_unit=org_unit,
        permissions=["users"],
        denied_permissions=[],
    )

    if not allowed:
        with pytest.raises(OctopError) as exc:
            await set_org_unit_permissions(
                "ops",
                OrgUnitPermissionsBody(permissions=["users"]),
                actor=actor,
                server=world.server,
            )
        assert exc.value.code is ErrorCode.FORBIDDEN
        assert exc.value.status == 403
        assert world.units.list_unit_permissions("ops") == []
        return

    written = await set_org_unit_permissions(
        "ops",
        OrgUnitPermissionsBody(permissions=["users"]),
        actor=actor,
        server=world.server,
    )
    assert written["permissions"] == ["users"]


async def test_unit_admin_may_not_grant_keys_it_does_not_hold(world: SimpleNamespace) -> None:
    """Granting is delegation, not self-escalation: held keys only."""
    await _create(world, "ops")
    holder = SimpleNamespace(
        id=9,
        is_admin=False,
        role=Role.UNIT_ADMIN,
        org_unit="ops",
        permissions=["users"],
        denied_permissions=[],
    )

    with pytest.raises(OctopError) as exc:
        await set_org_unit_permissions(
            "ops",
            OrgUnitPermissionsBody(permissions=["users", "security"]),
            actor=holder,
            server=world.server,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.details["missing"] == ["security"]
    assert world.units.list_unit_permissions("ops") == []

    # Keys the department already has count as held: a PUT replaces the whole
    # set, so the admin must be able to re-submit what is already granted.
    world.units.set_grants("ops", ["knowledge_bases"])
    kept = await set_org_unit_permissions(
        "ops",
        OrgUnitPermissionsBody(permissions=["users", "knowledge_bases"]),
        actor=holder,
        server=world.server,
    )
    assert kept["permissions"] == ["users", "knowledge_bases"]
