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
    )
    server = SimpleNamespace(
        _started=True,
        services=SimpleNamespace(repos=SimpleNamespace(org_unit_repo=repo)),
    )
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(octop_server=server)),
    )


def test_unknown_key_raises_at_construction() -> None:
    with pytest.raises(RuntimeError):
        require_permission("not_a_real_key")


async def test_dependency_denies_without_permission() -> None:
    dep = require_permission("browser")
    with pytest.raises(OctopError) as exc:
        await dep(_request(), _user(False, []))
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.details.get("permission") == "browser"


async def test_dependency_allows_admin_and_holder() -> None:
    dep = require_permission("browser")
    assert await dep(_request(), _user(True)) is not None
    assert await dep(_request(), _user(False, ["browser"])) is not None


async def test_dependency_honors_org_unit_grants_and_denies() -> None:
    dep = require_permission("browser")
    request = _request({"eng": ["browser"]})
    # A direct call does not run FastAPI's DI, so the server must be passed in.
    server = request.app.state.octop_server
    assert await dep(request, _user(False, [], org_unit="eng"), server) is not None
    with pytest.raises(OctopError) as exc:
        await dep(request, _user(False, [], org_unit="eng", denied=["browser"]), server)
    assert exc.value.code is ErrorCode.FORBIDDEN
