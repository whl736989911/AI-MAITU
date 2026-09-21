"""Unit tests for OrgUnitRepo — units, unit grants, and the v17 user scope columns."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> OrgUnitRepo:
    return OrgUnitRepo(db)


def test_create_and_get_roundtrip(repo: OrgUnitRepo) -> None:
    repo.create(key="hq", label_zh="总部", label_en="Headquarters")
    created = repo.create(
        key="ops", label_zh="运维", label_en="Operations", parent_key="hq", sort_order=3
    )
    assert created.key == "ops"
    assert created.label_zh == "运维"
    assert created.label_en == "Operations"
    assert created.parent_key == "hq"
    assert created.sort_order == 3
    assert created.created_at > 0

    row = repo.get("ops")
    assert row == created
    assert repo.get("missing") is None


def test_list_all_orders_by_sort_then_key(repo: OrgUnitRepo) -> None:
    repo.create(key="b", label_zh="乙", label_en="B", sort_order=2)
    repo.create(key="a", label_zh="甲", label_en="A", sort_order=2)
    repo.create(key="first", label_zh="一", label_en="First", sort_order=1)
    assert [unit.key for unit in repo.list_all()] == ["first", "a", "b"]


def test_delete_clears_unit_and_its_grants(repo: OrgUnitRepo) -> None:
    repo.create(key="ops", label_zh="运维", label_en="Operations")
    repo.create(key="ops-cn", label_zh="运维中国", label_en="Ops CN", parent_key="ops")
    repo.set_grants("ops", ["agents", "chat"])

    repo.delete("ops")

    assert repo.get("ops") is None
    assert repo.list_unit_permissions("ops") == []
    # ``parent_key`` is ON DELETE SET NULL, so the child survives detached.
    child = repo.get("ops-cn")
    assert child is not None
    assert child.parent_key is None


def test_set_grants_replaces_previous_set(repo: OrgUnitRepo) -> None:
    repo.create(key="ops", label_zh="运维", label_en="Operations")
    repo.set_grants("ops", ["chat", "agents"])
    assert repo.list_unit_permissions("ops") == ["agents", "chat"]

    repo.set_grants("ops", ["knowledge", "chat", "chat"])
    assert repo.list_unit_permissions("ops") == ["chat", "knowledge"]

    repo.set_grants("ops", [])
    assert repo.list_unit_permissions("ops") == []


def test_list_unit_permissions_ignores_other_units(repo: OrgUnitRepo) -> None:
    repo.create(key="ops", label_zh="运维", label_en="Operations")
    repo.create(key="sales", label_zh="销售", label_en="Sales")
    repo.set_grants("ops", ["agents"])
    repo.set_grants("sales", ["knowledge"])

    assert repo.list_unit_permissions("ops") == ["agents"]
    assert repo.list_unit_permissions("unknown") == []


def test_grants_for_units_unions_batches_and_dedupes(repo: OrgUnitRepo) -> None:
    repo.create(key="ops", label_zh="运维", label_en="Operations")
    repo.create(key="sales", label_zh="销售", label_en="Sales")
    repo.create(key="support", label_zh="支持", label_en="Support")
    repo.set_grants("ops", ["agents", "chat"])
    repo.set_grants("sales", ["chat", "knowledge"])
    repo.set_grants("support", [])

    assert repo.grants_for_units(["ops", "sales"]) == {"agents", "chat", "knowledge"}
    assert repo.grants_for_units(["ops", "ops", "support"]) == {"agents", "chat"}


def test_grants_for_units_accepts_empty_and_unknown_input(repo: OrgUnitRepo) -> None:
    repo.create(key="ops", label_zh="运维", label_en="Operations")
    repo.set_grants("ops", ["agents"])

    assert repo.grants_for_units([]) == set()
    # Generators must survive the call (single pass over the input).
    assert repo.grants_for_units(unit for unit in ["ops"]) == {"agents"}
    assert repo.grants_for_units(["ghost", "ops"]) == {"agents"}
    assert repo.grants_for_units(unit for unit in ["ghost"]) == set()


def test_user_scope_columns_default_to_unset(db: SqlitePool) -> None:
    users = UserRepo(db)
    user_id = users.create(username="plain", password_hash="h", role="user")
    row = users.get(user_id)
    assert row is not None
    assert row.org_unit is None
    assert row.denied_permissions == []


def test_user_scope_columns_roundtrip_through_setters(db: SqlitePool) -> None:
    users = UserRepo(db)
    user_id = users.create(
        username="alice",
        password_hash="h",
        role="unit_admin",
        org_unit="ops",
        denied_permissions=["browser"],
    )
    created = users.get(user_id)
    assert created is not None
    assert created.org_unit == "ops"
    assert created.denied_permissions == ["browser"]

    users.set_org_unit(user_id, "ops-cn")
    users.set_denied_permissions(user_id, ["filesystem", "users"])
    updated = users.get(user_id)
    assert updated is not None
    assert updated.org_unit == "ops-cn"
    assert updated.denied_permissions == ["filesystem", "users"]

    users.set_org_unit(user_id, None)
    users.set_denied_permissions(user_id, [])
    cleared = users.get(user_id)
    assert cleared is not None
    assert cleared.org_unit is None
    assert cleared.denied_permissions == []
