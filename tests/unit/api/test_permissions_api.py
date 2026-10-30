"""Unit tests for require_permission dependency."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.api.deps import require_permission
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _user(
    is_admin: bool,
    permissions: list[str] | None = None,
    *,
    org_unit: str | None = None,
    denied: list[str] | None = None,
) -> User:
    return User(
        id=1,
        username="u",
        role=Role.ADMIN if is_admin else Role.USER,
        display_name=None,
        permissions=permissions or [],
        org_unit=org_unit,
        denied_permissions=denied or [],
    )


def _request(grants: dict[str, list[str]] | None = None) -> SimpleNamespace:
    """Minimal request double — the dependency only reads ``state`` and ``app``."""
    mapping = grants or {}

    def _grants_for_units(keys: list[str]) -> set[str]:
        return {key for unit in keys for key in mapping.get(str(unit), [])}

    repo = SimpleNamespace(
        grants_for_units=_grants_for_units,
        list_unit_permissions=lambda unit: list(mapping.get(unit, [])),
        # A flat tree: every unit is its own root, so the ancestor chain a grant
        # lookup walks is just the unit itself.
        ancestor_keys=lambda unit: [unit] if unit in mapping else [],
    )
    server = SimpleNamespace(
        _started=True,
        services=SimpleNamespace(
            repos=SimpleNamespace(
                org_unit_repo=repo, user_repo=SimpleNamespace(units_for_user=lambda _uid: [])
            )
        ),
    )
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(octop_server=server)),
    )


def test_unknown_key_raises_at_construction() -> None:
    with pytest.raises(RuntimeError):
        require_permission("not_a_real_key")


async def test_dependency_denies_without_permission() -> None:
    dep = require_permission("users")
    request = _request()
    with pytest.raises(OctopError) as exc:
        await dep(request, _user(False, []), request.app.state.octop_server)
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.details.get("permission") == "users"


async def test_dependency_allows_admin_and_holder() -> None:
    dep = require_permission("users")
    request = _request()
    server = request.app.state.octop_server
    admin = _user(True)
    holder = _user(False, ["users"])
    assert await dep(request, admin, server) is admin
    assert await dep(request, holder, server) is holder


async def test_dependency_honors_org_unit_grants_and_denies() -> None:
    dep = require_permission("users")
    request = _request({"eng": ["users"]})
    # A direct call does not run FastAPI's DI, so the server must be passed in.
    server = request.app.state.octop_server
    member = _user(False, [], org_unit="eng")
    assert await dep(request, member, server) is member
    with pytest.raises(OctopError) as exc:
        await dep(request, _user(False, [], org_unit="eng", denied=["users"]), server)
    assert exc.value.code is ErrorCode.FORBIDDEN
