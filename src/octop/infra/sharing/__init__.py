"""Unified resource access control — one ACL entry per shareable resource.

Replaces the three ad-hoc global booleans (``agents.is_shared``,
``connectors.shared``, ``knowledge_bases.shared``) with a single object plus
extra grants, and classifies how widely a change lands so the caller can decide
whether it needs approval.

Everything here is pure: the caller resolves the user's role and org unit and
passes them in, so the rules stay unit-testable and IO-free.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

RESOURCE_TYPES = ("agent", "connector", "knowledge_base", "feature")

VISIBILITY_PRIVATE = "private"
VISIBILITY_UNIT = "unit"
VISIBILITY_PUBLIC = "public"

IMPACT_SELF = "self"
IMPACT_UNIT = "unit"
IMPACT_ORG = "org"

__all__ = [
    "IMPACT_ORG",
    "IMPACT_SELF",
    "IMPACT_UNIT",
    "RESOURCE_TYPES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_UNIT",
    "AclEntry",
    "allowed_resource_ids",
    "can_access",
    "impact_scope",
    "requires_approval",
    "user_scope",
]


@dataclass(frozen=True)
class AclEntry:
    """One resource's access state.

    ``owner_user_id`` is ``None`` for system-owned rows (shared template agents
    with no ``user_id``): such an entry has no owner to match, so it stays
    admin-only unless its visibility or a grant opens it up.

    ``unit_key`` snapshots the owner's org unit at share time, so an owner who
    later moves to another unit does not silently re-scope what they shared.
    ``grants`` only ever widen access — there is no resource-level deny.
    """

    resource_type: str
    resource_id: str
    owner_user_id: int | None
    visibility: str  # private | unit | public
    unit_key: str | None
    version: int
    grants: tuple[tuple[str, str], ...] = ()  # ((grantee_type, grantee_id), ...)


def _grants(entry: AclEntry) -> set[tuple[str, str]]:
    """Grant pairs as a set, tolerating lists from JSON row mappers."""
    return {(str(kind), str(grantee)) for kind, grantee in entry.grants}


def impact_scope(before: AclEntry, after: AclEntry) -> str:
    """How widely ``after`` lands relative to ``before``: self | unit | org.

    Branches are ordered by priority: widening to ``public`` outranks
    everything, then ``private`` -> ``unit``, then newly added unit/role
    grants. Everything else — including narrowing — stays private to the actor.
    """
    if after.visibility == VISIBILITY_PUBLIC and before.visibility != VISIBILITY_PUBLIC:
        return IMPACT_ORG
    if after.visibility == VISIBILITY_UNIT and before.visibility == VISIBILITY_PRIVATE:
        return IMPACT_UNIT
    added = _grants(after) - _grants(before)
    if any(kind in ("unit", "role") for kind, _ in added):
        return IMPACT_UNIT
    return IMPACT_SELF


def requires_approval(before: AclEntry, after: AclEntry) -> bool:
    """Only widening to the whole organization needs an admin approval."""
    return impact_scope(before, after) == IMPACT_ORG


def user_scope(user: Any) -> tuple[str, str | None]:
    """``(role, unit_key)`` for :func:`can_access` from a user object or row.

    The API's ``User`` carries a ``Role`` enum while a persisted ``users`` row
    carries the plain string; both must resolve to the same scope or the rules
    would see two different callers and answer differently.
    """
    role = getattr(user, "role", "")
    return (str(getattr(role, "value", role) or ""), getattr(user, "org_unit", None) or None)


def can_access(
    entry: AclEntry,
    *,
    user_id: int,
    role: str,
    unit_key: str | None,
) -> bool:
    """Whether this user may use the resource. Rules apply in order.

    ``unit_key`` is the caller's resolved org unit (``None`` when unassigned).
    """
    if role == "admin":
        return True
    # System-owned rows have no owner: ``None`` never equals a user id, so this
    # rule cannot leak access to them.
    if user_id == entry.owner_user_id:
        return True
    if entry.visibility == VISIBILITY_PUBLIC:
        return True
    # Unit scope compares against the entry's snapshot. ``unit_key`` is NULL
    # when the unit was deleted (``ON DELETE SET NULL``); such an entry falls
    # back to owner-only rather than matching every unassigned user.
    if entry.visibility == VISIBILITY_UNIT and unit_key is not None and unit_key == entry.unit_key:
        return True
    grants = _grants(entry)
    if ("user", str(user_id)) in grants:
        return True
    if unit_key is not None and ("unit", unit_key) in grants:
        return True
    return ("role", role) in grants


def allowed_resource_ids(
    entries: Iterable[AclEntry],
    *,
    user_id: int,
    role: str,
    unit_key: str | None,
) -> set[str]:
    """Ids the actor may use, out of those resources' ACL entries.

    The list form of :func:`can_access`, and the runtime scope every list of a
    resource type resolves through: the connector list, and every
    knowledge-base list (composer defaults, cron mounts, permission checks)
    ask here instead of keeping their own implementation of the rules.

    The entries decide which resources are in play: the caller loads them per
    resource type (``ResourceAclRepo.list_for_type``) and resolves the actor's
    scope once, so there is no ``resource_type`` argument to keep in sync.

    An id with no entry is never returned: no ACL row means no access, not open
    access.
    """
    return {
        entry.resource_id
        for entry in entries
        if can_access(entry, user_id=user_id, role=role, unit_key=unit_key)
    }
