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
    users = UserRepo(db)
    # A grant write also re-derives the channels of the department's members
    # (design §2.4), so the double carries the user repo that walk reads — and,
    # through the absent ``app_runtime``, no gateway to re-derive for.
    server = SimpleNamespace(
        services=SimpleNamespace(repos=SimpleNamespace(org_unit_repo=units, user_repo=users))
    )
    return SimpleNamespace(
        db=db,
        units=units,
        users=users,
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

async def test_enterprise_admin_without_a_unit_can_create_the_first_root(
    world: SimpleNamespace,
) -> None:
    actor = SimpleNamespace(
        id=9,
        is_admin=False,
        role=Role.ENTERPRISE_ADMIN,
        org_unit=None,
        permissions=["users"],
        denied_permissions=[],
    )
    created = await create_org_unit(
        OrgUnitCreateBody(
            key="acme",
            label_zh="甲公司",
            label_en="Acme",
            parent_key=None,
        ),
        actor=actor,
        server=world.server,
    )
    assert created["parent_key"] is None


@pytest.mark.parametrize(
    ("role", "org_unit"),
    [
        (Role.ENTERPRISE_ADMIN, "ops"),
        (Role.UNIT_ADMIN, "ops"),
        (Role.USER, None),
    ],
    ids=["bound-enterprise-admin", "unit-admin", "employee"],
)
async def test_only_system_or_unbound_enterprise_admin_can_create_a_root(
    world: SimpleNamespace, role: Role, org_unit: str | None
) -> None:
    actor = SimpleNamespace(
        id=9,
        is_admin=False,
        role=role,
        org_unit=org_unit,
        permissions=["users"],
        denied_permissions=[],
    )
    with pytest.raises(OctopError) as exc:
        await create_org_unit(
            OrgUnitCreateBody(
                key="new-root",
                label_zh="根",
                label_en="Root",
                parent_key=None,
            ),
            actor=actor,
            server=world.server,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN
async def test_enterprise_admin_can_manage_units_inside_its_enterprise(
    world: SimpleNamespace,
) -> None:
    """Enterprise-admin unit CRUD and grants cover its own enterprise branch."""
    await _create(world, "acme")
    await _create(world, "acme-sales", parent="acme")
    await _create(world, "globex")
    actor = SimpleNamespace(
        id=9,
        is_admin=False,
        role=Role.ENTERPRISE_ADMIN,
        org_unit="acme",
        permissions=["users", "experts"],
        denied_permissions=[],
    )

    with pytest.raises(OctopError) as root_move:
        await patch_org_unit(
            "acme",
            OrgUnitPatchBody(parent_key=None),
            actor=actor,
            server=world.server,
        )
    assert root_move.value.code is ErrorCode.FORBIDDEN

    created = await create_org_unit(
        OrgUnitCreateBody(
            key="acme-ops",
            label_zh="运维",
            label_en="Operations",
            parent_key="acme",
        ),
        actor=actor,
        server=world.server,
    )
    assert created["parent_key"] == "acme"

    updated = await patch_org_unit(
        "acme-ops",
        OrgUnitPatchBody(label_zh="平台运维", label_en="Platform Ops"),
        actor=actor,
        server=world.server,
    )
    assert updated["label"] == {"zh": "平台运维", "en": "Platform Ops"}

    grants = await set_org_unit_permissions(
        "acme-ops",
        OrgUnitPermissionsBody(permissions=["users", "experts"]),
        actor=actor,
        server=world.server,
    )
    assert grants["permissions"] == ["users", "experts"]
    assert await delete_org_unit(
        "acme-ops", actor=actor, server=world.server
    ) is None
    assert world.units.get("acme-ops") is None

    with pytest.raises(OctopError) as exc:
        await patch_org_unit(
            "globex",
            OrgUnitPatchBody(label_en="Other enterprise"),
            actor=actor,
            server=world.server,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN


async def test_unit_admin_cannot_manage_units_outside_its_subtree(
    world: SimpleNamespace,
) -> None:
    await _create(world, "acme")
    await _create(world, "acme-ops", parent="acme")
    await _create(world, "acme-ops-night", parent="acme-ops")
    await _create(world, "acme-sales", parent="acme")
    actor = SimpleNamespace(
        id=9,
        is_admin=False,
        role=Role.UNIT_ADMIN,
        org_unit="acme-ops",
        permissions=["users"],
        denied_permissions=[],
    )

    with pytest.raises(OctopError) as exc:
        await patch_org_unit(
            "acme-sales",
            OrgUnitPatchBody(label_en="Out of scope"),
            actor=actor,
            server=world.server,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN

    await create_org_unit(
        OrgUnitCreateBody(
            key="acme-ops-day",
            label_zh="白班",
            label_en="Day Shift",
            parent_key="acme-ops-night",
        ),
        actor=actor,
        server=world.server,
    )

@pytest.mark.asyncio
async def test_user_creation_requires_department_but_enterprise_admin_does_not(
    tmp_path: Path,
) -> None:
    """Lock unbound enterprise reach and the employee/admin unit binding rules."""
    from tests.support.app import octop_client
    from tests.support.auth import TEST_PASSWORD, auth_header, bootstrap_admin
    from octop.infra.users.scope import scope_for

    async with octop_client(tmp_path) as (client, srv):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        enterprise = await client.post(
            "/api/users",
            headers=admin_auth,
            json={
                "username": "company_admin",
                "password": TEST_PASSWORD,
                "role": "enterprise_admin",
                "permissions": [],
            },
        )
        assert enterprise.status_code == 201, enterprise.text
        assert enterprise.json()["org_unit"] is None
        assert srv.services is not None
        admin_row = srv.services.user_repo.get_by_username("company_admin")
        assert admin_row is not None
        enterprise_scope = scope_for(admin_row, srv.services.org_unit_repo)
        # Also works in an empty org tree and covers later-added departments.
        assert enterprise_scope.covers_unit("department-created-later") is True
        assert enterprise_scope.covers_unit(None) is True
        assert enterprise_scope.covers_account(user_id=9001, role="user", org_unit=None)

        for role in ("unit_admin", "user"):
            rejected = await client.post(
                "/api/users",
                headers=admin_auth,
                json={
                    "username": f"unbound_{role}",
                    "password": TEST_PASSWORD,
                    "role": role,
                    "permissions": [],
                },
            )
            assert rejected.status_code == 403

        srv.services.org_unit_repo.create(
            key="engineering", label_zh="研发", label_en="Engineering"
        )
        assert enterprise_scope.covers_unit("engineering") is True
        assert enterprise_scope.covers_account(
            user_id=9002, role="user", org_unit="engineering"
        )
        employee = await client.post(
            "/api/users",
            headers=admin_auth,
            json={
                "username": "bound_employee",
                "password": TEST_PASSWORD,
                "role": "user",
                "org_unit": "engineering",
                "permissions": [],
            },
        )
        assert employee.status_code == 201, employee.text
        assert employee.json()["org_unit"] == "engineering"
        unit_admin = await client.post(
            "/api/users",
            headers=admin_auth,
            json={
                "username": "bound_unit_admin",
                "password": TEST_PASSWORD,
                "role": "unit_admin",
                "org_unit": "engineering",
                "permissions": [],
            },
        )
        assert unit_admin.status_code == 201, unit_admin.text
        for user_id in (employee.json()["id"], unit_admin.json()["id"]):
            cleared = await client.patch(
                f"/api/users/{user_id}",
                headers=admin_auth,
                json={"org_unit": None},
            )
            assert cleared.status_code == 403
