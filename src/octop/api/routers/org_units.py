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
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_admin, require_permission
from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.org_units import OrgUnitRepo, OrgUnitRow
from octop.infra.errors import ErrorCode, OctopError

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
    user: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return every org unit as ``{key, label: {zh, en}, parent_key}``."""
    units = server.services.repos.org_unit_repo.list_all()
    return {
        "units": [
            {
                "key": unit.key,
                "label": {"zh": unit.label_zh, "en": unit.label_en},
                "parent_key": unit.parent_key,
            }
            for unit in units
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an organizational unit")
async def create_org_unit(
    body: OrgUnitCreateBody,
    user: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create a unit; the key must be free and any parent must already exist."""
    repo = server.services.repos.org_unit_repo
    if repo.get(body.key) is not None:
        raise OctopError(
            ErrorCode.AGENT_ID_TAKEN,
            f"org unit {body.key!r} already exists",
            details={"unit_key": body.key, "reason": f"org unit {body.key!r} already exists"},
        )
    if body.parent_key is not None:
        # A key that does not exist yet cannot appear in its own ancestor chain,
        # so a create only has to prove the parent is real.
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
    user: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Patch labels, parent, and order of an existing unit.

    ``parent_key`` is tri-state: omitted keeps the parent, ``null`` moves the
    unit to the root, a key reparents it. Labels are only written when sent with
    a value, since the columns are NOT NULL.
    """
    repo = server.services.repos.org_unit_repo
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)

    parent_patch: Any = UNSET
    if "parent_key" in body.model_fields_set:
        parent_patch = body.parent_key
        if parent_patch is not None:
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


@router.delete(
    "/{unit_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organizational unit",
)
async def delete_org_unit(
    unit_key: str,
    user: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> None:
    """Delete a leaf unit that no user is scoped to.

    Children and assigned users are refused instead of being rewritten: a
    cascade would silently drop sub-units (and their permission grants), and
    detaching users would silently widen or narrow what they can reach.
    """
    repo = server.services.repos.org_unit_repo
    if repo.get(unit_key) is None:
        raise _missing_unit(unit_key)

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
