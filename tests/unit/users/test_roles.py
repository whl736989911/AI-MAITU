"""Unit tests for the four-level role model (design §2.1, §4.2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.users.identity import (
    ASSIGNABLE_ROLES,
    ROLE_LEVELS,
    Role,
    User,
    assignable_roles,
    can_assign_role,
    can_manage_role,
    resolved_role,
    role_level,
    role_value,
)


def _user(role: Role) -> User:
    return User(id=7, username="u", role=role, display_name=None)


def test_the_model_has_exactly_four_levels() -> None:
    """design §2.1: 系统管理员 > 企业管理员 > 部门管理员 > 企业员工."""
    assert [member.value for member in Role] == [
        "admin",
        "enterprise_admin",
        "unit_admin",
        "user",
    ]
    assert ROLE_LEVELS == {
        "admin": 3,
        "enterprise_admin": 2,
        "unit_admin": 1,
        "user": 0,
    }
    assert role_level(Role.ADMIN) == 3
    assert role_level("enterprise_admin") == 2
    assert role_level(Role.UNIT_ADMIN) == 1
    assert role_level("user") == 0


def test_unknown_role_value_ranks_below_every_role() -> None:
    """A typo in a row must never read as an administrator."""
    assert role_level("enterpise_admin") == -1
    assert role_level(None) == -1
    assert role_level("") == -1
    assert role_value(None) == ""
    assert role_value(Role.ENTERPRISE_ADMIN) == "enterprise_admin"


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        # 系统管理员: 可创建和管理所有角色.
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.ENTERPRISE_ADMIN, True),
        (Role.ADMIN, Role.UNIT_ADMIN, True),
        (Role.ADMIN, Role.USER, True),
        # 企业管理员: may hand out the roles below it — never a system or another
        # enterprise administrator.
        (Role.ENTERPRISE_ADMIN, Role.ADMIN, False),
        (Role.ENTERPRISE_ADMIN, Role.ENTERPRISE_ADMIN, False),
        (Role.ENTERPRISE_ADMIN, Role.UNIT_ADMIN, True),
        (Role.ENTERPRISE_ADMIN, Role.USER, True),
        # 部门管理员: 只能创建本管理范围内的员工.
        (Role.UNIT_ADMIN, Role.ADMIN, False),
        (Role.UNIT_ADMIN, Role.ENTERPRISE_ADMIN, False),
        (Role.UNIT_ADMIN, Role.UNIT_ADMIN, False),
        (Role.UNIT_ADMIN, Role.USER, True),
        # 企业员工: 不能创建账号或授权.
        (Role.USER, Role.USER, False),
        (Role.USER, Role.UNIT_ADMIN, False),
        (Role.USER, "nonsense", False),
    ],
)
def test_assignable_roles_never_reach_a_peer_or_a_senior(
    actor: Role, target: Role | str, allowed: bool
) -> None:
    assert can_assign_role(actor, target) is allowed
    assert can_assign_role(actor.value, target) is allowed  # raw wire strings too


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        (Role.ADMIN, "admin", True),
        (Role.ADMIN, "user", True),
        (Role.ENTERPRISE_ADMIN, "admin", False),
        (Role.ENTERPRISE_ADMIN, "enterprise_admin", False),
        (Role.ENTERPRISE_ADMIN, "unit_admin", True),
        (Role.ENTERPRISE_ADMIN, "user", True),
        (Role.UNIT_ADMIN, "unit_admin", False),
        (Role.UNIT_ADMIN, "user", True),
        (Role.USER, "unit_admin", False),
        (Role.UNIT_ADMIN, "nonsense", True),
    ],
)
def test_managing_an_account_is_limited_to_lower_roles(
    actor: Role, target: str, allowed: bool
) -> None:
    """The target-side rule: design §4.3 rule 6, 越权对象 is refused."""
    assert can_manage_role(actor, target) is allowed


def test_assignable_and_manageable_tables_cover_every_role() -> None:
    """A new role must be added to both tables deliberately, not by default."""
    assert set(ASSIGNABLE_ROLES) == {member.value for member in Role}
    assert assignable_roles("nonsense") == ()
    assert assignable_roles(Role.USER) == ()


def test_scoped_administrators_are_not_system_administrators() -> None:
    """``is_admin`` stays narrow: it is the catalog-wide bypass.

    A role that borrowed the name would inherit every module key instead of the
    ones it was granted, which is the whole reason for a fourth level.
    """
    assert _user(Role.ADMIN).is_admin is True
    assert _user(Role.ENTERPRISE_ADMIN).is_admin is False
    assert _user(Role.ENTERPRISE_ADMIN).is_enterprise_admin is True
    assert _user(Role.UNIT_ADMIN).is_admin is False
    assert _user(Role.USER).is_admin is False


def test_resolved_role_reads_both_account_shapes() -> None:
    """A ``User`` carries ``is_admin``, a persisted row only ``role``."""
    assert resolved_role(_user(Role.ADMIN)) == "admin"
    assert resolved_role(SimpleNamespace(role="unit_admin")) == "unit_admin"
    assert resolved_role(SimpleNamespace(is_admin=True)) == "admin"
    assert resolved_role(SimpleNamespace(is_admin=False, role=Role.USER)) == "user"
    # A bare role is not an account: ``role_value`` is the function for that.
    assert resolved_role(None) == ""
    assert role_value("enterprise_admin") == "enterprise_admin"
