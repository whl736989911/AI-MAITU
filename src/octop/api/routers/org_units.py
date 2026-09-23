"""Org unit directory router.

Reading is part of the user-management module (``users``); unit *management* is
an administrator task, because a unit drives permission resolution for every
account scoped to it.

The write endpoints enforce the hierarchy invariants that make every unit query
terminate:

* a unit that still has children is never deleted (no cascade, no orphaned
  subtree),
* a unit that is still assigned to users is never deleted (``users.org_unit`` is
  a plain text column, so nothing else would catch the dangling scope),
* ``parent_key`` must name an existing unit,
* ``key`` is unique,
* a unit may never become its own ancestor — a cycle would hang every recursive
  walk over the tree, so it is refused at the only place that could create one.

Error codes stay inside the catalog (``ErrorCode`` / ``errors`` i18n / dashboard
``apiErrors`` are one three-way invariant, so no slice introduces a code on its
own). The delete refusals name the department, so they carry their own codes —
``ORG_UNIT_HAS_CHILDREN`` and ``ORG_UNIT_IN_USE`` — instead of reusing a
provider code whose message would call the unit a provider. The remaining
failure modes reuse the closest existing code and carry the precise cause in
``details``: the dashboard appends ``details.reason`` to the localized message.
``NOT_FOUND`` covers unknown units and parents, ``SLASH_BAD_ARGS`` — already the
repo's generic 400 "invalid arguments" code — covers the cycle refusal, and
``AGENT_ID_TAKEN`` the duplicate key.

``details`` keys must avoid ``key`` and ``locale``: ``to_envelope`` forwards them
into ``tr(key, locale, ...)``, so a ``details["key"]`` breaks localization with a
``TypeError`` instead of returning a response.

Department permission grants (``org_unit_permissions``) are the "unit" leg of
``role ∪ unit ∪ grant − deny``, so they live here rather than in the user
editor: one row per (unit, module key), and every member of the unit gains the
key through ``sharing``/``deps`` resolution — a grant reaches the department's
sub-departments too (design §2.1, see ``permissions.unit_permissions``).

Writing them needs the ``users`` module *and* a scope that covers the unit:
``admin`` for every unit, ``enterprise_admin`` for the units of its own
enterprise, ``unit_admin`` for its own department and its sub-departments — and
only with keys it holds itself (``permissions.assert_can_grant`` — the same rule
and the same function the user editor applies, resolved in the scope of *this*
unit). The same scope decides who may create, reparent and delete a unit
(design §4.2: 列表查询、详情查询、修改和删除操作都使用相同的范围校验); only creating
a *root* unit, or moving one to the root, stays a system administrator's move,
because that is what draws an enterprise boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.channel_runtime import sync_channel_runtime, unit_member_ids
from octop.api.deps import get_server, require_permission
from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.org_units import OrgUnitRepo, OrgUnitRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import (
    assert_can_grant,
    unit_permissions,
    validate_permission_keys,
)
from octop.infra.users.scope import OrgScope, assert_may_manage_unit, scope_for

router = APIRouter()

_KEY_MAX_LENGTH = 64
_LABEL_MAX_LENGTH = 100


class OrgUnitCreateBody(BaseModel):
    key: str = Field(min_length=1, max_length=_KEY_MAX_LENGTH, description="Stable unit key.")
    label_zh: str = Field(min_length=1, max_length=_LABEL_MAX_LENGTH)
    label_en: str = Field(min_length=1, max_length=_LABEL_MAX_LENGTH)
    parent_key: str | None = Field(default=None, description="Parent unit key, or null for a root.")
    sort_order: int = 0


class OrgUnitPatchBody(BaseModel):
    label_zh: str | None = Field(default=None, max_length=_LABEL_MAX_LENGTH)
    label_en: str | None = Field(default=None, max_length=_LABEL_MAX_LENGTH)
    parent_key: str | None = Field(
        default=None,
        description="Omit to keep the parent, send null to move the unit to the root.",
    )
    sort_order: int | None = None


class OrgUnitPermissionsBody(BaseModel):
    """The unit's whole grant set; ``PUT`` replaces, it does not merge."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[str] = Field(
        default_factory=list, description="Module keys every member of the unit gains."
    )


def _unit_payload(unit: OrgUnitRow) -> dict[str, Any]:
    """Single-unit body: the list shape plus the order the editor round-trips."""
    return {
        "key": unit.key,
        "label": {"zh": unit.label_zh, "en": unit.label_en},
        "parent_key": unit.parent_key,
        "sort_order": unit.sort_order,
    }


def _missing_unit(key: str) -> OctopError:
    return OctopError(
        ErrorCode.NOT_FOUND,
        f"org unit {key!r} not found",
        details={"unit_key": key, "reason": f"unknown org unit {key!r}"},
    )


def _assert_can_manage_grants(scope: OrgScope, unit_key: str) -> None:
    """Whoever administers *unit_key* may change its grants (design §2.1/§4.2).

    ``admin`` administers every unit, ``enterprise_admin`` every unit of its
    enterprise, ``unit_admin`` its own department and that department's
    sub-departments — the scope check is the whole point of the roles: an
    administrator that could edit a grant outside its branch would be a system
    administrator with a narrower UI, and one department could widen another's
    access.
    """
    assert_may_manage_unit(scope, unit_key, action="change this department's permissions")


def _assert_parent_exists(repo: OrgUnitRepo, parent_key: str) -> None:
    if repo.get(parent_key) is None:
        raise OctopError(
            ErrorCode.NOT_FOUND,
            f"parent org unit {parent_key!r} not found",
            details={"parent_key": parent_key, "reason": f"unknown parent unit {parent_key!r}"},
        )


def _assert_no_cycle(repo: OrgUnitRepo, *, key: str, new_parent: str) -> None:
    """Refuse a reparent that would make ``key`` its own ancestor.

    Walks up from the prospective parent. The visited set is not redundant with
    the router's own refusals: it also keeps the walk finite on a hierarchy that
    already holds a cycle from an older write, which is exactly the hang this
    guard exists to prevent.
    """
    seen: set[str] = set()
    current: str | None = new_parent
    while current is not None and current not in seen:
        if current == key:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                f"org unit {key!r} cannot be moved under {new_parent!r}: it is the unit or one of "
                f"its descendants",
                details={
                    "unit_key": key,
                    "parent_key": new_parent,
                    "reason": f"{new_parent!r} is {key!r} or a descendant of it",
                },
            )
        seen.add(current)
        ancestor = repo.get(current)
        current = ancestor.parent_key if ancestor is not None else None


@router.get("", summary="List organizational units")
async def list_org_units(
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return the units this operator administers as ``{key, label, parent_key}``.

    The filter is the same check the write endpoints run (design §4.2), so a
    scoped administrator sees its own branch: an enterprise administrator its
    whole enterprise, a department administrator its department and its
    sub-departments, a system administrator everything. ``parent_key`` of a
    branch root names a unit that is not in the list — the tree is then shown
    from the top of what the operator administers, which is the point.
    """
    scope = scope_for(actor, server.services.repos.org_unit_repo)
    units = server.services.repos.org_unit_repo.list_all()
    return {
        "units": [
            {
                "key": unit.key,
                "label": {"zh": unit.label_zh, "en": unit.label_en},
                "parent_key": unit.parent_key,
            }
            for unit in units
            if scope.covers_unit(unit.key)
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an organizational unit")
async def create_org_unit(
    body: OrgUnitCreateBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create a unit inside the operator's branch; the key must be free.

    A unit with no parent starts a new enterprise, which is a system
    administrator's move; anything else hangs off a parent the operator already
    administers, so nobody can graft a department onto another enterprise.
    """
    repo = server.services.repos.org_unit_repo
    scope = scope_for(actor, repo)
    if repo.get(body.key) is not None:
        raise OctopError(
            ErrorCode.AGENT_ID_TAKEN,
            f"org unit {body.key!r} already exists",
            details={"unit_key": body.key, "reason": f"org unit {body.key!r} already exists"},
        )
    if body.parent_key is None:
        if not scope.is_system_admin:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "admin required to create a root unit",
                details={
                    "reason": (
                        "a unit with no parent starts a new enterprise; create it under "
                        "one of your own departments instead"
                    )
                },
            )
    else:
        # A key that does not exist yet cannot appear in its own ancestor chain,
        # so a create only has to prove the parent is real and in scope.
        assert_may_manage_unit(scope, body.parent_key, action="create a unit there")
        _assert_parent_exists(repo, body.parent_key)
    unit = repo.create(
        key=body.key,
        label_zh=body.label_zh,
        label_en=body.label_en,
        parent_key=body.parent_key,
        sort_order=body.sort_order,
    )
    return _unit_payload(unit)


@router.patch("/{unit_key}", summary="Update an organizational unit")
async def patch_org_unit(
    unit_key: str,
    body: OrgUnitPatchBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Patch labels, parent, and order of an existing unit.

    ``parent_key`` is tri-state: omitted keeps the parent, ``null`` moves the
    unit to the root, a key reparents it. Labels are only written when sent with
    a value, since the columns are NOT NULL. Both ends of a reparent are checked:
    the operator must administer the unit *and* the department it moves under,
    so a unit can be neither taken over nor parked outside its branch.
    """
    repo = server.services.repos.org_unit_repo
    scope = scope_for(actor, repo)
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)
    assert_may_manage_unit(scope, unit_key, action="edit this unit")

    parent_patch: Any = UNSET
    if "parent_key" in body.model_fields_set:
        parent_patch = body.parent_key
        if parent_patch is None:
            if not scope.is_system_admin:
                raise OctopError(
                    ErrorCode.FORBIDDEN,
                    "admin required to move a unit to the root",
                    details={"unit_key": unit_key, "reason": "the root is the enterprise boundary"},
                )
        else:
            assert_may_manage_unit(scope, parent_patch, action="move a unit there")
            _assert_parent_exists(repo, parent_patch)
            _assert_no_cycle(repo, key=unit_key, new_parent=parent_patch)

    updated = repo.update(
        unit_key,
        label_zh=body.label_zh if body.label_zh is not None else UNSET,
        label_en=body.label_en if body.label_en is not None else UNSET,
        parent_key=parent_patch,
        sort_order=body.sort_order if body.sort_order is not None else UNSET,
    )
    assert updated is not None  # exists: checked above, and ``key`` is immutable
    return _unit_payload(updated)


@router.get("/{unit_key}/permissions", summary="List a department's permission grants")
async def get_org_unit_permissions(
    unit_key: str,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Module keys every member of ``unit_key`` gains (empty list = none).

    Reports the unit's *own* rows — what ``PUT`` writes — not the inherited
    union; the ancestors' keys are already visible on the ancestor itself.
    """
    repo = server.services.repos.org_unit_repo
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)
    assert_may_manage_unit(
        scope_for(actor, repo), unit_key, action="read this department's permissions"
    )
    return {"unit_key": unit_key, "permissions": repo.list_unit_permissions(unit_key)}


@router.put(
    "/{unit_key}/permissions",
    summary="Replace a department's permission grants",
)
async def set_org_unit_permissions(
    unit_key: str,
    body: OrgUnitPermissionsBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Replace the unit's grants; the body is the whole set, not a delta.

    A replace (not a merge) is what makes *revoking* a department's module
    possible at all: with a merge there would be no way to express "this
    department no longer gets ``browser``".
    """
    repo = server.services.repos.org_unit_repo
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)
    scope = scope_for(actor, repo)
    _assert_can_manage_grants(scope, unit_key)
    try:
        keys = validate_permission_keys(body.permissions)
    except ValueError as exc:
        # Same 400 shape the user editor uses for an unknown module key.
        raise OctopError(ErrorCode.FORBIDDEN, str(exc), status=400) from exc
    if not scope.is_system_admin:
        # Delegation, not self-escalation: an administrator may only hand out
        # keys it holds. Same rule and same function as the user editor
        # (``users._assert_can_assign``), resolved in the scope of *this*
        # department — its own grants and its ancestors' — because ``PUT``
        # replaces the whole set, so its admin must be able to re-submit the
        # grants the department already carries.
        assert_can_grant(
            actor,
            keys,
            unit_grants=unit_permissions(unit_key, repo),
            details={"unit_key": unit_key},
        )
    repo.set_grants(unit_key, keys)
    # A department grant is the "unit" leg of every member's effective set, so
    # this write can revoke a ``channel_<kind>`` for a whole subtree at once;
    # their channels stop now (design §2.3/§2.4).
    await sync_channel_runtime(server, user_ids=unit_member_ids(server, unit_key))
    return {"unit_key": unit_key, "permissions": keys}


@router.delete(
    "/{unit_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organizational unit",
)
async def delete_org_unit(
    unit_key: str,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Delete a leaf unit that no user is scoped to, inside the operator's branch.

    Children and assigned users are refused instead of being rewritten: a
    cascade would silently drop sub-units (and their permission grants), and
    detaching users would silently widen or narrow what they can reach.
    """
    repo = server.services.repos.org_unit_repo
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)
    assert_may_manage_unit(scope_for(actor, repo), unit_key, action="delete this unit")

    children = repo.list_children(unit_key)
    if children:
        raise OctopError(
            ErrorCode.ORG_UNIT_HAS_CHILDREN,
            f"org unit {unit_key!r} has {len(children)} child unit(s)",
            details={
                "children": children,
                "reason": f"org unit {unit_key!r} still has {len(children)} child unit(s)",
            },
        )

    members = repo.list_users_in_unit(unit_key)
    if members:
        raise OctopError(
            ErrorCode.ORG_UNIT_IN_USE,
            f"org unit {unit_key!r} is still assigned to {len(members)} user(s)",
            details={
                "users": members,
                "reason": f"org unit {unit_key!r} is still assigned to {len(members)} user(s)",
            },
        )

    repo.delete(unit_key)
