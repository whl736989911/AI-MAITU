"""Unit tests for organization scope (design §2.1, §4.2, §4.3).

The tree is the real one — a migrated SQLite control plane — because the rules
are about what those rows say: an enterprise is a root unit, a department is a
unit and its sub-departments, and an account with no unit is outside both.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role
from octop.infra.users.permissions import unit_permissions
from octop.infra.users.scope import (
    assert_assignable_role,
    assert_may_manage_account,
    assert_may_manage_unit,
    enterprise_root,
    scope_for,
    subtree_keys,
)


@pytest.fixture
def repo(tmp_path: Path) -> OrgUnitRepo:
    """Two enterprises, each with departments and a sub-department.

    ``acme`` (enterprise) ─┬─ ``acme-ops`` ── ``acme-ops-night``
                          └─ ``acme-sales``
    ``globex`` (enterprise) ─── ``globex-ops``
    """
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    units = OrgUnitRepo(db)
    units.create(key="acme", label_zh="甲企业", label_en="Acme")
    units.create(key="acme-ops", label_zh="运维", label_en="Ops", parent_key="acme")
    units.create(key="acme-ops-night", label_zh="夜班", label_en="Night", parent_key="acme-ops")
    units.create(key="acme-sales", label_zh="销售", label_en="Sales", parent_key="acme")
    units.create(key="globex", label_zh="乙企业", label_en="Globex")
    units.create(key="globex-ops", label_zh="运维", label_en="Ops", parent_key="globex")
    return units


def _actor(role: Role | str, org_unit: str | None, *, user_id: int = 9) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, is_admin=False, role=role, org_unit=org_unit)


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=1, is_admin=True, role=Role.ADMIN, org_unit=None)


def test_enterprise_is_the_root_of_the_actors_branch(repo: OrgUnitRepo) -> None:
    assert enterprise_root(repo, "acme-ops-night") == "acme"
    assert enterprise_root(repo, "globex-ops") == "globex"
    assert enterprise_root(repo, "acme") == "acme"
    # An unknown unit names no enterprise: guessing one would hand out reach.
    assert enterprise_root(repo, "ghost") is None


def test_subtree_is_the_unit_and_everything_below_it(repo: OrgUnitRepo) -> None:
    assert subtree_keys(repo, "acme-ops") == {"acme-ops", "acme-ops-night"}
    assert subtree_keys(repo, "acme") == {"acme", "acme-ops", "acme-ops-night", "acme-sales"}
    assert subtree_keys(repo, "ghost") == frozenset()


def test_system_admin_reaches_every_unit_including_unbound(repo: OrgUnitRepo) -> None:
    scope = scope_for(_admin(), repo)
    assert scope.is_system_admin is True
    assert scope.covers_unit("globex-ops") is True
    assert scope.covers_unit(None) is True
    assert scope.covers_account(user_id=99, role=Role.ADMIN, org_unit=None) is True


def test_enterprise_admin_reaches_its_enterprise_and_nothing_else(repo: OrgUnitRepo) -> None:
    scope = scope_for(_actor(Role.ENTERPRISE_ADMIN, "acme-sales"), repo)
    assert scope.enterprise == "acme"
    assert scope.units == {"acme", "acme-ops", "acme-ops-night", "acme-sales"}
    assert scope.covers_unit("acme-ops-night") is True
    # 企业管理员 manages 本企业: another enterprise is out of reach.
    assert scope.covers_unit("globex-ops") is False
    assert scope.covers_unit(None) is False


def test_unit_admin_reaches_its_department_and_sub_departments(repo: OrgUnitRepo) -> None:
    scope = scope_for(_actor(Role.UNIT_ADMIN, "acme-ops"), repo)
    assert scope.units == {"acme-ops", "acme-ops-night"}
    assert scope.covers_unit("acme-ops-night") is True
    # Sibling and parent departments are out of reach, and so is the enterprise
    # root above it (design §2.1: 不能管理兄弟部门或上级部门).
    assert scope.covers_unit("acme-sales") is False
    assert scope.covers_unit("acme") is False


def test_plain_employee_and_unknown_roles_reach_nothing(repo: OrgUnitRepo) -> None:
    for role in (Role.USER, "nonsense"):
        scope = scope_for(_actor(role, "acme-ops"), repo)
        assert scope.units == frozenset()
        assert scope.covers_unit("acme-ops") is False
        assert scope.covers_account(user_id=99, role=Role.USER, org_unit="acme-ops") is False


def test_unbound_enterprise_admin_reaches_every_current_and_future_unit(
    repo: OrgUnitRepo,
) -> None:
    scope = scope_for(_actor(Role.ENTERPRISE_ADMIN, None), repo)
    assert scope.enterprise == "*"
    assert scope.units == frozenset()
    assert scope.covers_unit("globex-ops") is True
    assert scope.covers_unit(None) is True
    assert scope.covers_account(user_id=5, role=Role.USER, org_unit=None) is True
    # Enterprise-wide reach is dynamic: newly-created units need no cached list.
    repo.create(key="new-dept", label_zh="新部门", label_en="New Department")
    assert scope.covers_unit("new-dept") is True


def test_unit_admin_without_a_unit_reaches_nothing(repo: OrgUnitRepo) -> None:
    scope = scope_for(_actor(Role.UNIT_ADMIN, None), repo)
    assert scope.units == frozenset()
    assert scope.covers_unit("acme") is False


def test_covers_account_checks_the_role_before_the_department(repo: OrgUnitRepo) -> None:
    scope = scope_for(_actor(Role.UNIT_ADMIN, "acme-ops", user_id=9), repo)
    # Inside the department, but not below the actor: a peer or a senior is out
    # of reach whatever the department says.
    assert scope.covers_account(user_id=5, role=Role.UNIT_ADMIN, org_unit="acme-ops") is False
    assert scope.covers_account(user_id=5, role=Role.ENTERPRISE_ADMIN, org_unit="acme") is False
    assert scope.covers_account(user_id=5, role=Role.USER, org_unit="acme-ops-night") is True
    assert scope.covers_account(user_id=5, role=Role.USER, org_unit="acme-sales") is False
    # Self is always reachable: profile edits and one's own password cannot
    # escalate, and the fields that could are checked separately.
    assert scope.covers_account(user_id=9, role=Role.UNIT_ADMIN, org_unit="globex-ops") is True


def test_scope_refusals_name_the_reason(repo: OrgUnitRepo) -> None:
    scope = scope_for(_actor(Role.UNIT_ADMIN, "acme-ops"), repo)
    with pytest.raises(OctopError) as unit_exc:
        assert_may_manage_unit(scope, "globex-ops", action="edit this unit")
    assert unit_exc.value.code is ErrorCode.FORBIDDEN
    assert unit_exc.value.status == 403
    assert unit_exc.value.details["unit_key"] == "globex-ops"

    target = SimpleNamespace(id=5, role="user", org_unit="globex-ops")
    with pytest.raises(OctopError) as account_exc:
        assert_may_manage_account(scope, target, action="edit this account")
    assert account_exc.value.code is ErrorCode.FORBIDDEN
    assert account_exc.value.details["target_org_unit"] == "globex-ops"

    with pytest.raises(OctopError) as role_exc:
        assert_assignable_role(_actor(Role.ENTERPRISE_ADMIN, "acme"), "admin", action="assign")
    assert role_exc.value.details["assignable"] == ["unit_admin", "user"]


def test_department_grant_reaches_its_sub_departments(repo: OrgUnitRepo) -> None:
    """A department includes its sub-departments (design §2.1).

    The grant is written on ``acme-ops`` and the member sits in
    ``acme-ops-night``: reading only the member's own row would make the grant
    invisible to exactly the member it was meant for. The enterprise root's
    grants reach the whole enterprise the same way.
    """
    assert unit_permissions("acme-ops-night", repo) == set()
    repo.set_grants("acme-ops", ["browser"])
    assert unit_permissions("acme-ops-night", repo) == {"browser"}
    assert unit_permissions("acme-ops", repo) == {"browser"}
    # A sibling department is not below the granted one.
    assert unit_permissions("acme-sales", repo) == set()

    repo.set_grants("acme", ["terminal"])
    assert unit_permissions("acme-sales", repo) == {"terminal"}
    assert unit_permissions("acme-ops-night", repo) == {"browser", "terminal"}
    # Another enterprise is untouched.
    assert unit_permissions("globex-ops", repo) == set()
    # An unknown unit resolves to nothing rather than to the root's grants.
    assert unit_permissions("ghost", repo) == set()
