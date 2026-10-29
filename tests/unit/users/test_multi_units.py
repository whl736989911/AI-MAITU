"""An account in several departments sees the union of their scopes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import AclEntry
from octop.infra.users.permissions import unit_permissions


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict]:
    pool = SqlitePool(tmp_path / "multi.db")
    run_migrations(pool)
    users = UserRepo(pool)
    units = OrgUnitRepo(pool)
    units.create(key="tech", label_zh="技术部", label_en="Tech")
    units.create(key="equip", label_zh="设备部", label_en="Equipment")
    units.create(key="plant", label_zh="生产部", label_en="Plant")
    units.set_grants("tech", ["knowledge_bases"])
    units.set_grants("equip", ["knowledge_bases", "users"])
    owner = users.create(username="owner", password_hash="x", role="user")
    try:
        yield {"pool": pool, "users": users, "units": units, "owner": owner}
    finally:
        pool.close()


def test_units_for_user_includes_every_membership(env: dict) -> None:
    users = env["users"]
    user = users.create(username="dual", password_hash="x", role="user")
    users.set_units(user, ["tech", "equip"])

    assert users.units_for_user(user) == ["equip", "tech"]
    # The first key stays the primary department for the user list.
    assert users.get(user).org_unit == "tech"


def test_module_grants_union_across_departments(env: dict) -> None:
    users, units = env["users"], env["units"]
    user = users.create(username="dual", password_hash="x", role="user")
    users.set_units(user, ["tech", "equip"])

    grants: set[str] = set()
    for unit in users.units_for_user(user):
        grants |= unit_permissions(unit, units)

    assert grants == {"knowledge_bases", "users"}


def test_asset_scoped_to_one_department_is_visible_from_the_other(env: dict) -> None:
    """A grant on 设备部 reaches an account that also belongs to 技术部."""
    acl = ResourceAclRepo(env["pool"])
    users = env["users"]
    user = users.create(username="dual", password_hash="x", role="user")
    users.set_units(user, ["tech", "equip"])
    owner = env["owner"]

    acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id="doc-1",
            owner_user_id=owner,
            visibility="unit",
            unit_key="equip",
            version=1,
        )
    )

    role, unit_keys = acl.scope_for_user(user)
    visible = acl.list_visible_resource_ids(
        "knowledge_document", user_id=user, role=role, unit_keys=unit_keys
    )

    assert "equip" in unit_keys and "tech" in unit_keys
    assert visible == {"doc-1"}


def test_leaving_a_department_drops_its_scope(env: dict) -> None:
    acl = ResourceAclRepo(env["pool"])
    users = env["users"]
    user = users.create(username="dual", password_hash="x", role="user")
    users.set_units(user, ["tech", "equip"])
    acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id="doc-1",
            owner_user_id=env["owner"],
            visibility="unit",
            unit_key="equip",
            version=1,
        )
    )

    users.set_units(user, ["tech"])
    role, unit_keys = acl.scope_for_user(user)
    visible = acl.list_visible_resource_ids(
        "knowledge_document", user_id=user, role=role, unit_keys=unit_keys
    )

    assert unit_keys == ("tech",)
    assert visible == set()
