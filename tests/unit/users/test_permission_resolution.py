"""Unit tests for the M2 resolution formula ``role ∪ unit ∪ grant − deny``."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import pytest

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User
from octop.infra.users.permissions import (
    ALL_PERMISSION_KEYS,
    BASELINE_PERMISSIONS,
    RESTRICTED_DEPLOYMENT_PERMISSIONS,
    assert_can_grant,
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
    """Fake org-unit store: a flat tree, so a unit is its own only ancestor."""

    grants: dict[str, list[str]] = field(default_factory=dict)

    def list_unit_permissions(self, unit_key: str) -> list[str]:
        return list(self.grants.get(unit_key, []))

    def ancestor_keys(self, unit_key: str) -> list[str]:
        return [unit_key] if unit_key in self.grants else []

    def grants_for_units(self, unit_keys: Iterable[str]) -> set[str]:
        return {key for unit in unit_keys for key in self.grants.get(str(unit), [])}


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
    repo = _UnitRepo({"eng": ["features", "users"]})
    assert unit_permissions(None, repo) == set()
    assert unit_permissions("", repo) == set()
    assert unit_permissions("sales", repo) == set()
    assert unit_permissions("eng", repo) == {"features", "users"}


def test_unit_permissions_returns_detached_copy() -> None:
    repo = _UnitRepo({"eng": ["features"]})
    got = unit_permissions("eng", repo)
    got.add("users")
    assert unit_permissions("eng", repo) == {"features"}


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
        permissions=["features", "users"],
        denied=["features"],
        unit_grants=None,
    )
    assert "features" not in resolved
    assert "users" in resolved


def test_resolve_permissions_deny_outranks_unit_grant() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=None,
        denied=["features"],
        unit_grants={"features", "users"},
    )
    assert "features" not in resolved
    assert "users" in resolved


def test_teams_permission_requires_effective_experts_across_grant_sources() -> None:
    def resolve(
        *,
        permissions: list[str] | None = None,
        denied: list[str] | None = None,
        unit_grants: set[str] | None = None,
    ) -> set[str]:
        return resolve_permissions(
            role=Role.USER,
            permissions=permissions,
            denied=denied,
            unit_grants=unit_grants,
        )

    assert "teams" not in resolve(permissions=["experts"])
    assert "teams" not in resolve(permissions=["teams"])
    assert {"experts", "teams"} <= resolve(permissions=["experts", "teams"])
    # Either grant source can supply the expert prerequisite.
    assert {"experts", "teams"} <= resolve(permissions=["experts"], unit_grants={"teams"})
    assert {"experts", "teams"} <= resolve(permissions=["teams"], unit_grants={"experts"})
    # An explicit deny removes the prerequisite after every grant source and
    # therefore makes teams ineffective as well.
    resolved = resolve(
        permissions=["experts", "teams"],
        denied=["experts"],
        unit_grants={"experts"},
    )
    assert "experts" not in resolved
    assert "teams" not in resolved


def test_resolve_permissions_deny_outranks_baseline_grant() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=sorted(BASELINE_PERMISSIONS),
        denied=["channels"],
        unit_grants=None,
    )
    assert "channels" not in resolved
    assert resolved == BASELINE_PERMISSIONS - {"channels"}


@pytest.mark.parametrize("managed", [False, True])
def test_resolve_permissions_admin_matches_deployment_mode(
    monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    if managed:
        monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    else:
        monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    resolved = resolve_permissions(
        role=Role.ADMIN,
        permissions=[],
        denied=["features", "users"],
        unit_grants=set(),
    )
    restricted = RESTRICTED_DEPLOYMENT_PERMISSIONS if managed else set()
    assert resolved == ALL_PERMISSION_KEYS - restricted


@pytest.mark.parametrize("managed", [False, True])
def test_resolve_permissions_explicit_browser_grant_matches_deployment_mode(
    monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    if managed:
        monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    else:
        monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=["browser"],
        denied=None,
        unit_grants=None,
    )
    assert ("browser" in resolved) is not managed


@pytest.mark.parametrize("managed", [False, True])
def test_agent_plugin_module_respects_managed_install_license(
    monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    if managed:
        monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    else:
        monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=["agent_plugins"],
        denied=None,
        unit_grants=None,
    )
    assert ("agent_plugins" in resolved) is not managed


def test_resolve_permissions_difference_is_exact() -> None:
    resolved = resolve_permissions(
        role=Role.USER,
        permissions=["features", "users"],
        denied=["users", "envs"],
        unit_grants=None,
    )
    # A deny subtracts exactly its own keys: ``users`` is taken away, a deny
    # for a key that was never granted (``envs``) removes nothing else, and
    # ``features`` survives untouched.
    assert resolved == {"features"}


def test_resolve_permissions_is_pure() -> None:
    permissions = ["users"]
    denied = ["users"]
    unit_grants = {"users"}
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
    assert permissions == ["users"]
    assert denied == ["users"]
    assert unit_grants == {"users"}
    assert first is not unit_grants


def test_user_has_permission_uses_unit_grants() -> None:
    member = _user(Role.USER, permissions=["features"], org_unit="eng")
    assert user_has_permission(member, "providers") is False
    assert user_has_permission(member, "providers", unit_grants={"providers"}) is True
    assert user_has_permission(member, "features") is True


def test_user_has_permission_deny_outranks_unit_grant() -> None:
    member = _user(Role.UNIT_ADMIN, denied=["features"], org_unit="eng")
    assert user_has_permission(member, "features", unit_grants={"features"}) is False
    assert "features" not in effective_permissions(member, unit_grants={"features"})


def test_user_has_permission_denies_unknown_key() -> None:
    member = _user(Role.USER, permissions=["features"])
    assert user_has_permission(member, "not_a_real_key") is False
    assert user_has_permission(member, "not_a_real_key", unit_grants={"not_a_real_key"}) is False


def test_effective_permissions_matches_formula() -> None:
    member = _user(
        Role.USER,
        permissions=sorted(BASELINE_PERMISSIONS | {"users"}),
        denied=["channels"],
        org_unit="eng",
    )
    expected = sorted((BASELINE_PERMISSIONS - {"channels"}) | {"users"})
    assert effective_permissions(member, unit_grants={"users"}) == expected


@pytest.mark.parametrize("managed", [False, True])
def test_effective_permissions_admin_matches_deployment_mode(
    monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    if managed:
        monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    else:
        monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    admin = _user(Role.ADMIN, permissions=[], denied=["features"])
    restricted = RESTRICTED_DEPLOYMENT_PERMISSIONS if managed else set()
    assert effective_permissions(admin) == sorted(ALL_PERMISSION_KEYS - restricted)


@pytest.mark.parametrize("managed", [False, True])
def test_wrappers_accept_legacy_user_shape_without_role_or_denied(
    monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    if managed:
        monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    else:
        monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    admin = _LegacyUser(is_admin=True, permissions=[])
    restricted = RESTRICTED_DEPLOYMENT_PERMISSIONS if managed else set()
    assert effective_permissions(admin) == sorted(ALL_PERMISSION_KEYS - restricted)
    member = _LegacyUser(is_admin=False, permissions=["users", "features"])
    assert effective_permissions(member) == ["features", "users"]
    assert user_has_permission(member, "features") is True
    assert user_has_permission(member, "providers") is False


def test_assert_can_grant_uses_the_effective_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Granting is bounded by ``role ∪ unit ∪ grant − deny``, not by the stored column."""
    monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    holder = _user(Role.USER, permissions=["users", "terminal"], denied=["terminal"])

    # Stored grant: held.
    assert_can_grant(holder, ["users"])
    # Department grant: held, and the reason the two grant surfaces share this
    # function.
    assert_can_grant(holder, ["providers"], unit_grants={"providers"})
    # An explicit deny outranks a stored grant on an unmanaged installation.
    with pytest.raises(OctopError) as exc:
        assert_can_grant(holder, ["terminal"])
    assert exc.value.code is ErrorCode.FORBIDDEN
    # Neither stored nor granted.
    with pytest.raises(OctopError) as exc:
        assert_can_grant(holder, ["users", "security"], unit_grants={"providers"})
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.details["missing"] == ["security"]
    # Caller context rides along with the missing keys.
    with pytest.raises(OctopError) as exc:
        assert_can_grant(holder, ["security"], details={"unit_key": "eng"})
    assert exc.value.details == {"missing": ["security"], "unit_key": "eng"}


@pytest.mark.parametrize(
    "key",
    ["skills", "subagents", "tools", "agent_plugins", "memory", "mbti"],
)
def test_personalization_permissions_are_independent_and_deny_wins(key: str) -> None:
    assert key in ALL_PERMISSION_KEYS
    if key != "mbti":
        assert key not in BASELINE_PERMISSIONS
    granted = _user(Role.USER, permissions=[key])
    assert user_has_permission(granted, key)
    denied = _user(Role.USER, permissions=[key], denied=[key])
    assert not user_has_permission(denied, key)


def test_assert_can_grant_admin_bypasses_for_allowed_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    admin = _user(Role.ADMIN, permissions=[], denied=["features"])
    assert_can_grant(admin, ["providers", "security", "terminal"])
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    with pytest.raises(OctopError) as exc:
        assert_can_grant(admin, ["terminal"])
    assert exc.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
