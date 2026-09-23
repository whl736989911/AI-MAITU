"""Unified resource access control — one ACL entry per shareable resource.

Replaces the three ad-hoc global booleans (``agents.is_shared``,
``connectors.shared``, ``knowledge_bases.shared``) with a single object plus
extra grants, and classifies how widely a change lands so the caller can decide
whether it needs approval.

An entry answers two questions, from the same row: :func:`can_access` — may this
user reach the resource at all — and :func:`can_write` — may they maintain it.
Both read the entry's ``permission`` level (``read`` | ``write``), so "share it"
and "share it read-only" are one table and one rule set.

Everything here is pure: the caller resolves the user's role and org scope and
passes them in, so the rules stay unit-testable and IO-free. The org scope is
the caller's *unit chain* — their unit and the units above it, as
``OrgUnitRepo.ancestor_keys`` builds it — which is what makes a grant to a
department reach its sub-departments; module grants inherit through the same
chain (``users.permissions.unit_permissions``), so both levels inherit by one
mechanism.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

RESOURCE_TYPES = ("agent", "connector", "knowledge_base", "feature", "knowledge_document")

VISIBILITY_PRIVATE = "private"
VISIBILITY_UNIT = "unit"
VISIBILITY_PUBLIC = "public"

#: What a matching entry lets the viewer do. ``read`` is the default and what
#: every row that predates the level carries — including the ``public`` row an
#: enterprise knowledge space is seeded with, so "everyone can reach it" means
#: "everyone can read it", never "everyone can write it".
PERMISSION_READ = "read"
PERMISSION_WRITE = "write"
PERMISSIONS = (PERMISSION_READ, PERMISSION_WRITE)

IMPACT_SELF = "self"
IMPACT_UNIT = "unit"
IMPACT_ORG = "org"

__all__ = [
    "IMPACT_ORG",
    "IMPACT_SELF",
    "IMPACT_UNIT",
    "PERMISSIONS",
    "PERMISSION_READ",
    "PERMISSION_WRITE",
    "RESOURCE_TYPES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_UNIT",
    "AclEntry",
    "allowed_resource_ids",
    "can_access",
    "can_write",
    "impact_scope",
    "requires_approval",
]


@dataclass(frozen=True)
class AclEntry:
    """One resource's access state.

    ``owner_user_id`` is ``None`` for system-owned rows (shared template agents
    with no ``user_id``): such an entry has no owner to match, so it stays
    admin-only unless its visibility or a grant opens it up.

    ``unit_key`` snapshots the owner's org unit at share time, so an owner who
    later moves to another unit does not silently re-scope what they shared.
    ``grants`` only ever widen access — there is no resource-level deny, and
    ``permission`` is what the whole entry lets a matching viewer *do*:
    ``read`` reaches the resource, ``write`` also maintains it (see
    :func:`can_write`). It trails the list it sits beside so the tuple's
    existing positional shape is unchanged.
    """

    resource_type: str
    resource_id: str
    owner_user_id: int | None
    visibility: str  # private | unit | public
    unit_key: str | None
    version: int
    grants: tuple[tuple[str, str], ...] = ()  # ((grantee_type, grantee_id), ...)
    permission: str = PERMISSION_READ  # read | write


def _grants(entry: AclEntry) -> set[tuple[str, str]]:
    """Grant pairs as a set, tolerating lists from JSON row mappers."""
    return {(str(kind), str(grantee)) for kind, grantee in entry.grants}


def impact_scope(before: AclEntry, after: AclEntry) -> str:
    """How widely ``after`` lands relative to ``before``: self | unit | org.

    Branches are ordered by priority: widening to ``public`` outranks
    everything, then ``private`` -> ``unit``, then newly added unit/role
    grants. Everything else — including narrowing — stays private to the actor.

    A public row that *gains* ``write`` is org-wide too: it hands the whole
    organization the right to maintain the resource, which is the reach the
    approval gate exists for. Reaching everyone to read was already approved
    when the row was published, so only the escalation waits.
    """
    if after.visibility == VISIBILITY_PUBLIC:
        if before.visibility != VISIBILITY_PUBLIC:
            return IMPACT_ORG
        if after.permission == PERMISSION_WRITE and before.permission != PERMISSION_WRITE:
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


def can_access(
    entry: AclEntry,
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
) -> bool:
    """Whether this user may use the resource. Rules apply in order.

    ``unit_keys`` is the caller's resolved org scope: their own unit and the
    units above it, nearest first (``()`` when they have none). Passing the
    chain rather than one key is what makes a unit scope inherit: an entry
    scoped to a department reaches the members of its sub-departments, and the
    reverse direction cannot match, because the chain holds a viewer's
    *ancestors* and never its descendants.
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
    if entry.visibility == VISIBILITY_UNIT and entry.unit_key in unit_keys:
        return True
    grants = _grants(entry)
    if ("user", str(user_id)) in grants:
        return True
    if any(("unit", unit) in grants for unit in unit_keys):
        return True
    return ("role", role) in grants


def can_write(
    entry: AclEntry,
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
) -> bool:
    """Whether this user may maintain the resource — add, change, remove content.

    ``write`` is the owner's and an administrator's, always, and otherwise only
    where the entry says so: a matching viewer of a ``write`` entry may
    maintain what they can reach, and a ``read`` entry keeps exactly the reach
    it had before the level existed — access, no maintenance. Every row that
    predates the level, the ``public`` enterprise row included, reads as
    ``read``.
    """
    if role == "admin" or user_id == entry.owner_user_id:
        return True
    return entry.permission == PERMISSION_WRITE and can_access(
        entry, user_id=user_id, role=role, unit_keys=unit_keys
    )


def allowed_resource_ids(
    entries: Iterable[AclEntry],
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
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
        if can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys)
    }
