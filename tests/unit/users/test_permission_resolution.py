"""Unit tests for the M2 resolution formula ``role ∪ unit ∪ grant − deny``."""

from __future__ import annotations

from dataclasses import dataclass, field

from octop.infra.users.identity import Role, User
from octop.infra.users.permissions import (
    ALL_PERMISSION_KEYS,
    BASELINE_PERMISSIONS,
    effective_permissions,
    resolve_permissions,
    role_default_permissions,
    unit_permissions,
    user_has_permission,
)


@dataclass
class _LegacyUser:
    """Pre-M2 user shape: only the members of the ``PermissionUser`` protocol."""

    is_admin: bool
    permissions: list[str] = field(default_factory=list)


@dataclass
class _UnitRepo:
    """Fake org-unit store implementing ``list_unit_permissions``."""

    grants: dict[str, list[str]] = field(default_factory=dict)

    def list_unit_permissions(self, unit_key: str) -> list[str]:
        return list(self.grants.get(unit_key, []))


def _user(
    role: Role = Role.USER,
    *,
    permissions: list[str] | None = None,
    denied: list[str] | None = None,
    org_unit: str | None = None,
) -> User:
    return User(
        id=1,
        username="u",
        role=role,
        display_name=None,
        permissions=list(permissions or []),
        org_unit=org_unit,
        denied_permissions=list(denied or []),
    )


def test_role_default_permissions_only_admin_gets_catalog() -> None:
    assert role_default_permissions(Role.ADMIN) == ALL_PERMISSION_KEYS
    assert role_default_permissions(Role.UNIT_ADMIN) == set()
    assert role_default_permissions(Role.USER) == set()
    assert role_default_permissions("admin") == ALL_PERMISSION_KEYS
    assert role_default_permissions("nonsense") == set()


def test_unit_admin_is_not_admin() -> None:
    unit_admin = _user(Role.UNIT_ADMIN)
    assert unit_admin.is_admin is False
    assert user_has_permission(unit_admin, "providers") is False
    assert effective_permissions(unit_admin) == []


def test_resolve_permissions_unit_none_is_empty_set() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=None,
        denied=None,
        unit_grants=None,
    )
    assert resolved == set()


def test_unit_permissions_none_or_unknown_key_is_empty() -> None:
    repo = _UnitRepo({"eng": ["browser", "terminal"]})
    assert unit_permissions(None, repo) == set()
    assert unit_permissions("", repo) == set()
    assert unit_permissions("sales", repo) == set()
    assert unit_permissions("eng", repo) == {"browser", "terminal"}


def test_unit_permissions_returns_detached_copy() -> None:
    repo = _UnitRepo({"eng": ["browser"]})
    got = unit_permissions("eng", repo)
    got.add("users")
    assert unit_permissions("eng", repo) == {"browser"}


def test_resolve_permissions_unit_grant_applies() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=None,
        denied=None,
        unit_grants={"providers", "users"},
    )
    assert {"providers", "users"} <= resolved


def test_resolve_permissions_deny_outranks_grant() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=["browser", "users"],
        denied=["browser"],
        unit_grants=None,
    )
    assert "browser" not in resolved
    assert "users" in resolved


def test_resolve_permissions_deny_outranks_unit_grant() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=None,
        denied=["terminal"],
        unit_grants={"terminal", "desktop"},
    )
    assert "terminal" not in resolved
    assert "desktop" in resolved


def test_resolve_permissions_deny_outranks_baseline_grant() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=sorted(BASELINE_PERMISSIONS),
        denied=["channels"],
        unit_grants=None,
    )
    assert "channels" not in resolved
    assert resolved == BASELINE_PERMISSIONS - {"channels"}


def test_resolve_permissions_admin_bypasses_deny_and_empty_permissions() -> None:
    resolved = resolve_permissions(
        role=Role.ADMIN,
        permissions=[],
        denied=["browser", "users"],
        unit_grants=set(),
    )
    assert resolved == ALL_PERMISSION_KEYS


def test_resolve_permissions_difference_is_exact() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=["browser", "users"],
        denied=["users", "envs"],
        unit_grants=None,
    )
    # Granted keys lose exactly the denied ones; a deny for a key that was never
    # granted (here ``envs``) removes nothing else.
    assert resolved == {"browser"}


def test_resolve_permissions_is_pure() -> None:
    permissions = ["browser"]
    denied = ["users"]
    unit_grants = {"terminal"}
    first = resolve_permissions(
        role=Role.USER,
        permissions=permissions,
        denied=denied,
        unit_grants=unit_grants,
    )
    second = resolve_permissions(
        role=Role.USER,
        permissions=permissions,
        denied=denied,
        unit_grants=unit_grants,
    )
    assert first == second
    assert permissions == ["browser"]
    assert denied == ["users"]
    assert unit_grants == {"terminal"}
    assert first is not unit_grants


def test_user_has_permission_uses_unit_grants() -> None:
    member = _user(Role.USER, permissions=["browser"], org_unit="eng")
    assert user_has_permission(member, "providers") is False
    assert user_has_permission(member, "providers", unit_grants={"providers"}) is True
    assert user_has_permission(member, "browser") is True


def test_user_has_permission_deny_outranks_unit_grant() -> None:
    member = _user(Role.UNIT_ADMIN, denied=["terminal"], org_unit="eng")
    assert user_has_permission(member, "terminal", unit_grants={"terminal"}) is False
    assert "terminal" not in effective_permissions(member, unit_grants={"terminal"})


def test_user_has_permission_denies_unknown_key() -> None:
    member = _user(Role.USER, permissions=["browser"])
    assert user_has_permission(member, "not_a_real_key") is False
    assert user_has_permission(member, "not_a_real_key", unit_grants={"not_a_real_key"}) is False


def test_effective_permissions_matches_formula() -> None:
    member = _user(
        Role.USER,
        permissions=sorted(BASELINE_PERMISSIONS | {"browser"}),
        denied=["channels"],
        org_unit="eng",
    )
    expected = sorted((BASELINE_PERMISSIONS - {"channels"}) | {"browser", "users"})
    assert effective_permissions(member, unit_grants={"users"}) == expected


def test_effective_permissions_admin_bypasses_deny() -> None:
    admin = _user(Role.ADMIN, permissions=[], denied=["browser"])
    assert effective_permissions(admin) == sorted(ALL_PERMISSION_KEYS)


def test_wrappers_accept_legacy_user_shape_without_role_or_denied() -> None:
    admin = _LegacyUser(is_admin=True, permissions=[])
    assert effective_permissions(admin) == sorted(ALL_PERMISSION_KEYS)
    member = _LegacyUser(is_admin=False, permissions=["browser"])
    assert effective_permissions(member) == ["browser"]
    assert user_has_permission(member, "browser") is True
    assert user_has_permission(member, "providers") is False
